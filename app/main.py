from __future__ import annotations

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException

from app.schemas import WebhookPayload
from app.classifier import classify_error
from app.decision_engine import decide_action
from app.recovery_simulator import simulate_recovery_outcome
from app.razorpay_client import create_recovery_payment_link
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

# ---------------------------------------------------------------------------
# Real Payment Link quota guard (Package 5).
#
# Razorpay test mode hard-caps total Payment Link creation at 30 per
# business account.  Our batch can generate ~43 active-attempt events per
# run, so without a cap we reliably hit 429 RATE_LIMIT_EXCEEDED on the
# tail requests.
#
# REAL_LINK_QUOTA  – maximum real Razorpay API calls per server lifetime.
#                    Configurable via env var; defaults to 25 so there is a
#                    5-link safety buffer under the 30-link hard cap.
# _real_link_count – how many real links this process has successfully
#                    created.  Incremented only on API success.
#                    Resets to 0 on server restart.
# ---------------------------------------------------------------------------
REAL_LINK_QUOTA: int = int(os.getenv("REAL_LINK_QUOTA", "25"))
_real_link_count: int = 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise DB on startup and compute effective quota."""
    db.init_audit_table()
    
    global REAL_LINK_QUOTA
    try:
        from app.razorpay_client import get_existing_link_count
        actual_count = get_existing_link_count()
        REAL_LINK_QUOTA = max(0, min(25, 30 - actual_count))
        print(f"\nReal link quota this session: {REAL_LINK_QUOTA} ({actual_count} already used on account)\n")
    except Exception as exc:
        REAL_LINK_QUOTA = 0
        print(f"\nWARNING: Failed to fetch existing payment links from Razorpay API: {exc}")
        print("Falling back to safe default of 0 real links for this session.\n")
        
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
def handle_webhook(payload: WebhookPayload):
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
    try:
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

    subscription = {
        "subscription_id": subscription_id,
        "current_status": sub_entity.get("current_status", "unknown"),
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
        # Step 5b — real Razorpay Payment Link API call, quota-guarded.   #
        #                                                                  #
        # Primary defence against Razorpay test-mode's 30-link hard cap:  #
        # stop calling the real API once REAL_LINK_QUOTA successes have    #
        # been recorded this server process lifetime.  The existing 429    #
        # handler below is kept as a secondary safety net.                 #
        # ---------------------------------------------------------------- #
        global _real_link_count

        if _real_link_count < REAL_LINK_QUOTA:
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
                _real_link_count += 1          # count only confirmed successes
            else:
                # Real API call failed — note it, but keep going.
                # (Secondary safety net; primary is the quota guard above.)
                reasoning = reasoning + f" [LINK CREATION FAILED: {link_result['error']}]"
        else:
            # Quota exhausted — skip the real API call entirely.
            reasoning = reasoning + (
                f" [Real payment link creation skipped: this run's demo-safe"
                f" quota of {REAL_LINK_QUOTA} real Razorpay test links has been"
                f" reached; remaining attempts use simulated outcomes only.]"
            )

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
