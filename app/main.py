from __future__ import annotations

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException

from app.schemas import WebhookPayload
from app.classifier import classify_error
from app.decision_engine import decide_action
from app.recovery_simulator import simulate_recovery_outcome
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
      5. If action involves customer outreach, simulate the outcome.
      6. Write one audit_log row.
      7. Return the decision JSON.
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

    if action in ("send_recovery_link", "send_nudge", "send_card_update_link"):
        recovered = simulate_recovery_outcome(bucket)
        if recovered:
            outcome = "recovered"
            # Mark with a sentinel so no further attempts are made for this sub
            _recovery_attempt_counter[subscription_id] = 999
        else:
            outcome = "failed"
            _recovery_attempt_counter[subscription_id] = current_attempt

    elif action == "escalate_to_human":
        outcome = "escalated"
        # Human review is not an automated attempt; do not increment counter

    elif action == "defer_to_razorpay":
        outcome = "deferred"
        # Razorpay handles it; do not count as a recovery attempt

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
    )

    return {
        "subscription_id": subscription_id,
        "action": action,
        "channel": channel,
        "reasoning": reasoning,
        "outcome": outcome,
    }


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
