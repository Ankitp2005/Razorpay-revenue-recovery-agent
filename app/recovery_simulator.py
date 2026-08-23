from __future__ import annotations
import random

# Seed once at module import so every demo run is reproducible.
# NOTE: Recovery probabilities below are ASSUMED illustrative values
# and are NOT verified Razorpay statistics. Treat as demo-only parameters.
_rng = random.Random(42)

_SUCCESS_PROB: dict[str, float] = {
    "transient":          0.75,
    "auth_failure":       0.65,
    "funds_related":      0.45,
    "card_action_needed": 0.35,
    "customer_intent":    0.20,
    "risk_block":         0.15,
    "unknown":            0.10,
}


def simulate_recovery_outcome(bucket: str) -> bool:
    """
    Returns True if the recovery action is simulated as successful.
    Uses a module-level seeded RNG so results are reproducible across
    demo runs without needing to re-seed externally.
    """
    prob = _SUCCESS_PROB.get(bucket, _SUCCESS_PROB["unknown"])
    return _rng.random() < prob
