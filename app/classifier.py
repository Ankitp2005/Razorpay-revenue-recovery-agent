"""
app/classifier.py
=================
Maps a Razorpay error_code to one of six failure buckets.
Defensive: unknown codes return "unknown" without crashing.
"""

# Exact mapping — do not add codes or buckets here.
# Bucket taxonomy:
#   funds_related      → account balance issue, may self-resolve at month end
#   card_action_needed → card is expired / blocked / not enrolled; auto-retry pointless
#   auth_failure       → OTP / 3DS step didn't complete; often a timing issue
#   transient          → gateway / bank-side technical hiccup; retry usually works
#   customer_intent    → customer cancelled or has limits set intentionally
#   risk_block         → fraud-system intervention; escalate, never auto-nudge

_BUCKET_MAP: dict[str, str] = {
    "insufficient_funds":         "funds_related",
    "card_expired":               "card_action_needed",
    "debit_instrument_blocked":   "card_action_needed",
    "card_not_enrolled":          "card_action_needed",
    "authentication_failed":      "auth_failure",
    "gateway_technical_error":    "transient",
    "bank_technical_error":       "transient",
    "payment_cancelled":          "customer_intent",
    "transaction_limit_exceeded": "customer_intent",
    "payment_risk_check_failed":  "risk_block",
}


def classify_error(error_code: str) -> str:
    """
    Returns the failure bucket for a given Razorpay error_code.
    Falls back to "unknown" for any unrecognised code — never raises.
    """
    return _BUCKET_MAP.get(error_code, "unknown")
