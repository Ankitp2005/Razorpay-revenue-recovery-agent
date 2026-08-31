from __future__ import annotations


def _decide_action_internal(
    event_type: str,
    bucket: str,
    attempt_number: int,
    subscription: dict,
) -> dict:
    """
    Core decision logic: given WHY a payment failed (bucket), WHEN we
    are in the retry lifecycle (event_type + attempt_number), and WHO the
    subscriber is, return the single best action to take.

    Returned dict keys (always present, no optional fields):
      action    : what to do
      channel   : how to reach the customer ("none" if no outreach)
      reasoning : plain-English explanation for the audit trail

    Priority order of rules is documented inline.
    """
    sub_id = subscription.get("subscription_id", "unknown")

    # ------------------------------------------------------------------ #
    # HARD CAP: never exceed 3 recovery attempts, regardless of bucket.   #
    # This check runs FIRST so no rule below can bypass it.               #
    # ------------------------------------------------------------------ #
    if attempt_number > 3:
        return {
            "action": "stop",
            "channel": "none",
            "reasoning": (
                f"Hard cap reached: {attempt_number} recovery attempts have been "
                f"made for subscription {sub_id} (bucket: {bucket}). "
                "No further automated actions will be taken. This subscription "
                "is logged as an exception for manual review."
            ),
        }

    # ------------------------------------------------------------------ #
    # RISK BLOCK: escalate immediately, never auto-nudge, on ANY event.   #
    # ------------------------------------------------------------------ #
    if bucket == "risk_block":
        return {
            "action": "escalate_to_human",
            "channel": "none",
            "reasoning": (
                f"Subscription {sub_id} failed with a risk/fraud-flagged decline "
                f"(bucket: risk_block, event: {event_type}, attempt: {attempt_number}). "
                "Auto-retrying or nudging a risk-flagged decline could be interpreted "
                "as abuse by the issuing bank fraud systems. A human agent must "
                "review this case before any further action."
            ),
        }

    # ------------------------------------------------------------------ #
    # PENDING EVENT: still within Razorpay T+3 auto-retry window.         #
    # ------------------------------------------------------------------ #
    if event_type == "subscription.pending":
        if bucket == "card_action_needed":
            return {
                "action": "send_card_update_link",
                "channel": "sms",
                "reasoning": (
                    f"Subscription {sub_id} failed due to a card issue "
                    f"(bucket: card_action_needed, attempt: {attempt_number}). "
                    "Card expiry/block/enrollment errors cannot self-resolve -- "
                    "Razorpay auto-retry will keep failing for 3 days before "
                    "halting. We skip ahead and send the customer a card-update "
                    "link via SMS so they can fix the root cause immediately."
                ),
            }
        # All other pending buckets: let Razorpay built-in retry run.
        return {
            "action": "defer_to_razorpay",
            "channel": "none",
            "reasoning": (
                f"Subscription {sub_id} failed with bucket '{bucket}' "
                f"(event: {event_type}, attempt: {attempt_number}). "
                "This failure type is plausibly self-resolving (e.g. transient "
                "gateway error, temporary funds shortfall, auth timeout). "
                "We defer to Razorpay built-in T+3 auto-retry mechanism "
                "rather than adding noise with a premature customer-facing nudge."
            ),
        }

    # ------------------------------------------------------------------ #
    # HALTED EVENT: Razorpay auto-retry is exhausted.                     #
    # ------------------------------------------------------------------ #
    if event_type == "subscription.halted":
        if bucket == "customer_intent":
            # Give exactly one low-pressure nudge; stop after that.
            if attempt_number <= 1:
                return {
                    "action": "send_nudge",
                    "channel": "sms",
                    "reasoning": (
                        f"Subscription {sub_id} halted with a customer-intent "
                        f"signal (bucket: customer_intent, attempt: {attempt_number}). "
                        "The customer may have intentionally cancelled or set a "
                        "transaction limit. We send a single low-pressure SMS nudge "
                        "-- offering help, not pressure -- then stop. We will not "
                        "repeatedly contact a customer who has shown intent to opt out."
                    ),
                }
            else:
                return {
                    "action": "stop",
                    "channel": "none",
                    "reasoning": (
                        f"Subscription {sub_id} received a second recovery attempt "
                        f"after a customer-intent signal (bucket: customer_intent, "
                        f"attempt: {attempt_number}). Policy: send at most one nudge "
                        "for customer-intent failures to respect customer autonomy. "
                        "Stopping further outreach."
                    ),
                }

        # For all other halted buckets: escalate channel with each attempt.
        # Rationale: start cheap/efficient, escalate only on non-conversion,
        # cap at 3 total recovery attempts.
        if attempt_number == 1:
            channel = "whatsapp"
            channel_note = (
                "First recovery attempt: using WhatsApp as the primary channel -- "
                "higher open rate than SMS, lower cost than a voice call."
            )
        elif attempt_number == 2:
            channel = "voice_hinglish"
            channel_note = (
                "Second recovery attempt: escalating to a voice call in Hinglish -- "
                "the previous WhatsApp message did not convert."
            )
        else:  # attempt_number == 3
            channel = "sms"
            channel_note = (
                "Third and FINAL recovery attempt: falling back to SMS as a "
                "last-resort written reminder before we stop all outreach."
            )

        return {
            "action": "send_recovery_link",
            "channel": channel,
            "reasoning": (
                f"Subscription {sub_id} is halted with bucket '{bucket}' "
                f"(attempt: {attempt_number}). Razorpay auto-retry is exhausted; "
                "agent is taking over recovery. " + channel_note
            ),
        }

    # Fallback for any unexpected event_type -- be safe, never crash.
    return {
        "action": "escalate_to_human",
        "channel": "none",
        "reasoning": (
            f"Unrecognised event_type '{event_type}' for subscription {sub_id} "
            f"(bucket: {bucket}, attempt: {attempt_number}). "
            "Defaulting to human escalation to avoid taking an incorrect automated action."
        ),
    }


def decide_action(
    event_type: str,
    bucket: str,
    attempt_number: int,
    subscription: dict,
) -> dict:
    decision = _decide_action_internal(event_type, bucket, attempt_number, subscription)
    
    if decision["action"] in ("send_recovery_link", "send_nudge", "send_card_update_link"):
        contact_consent = subscription.get("contact_consent", True)
        if not contact_consent:
            sub_id = subscription.get("subscription_id", "unknown")
            decision["action"] = "escalate_to_human"
            decision["channel"] = "none"
            decision["reasoning"] += f" [CONSENT OVERRIDE: Subscription {sub_id} does not have contact consent. Automated outreach skipped; escalated to human.]"
            
    return decision
