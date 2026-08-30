from __future__ import annotations

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from app.webhook_security import verify_razorpay_signature

from app.schemas import WebhookPayload
from app.classifier import classify_error
from app.decision_engine import decide_action
from app.recovery_simulator import simulate_recovery_outcome
from app.razorpay_client import create_recovery_payment_link
from app.sms_client import send_recovery_sms
from app import db
from app.dashboard import router as dashboard_router


# ---------------------------------------------------------------------------
# Attempt tracking is DB-backed (Package 5.2).
#
# current_attempt is derived at request-time by querying audit_log:
#   • If any row for this subscription has outcome = 'recovered', return 999
#     so the decision engine's attempt_number > 3 hard-cap fires and the
#     handler emits 'stop' (mirrors the prior in-memory recovered sentinel).
#   • Otherwise: COUNT(rows where subscription_id = ? AND outcome != 'recovered')
#     + 1 for the current in-flight event.
#
# This means server restarts and manual test webhooks can NEVER corrupt a
# batch run: the authoritative source of truth is always audit_log, which
# replay_batch.py wipes with "DELETE FROM audit_log" at the start of each run.
# ---------------------------------------------------------------------------

_real_links_disabled: bool = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise DB on startup."""
    db.init_audit_table()
    yield


app = FastAPI(
    title="Razorpay Recovery Agent",
    description=(
        "AI agent that recovers revenue from failed Razorpay subscription "
        "auto-debits. Classifies failures, decides recovery actions, and "
        "logs every decision for full auditability."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(dashboard_router)

@app.post("/webhook/razorpay")
async def handle_webhook(request: Request):
    """
    Main entry point for Razorpay webhook events.

    Flow:
      1. Extract fields from the Razorpay-shaped payload.
      2. Classify the error_code into a failure bucket.
      3. Determine which recovery attempt number this is.
      4. Decide the action via the decision engine.
      5. If action involves customer outreach:
         a. Look up customer_name, customer_phone, plan_name from DB.
         b. Real Payment Link API — quota-guarded (Package 5):
            - If _real_link_count < REAL_LINK_QUOTA: call real API,
              increment counter on success.
            - If quota reached: skip API call, append note to reasoning.
            - Existing 429 error handling is kept as a safety net.
         c. Simulate whether the synthetic customer "pays" (unchanged).
            Simulation always runs regardless of link quota status.
      6. Write one audit_log row (including link id/url if created).
      7. Return the decision JSON (including payment_link_url if created).
    """
    raw_body = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")
    event_id = request.headers.get("x-razorpay-event-id") or request.headers.get("request-id")
    
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")
    if secret and secret != "<placeholder>":
        secret = secret.strip()
        if not verify_razorpay_signature(raw_body, signature, secret):
            try:
                body_json = await request.json()
                sub_entity = body_json.get("payload", {}).get("subscription", {}).get("entity", {})
                subscription_id = sub_entity.get("subscription_id") or sub_entity.get("id") or "unknown"
                event_type = body_json.get("event", "unknown")
            except Exception:
                subscription_id = "unknown"
                event_type = "unknown"
            
            db.log_decision(
                subscription_id=subscription_id,
                event_type=event_type,
                error_code="unknown",
                bucket="unknown",
                attempt_number=0,
                action="none",
                channel="none",
                reasoning="Webhook signature verification failed.",
                outcome="signature_rejected",
                amount_inr=0,
                razorpay_event_id=event_id
            )
            raise HTTPException(status_code=401, detail="Invalid signature")
    else:
        print("Warning: RAZORPAY_WEBHOOK_SECRET is not set. Signature verification disabled.")

    if event_id:
        conn = db.get_connection()
        existing = conn.execute("SELECT 1 FROM audit_log WHERE razorpay_event_id = ?", (event_id,)).fetchone()
        conn.close()
        if existing:
            return {"status": "ignored", "reason": "duplicate event, already processed"}

    try:
        body_json = await request.json()
        payload = WebhookPayload(**body_json)
        sub_entity = payload.payload.subscription.entity
        pay_entity = payload.payload.payment.entity
        event_type = payload.event

        subscription_id = sub_entity.get("subscription_id") or sub_entity.get("id")
        amount_inr = pay_entity.get("amount_inr") or sub_entity.get("plan_amount_inr")
        error_code = pay_entity.get("error_code", "unknown")

        if not subscription_id:
            raise HTTPException(
                status_code=422,
                detail="Missing subscription_id in payload.subscription.entity",
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Malformed payload: {exc}")

    _sub_conn = db.get_connection()
    _sub_row = _sub_conn.execute(
        "SELECT contact_consent FROM subscriptions WHERE subscription_id = ?",
        (subscription_id,)
    ).fetchone()
    _sub_conn.close()
    
    subscription = {
        "subscription_id": subscription_id,
        "current_status": sub_entity.get("current_status", "unknown"),
        "contact_consent": bool(_sub_row["contact_consent"]) if _sub_row else True,
    }

    bucket = classify_error(error_code)

    # ------------------------------------------------------------------ #
    # DB-backed attempt number (Package 5.2 fix).                        #
    #                                                                     #
    # Read directly from audit_log so the value is always consistent     #
    # with what is persisted — immune to server restarts and manual       #
    # test webhooks that previously silently inflated the in-memory dict. #
    # ------------------------------------------------------------------ #
    _attempt_conn = db.get_connection()
    _recovered_sentinel = _attempt_conn.execute(
        "SELECT 1 FROM audit_log WHERE subscription_id = ? AND outcome = 'recovered' LIMIT 1",
        (subscription_id,),
    ).fetchone()
    if _recovered_sentinel:
        current_attempt = 999  # force 'stop' via the hard-cap rule
    else:
        _prior_count = _attempt_conn.execute(
            "SELECT COUNT(*) FROM audit_log "
            "WHERE subscription_id = ? AND outcome != 'recovered'",
            (subscription_id,),
        ).fetchone()[0]
        current_attempt = _prior_count + 1
    _attempt_conn.close()


    decision = decide_action(event_type, bucket, current_attempt, subscription)
    action = decision["action"]
    channel = decision["channel"]
    reasoning = decision["reasoning"]

    outcome: str | None = None
    razorpay_payment_link_id: str | None = None
    razorpay_short_url: str | None = None
    payment_link_url: str | None = None
    sms_sent_flag: bool | None = None
    sms_message_sid: str | None = None

    if action in ("send_recovery_link", "send_card_update_link", "send_nudge"):
        # ---------------------------------------------------------------- #
        # Step 5a — look up customer details from subscriptions table.     #
        # ---------------------------------------------------------------- #
        conn = db.get_connection()
        sub_row = conn.execute(
            "SELECT customer_name, customer_phone, plan_name, plan_amount_inr "
            "FROM subscriptions WHERE subscription_id = ?",
            (subscription_id,),
        ).fetchone()
        conn.close()

        if sub_row:
            customer_name = sub_row["customer_name"]
            customer_phone = sub_row["customer_phone"]
            plan_name = sub_row["plan_name"]
            link_amount = sub_row["plan_amount_inr"] or amount_inr or 0
        else:
            # Payload contains synthetic data — fall back gracefully
            customer_name = sub_entity.get("customer_name", "Subscriber")
            customer_phone = sub_entity.get("customer_contact", "+919999999999")
            plan_name = sub_entity.get("plan_name", "Subscription")
            link_amount = amount_inr or 0

        description = (
            f"Recovery payment for {plan_name} - attempt {current_attempt}"
        )

        # ---------------------------------------------------------------- #
        # Step 5b — real Razorpay Payment Link API call with circuit       #
        # breaker.                                                         #
        #                                                                  #
        # Primary defence against Razorpay test-mode's 30-link hard cap:   #
        # stop calling the real API once a RATE_LIMIT_EXCEEDED error is    #
        # hit.                                                             #
        # ---------------------------------------------------------------- #
        global _real_links_disabled

        if not _real_links_disabled:
            link_result = create_recovery_payment_link(
                customer_name=customer_name,
                customer_phone=customer_phone,
                amount_inr=link_amount,
                subscription_id=subscription_id,
                description=description,
            )

            if link_result["success"]:
                razorpay_payment_link_id = link_result["payment_link_id"]
                razorpay_short_url = link_result["short_url"]
                payment_link_url = razorpay_short_url
            else:
                if "RATE_LIMIT_EXCEEDED" in link_result["error"] or "429" in link_result["error"]:
                    _real_links_disabled = True
                    reasoning += " [Real link creation disabled for remainder of session: Razorpay test-mode cap reached.]"
                else:
                    reasoning += f" [LINK CREATION FAILED: {link_result['error']}]"
        else:
            reasoning += " [Real link creation disabled for remainder of session: Razorpay test-mode cap reached.]"

        # ---------------------------------------------------------------- #
        # Real SMS delivery via Twilio (Package 9)                         #
        # ---------------------------------------------------------------- #
        if channel == "sms":
            msg_text = f"Hi {customer_name}, your payment of Rs.{link_amount} for {plan_name} needs attention."
            if payment_link_url:
                msg_text += f" Complete it here: {payment_link_url}"
                
            sms_result = send_recovery_sms(customer_phone, msg_text)
            if sms_result["success"]:
                sms_sent_flag = True
                sms_message_sid = sms_result["message_sid"]
            else:
                sms_sent_flag = False
                reasoning += f" [SMS FAILED: {sms_result['error']}]"

        # ---------------------------------------------------------------- #
        # Step 5c — simulate synthetic "did customer pay" outcome.         #
        # Logic unchanged from Package 2.                                  #
        # ---------------------------------------------------------------- #
        recovered = simulate_recovery_outcome(bucket)
        if recovered:
            outcome = "recovered"
        else:
            outcome = "failed"

    elif action == "escalate_to_human":
        outcome = "escalated"

    elif action == "defer_to_razorpay":
        outcome = "deferred"

    elif action == "stop":
        outcome = "stopped"

    db.log_decision(
        subscription_id=subscription_id,
        event_type=event_type,
        error_code=error_code,
        bucket=bucket,
        attempt_number=current_attempt,
        action=action,
        channel=channel,
        reasoning=reasoning,
        outcome=outcome,
        amount_inr=amount_inr,
        razorpay_payment_link_id=razorpay_payment_link_id,
        razorpay_short_url=razorpay_short_url,
        razorpay_event_id=event_id,
        sms_sent=sms_sent_flag,
        sms_message_sid=sms_message_sid,
    )

    response = {
        "subscription_id": subscription_id,
        "action": action,
        "channel": channel,
        "reasoning": reasoning,
        "outcome": outcome,
    }
    if payment_link_url:
        response["payment_link_url"] = payment_link_url

    return response


@app.get("/audit/{subscription_id}")
def get_audit(subscription_id: str):
    """
    Returns the complete decision trail for a single subscription,
    ordered chronologically.
    """
    rows = db.get_audit_for_subscription(subscription_id)
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No audit log entries found for subscription '{subscription_id}'",
        )
    return rows


@app.get("/summary")
def get_summary():
    """
    Aggregate recovery stats -- powers the demo dashboard.
    Field names are contractually stable; downstream code depends on them.
    """
    return db.get_summary_data()

@app.post("/webhook/razorpay/payment-link-paid")
async def handle_payment_link_paid(request: Request):
    raw_body = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")
    event_id = request.headers.get("x-razorpay-event-id") or request.headers.get("request-id")
    
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")
    if secret and secret != "<placeholder>":
        secret = secret.strip()
        if not verify_razorpay_signature(raw_body, signature, secret):
            raise HTTPException(status_code=401, detail="Invalid signature")
    else:
        print("Warning: RAZORPAY_WEBHOOK_SECRET is not set. Signature verification disabled.")

    if event_id:
        conn = db.get_connection()
        existing = conn.execute("SELECT 1 FROM audit_log WHERE razorpay_event_id = ?", (event_id,)).fetchone()
        conn.close()
        if existing:
            return {"status": "ignored", "reason": "duplicate event, already processed"}

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed payload")
        
    event = body.get("event")
    
    if event != "payment_link.paid":
        return {"status": "ignored", "reason": "not payment_link.paid event"}
        
    try:
        reference_id = body["payload"]["payment_link"]["entity"].get("reference_id")
        real_payment_id = body["payload"]["payment"]["entity"].get("id")
        amount_paid = body["payload"]["payment_link"]["entity"].get("amount_paid")
    except (KeyError, TypeError) as exc:
        print(f"Warning: missing expected structure in payment_link.paid webhook: {exc}")
        return {"status": "ignored", "reason": "missing expected structure"}
        
    if not reference_id or "_" not in str(reference_id):
        print(f"Warning: missing or malformed reference_id: {reference_id}")
        return {"status": "ignored", "reason": "missing or malformed reference_id"}
        
    subscription_id = str(reference_id).rsplit("_", 1)[0]
    
    conn = db.get_connection()
    row = conn.execute(
        "SELECT * FROM audit_log WHERE subscription_id = ? ORDER BY id DESC LIMIT 1", 
        (subscription_id,)
    ).fetchone()
    
    if not row:
        print(f"Warning: no audit_log row found for subscription {subscription_id}")
        conn.close()
        return {"status": "ignored", "reason": f"no audit_log for {subscription_id}"}
        
    row_id = row["id"]
    old_reasoning = row["reasoning"]
    
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    new_reasoning = f"{old_reasoning} [CONFIRMED via real Razorpay payment pay_id={real_payment_id} at {ts} — this is a genuine payment confirmation, not a simulated outcome.]"
    
    conn.execute(
        """
        UPDATE audit_log
        SET outcome = 'recovered', 
            confirmed_real_payment_id = ?,
            reasoning = ?,
            razorpay_event_id = ?
        WHERE id = ?
        """,
        (real_payment_id, new_reasoning, event_id, row_id)
    )
    conn.commit()
    conn.close()
    
    return {
        "status": "updated",
        "subscription_id": subscription_id,
        "row_id": row_id,
        "outcome": "recovered",
        "confirmed_real_payment_id": real_payment_id
    }

