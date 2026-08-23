from __future__ import annotations

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException

from app.schemas import WebhookPayload
from app.classifier import classify_error
from app.decision_engine import decide_action
from app.recovery_simulator import simulate_recovery_outcome
from app.razorpay_client import create_recovery_payment_link
from app import db


# ---------------------------------------------------------------------------
# In-memory attempt tracker so the decision engine knows which recovery
# attempt number we are on for each subscription.
# Key: subscription_id  Value: int (number of recovery actions taken so far)
# Reset on server restart -- fine for demo scale.
# ---------------------------------------------------------------------------
_recovery_attempt_counter: dict[str, int] = {}


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
         b. Call create_recovery_payment_link() — real Razorpay API.
         c. Simulate whether the synthetic customer "pays" (unchanged).
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

    current_attempt = _recovery_attempt_counter.get(subscription_id, 0) + 1

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
        # Step 5b — real Razorpay Payment Link API call.                   #
        # ---------------------------------------------------------------- #
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
            # Real API call failed — note it, but keep going
            reasoning = reasoning + f" [LINK CREATION FAILED: {link_result['error']}]"

        # ---------------------------------------------------------------- #
        # Step 5c — simulate synthetic "did customer pay" outcome.         #
        # Logic unchanged from Package 2.                                  #
        # ---------------------------------------------------------------- #
        recovered = simulate_recovery_outcome(bucket)
        if recovered:
            outcome = "recovered"
            _recovery_attempt_counter[subscription_id] = 999
        else:
            outcome = "failed"
            _recovery_attempt_counter[subscription_id] = current_attempt

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
