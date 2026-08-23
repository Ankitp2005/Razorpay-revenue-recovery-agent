"""
replay_batch.py
===============
Standalone script that reads all 65 subscriptions from subscriptions.db
and replays their payment_attempts as realistic webhook events to the
running FastAPI recovery agent.

Usage:
    # In terminal 1:
    uvicorn app.main:app --reload

    # In terminal 2:
    python replay_batch.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: requests library not found. Run: pip install requests")
    sys.exit(1)

BASE_URL = "http://localhost:8000"
DB_PATH = Path("data/subscriptions.db")
WEBHOOK_URL = f"{BASE_URL}/webhook/razorpay"
SUMMARY_URL = f"{BASE_URL}/summary"


def load_data() -> tuple[list[dict], dict[str, list[dict]]]:
    """Load subscriptions and their attempts from SQLite."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    subs = [dict(r) for r in conn.execute(
        "SELECT * FROM subscriptions ORDER BY subscription_id"
    ).fetchall()]

    all_attempts = conn.execute(
        "SELECT * FROM payment_attempts ORDER BY subscription_id, attempt_number"
    ).fetchall()

    attempts_by_sub: dict[str, list[dict]] = {}
    for row in all_attempts:
        d = dict(row)
        attempts_by_sub.setdefault(d["subscription_id"], []).append(d)

    conn.close()
    return subs, attempts_by_sub


def build_webhook(sub: dict, attempt: dict, event_type: str) -> dict:
    """
    Construct a WebhookPayload-shaped dict mimicking Razorpay real
    webhook envelope structure.
    """
    return {
        "event": event_type,
        "payload": {
            "subscription": {
                "entity": {
                    "subscription_id":         sub["subscription_id"],
                    "id":                      sub["subscription_id"],
                    "customer_id":             sub["customer_id"],
                    "customer_name":           sub["customer_name"],
                    "customer_phone":          sub["customer_phone"],
                    "plan_name":               sub["plan_name"],
                    "plan_amount_inr":         sub["plan_amount_inr"],
                    "billing_frequency":       sub["billing_frequency"],
                    "mandate_type":            sub["mandate_type"],
                    "subscription_start_date": sub["subscription_start_date"],
                    "total_count":             sub["total_count"],
                    "paid_count":              sub["paid_count"],
                    "current_status":          sub["current_status"],
                }
            },
            "payment": {
                "entity": {
                    "attempt_id":        attempt["attempt_id"],
                    "attempt_number":    attempt["attempt_number"],
                    "attempt_timestamp": attempt["attempt_timestamp"],
                    "amount_inr":        attempt["amount_inr"],
                    "status":            attempt["status"],
                    "error_code":        attempt["error_code"],
                    "error_description": attempt["error_description"],
                }
            },
        },
    }


def determine_event_type(sub: dict, attempt: dict, all_attempts: list[dict]) -> str:
    """
    Determine the correct Razorpay event type for this attempt:
      - subscription.halted -> only for the LAST attempt of a halted subscription
      - subscription.pending -> for all other attempts
    """
    is_halted = sub["current_status"] == "halted"
    is_last_attempt = attempt["attempt_number"] == max(
        a["attempt_number"] for a in all_attempts
    )
    if is_halted and is_last_attempt:
        return "subscription.halted"
    return "subscription.pending"


def main():
    # ---- Connectivity check ------------------------------------------------
    print(f"Connecting to FastAPI server at {BASE_URL} ...")
    try:
        resp = requests.get(f"{BASE_URL}/docs", timeout=3)
        resp.raise_for_status()
        print("Server is up. Starting replay...\n")
    except requests.exceptions.ConnectionError:
        print("\nERROR: Cannot connect to FastAPI server.")
        print("Please start the server first:\n")
        print("    uvicorn app.main:app --reload\n")
        print("Then re-run this script in a separate terminal.")
        sys.exit(1)
    except Exception as exc:
        print(f"WARNING: Unexpected connectivity check error: {exc}")
        print("Proceeding anyway...")

    # ---- Load data ---------------------------------------------------------
    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}.")
        print("Run generate_data.py first.")
        sys.exit(1)

    print("Clearing audit_log before replay...")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM audit_log;")
    conn.commit()
    conn.close()


    subs, attempts_by_sub = load_data()
    print(f"Loaded {len(subs)} subscriptions from {DB_PATH}")

    # ---- Replay ------------------------------------------------------------
    ok_count = 0
    err_count = 0

    for sub in subs:
        sid = sub["subscription_id"]
        attempts = attempts_by_sub.get(sid, [])

        for attempt in sorted(attempts, key=lambda a: a["attempt_number"]):
            event_type = determine_event_type(sub, attempt, attempts)
            webhook = build_webhook(sub, attempt, event_type)

            try:
                r = requests.post(WEBHOOK_URL, json=webhook, timeout=10)
                r.raise_for_status()
                result = r.json()
                ok_count += 1
                print(
                    f"  [{ok_count:03d}] {sid[:20]} | "
                    f"event={event_type.split('.')[1]:<8} | "
                    f"err={attempt['error_code']:<30} | "
                    f"action={result.get('action','?'):<25} | "
                    f"outcome={result.get('outcome','?')}"
                )
            except requests.exceptions.HTTPError as exc:
                err_count += 1
                print(f"  [ERR] {sid} attempt {attempt['attempt_number']}: HTTP {exc.response.status_code} - {exc.response.text[:200]}")
            except Exception as exc:
                err_count += 1
                print(f"  [ERR] {sid} attempt {attempt['attempt_number']}: {exc}")

    print(f"\nReplay complete: {ok_count} OK, {err_count} errors")

    # ---- Summary -----------------------------------------------------------
    print("\n" + "=" * 70)
    print("  FINAL /summary RESPONSE")
    print("=" * 70)
    try:
        summary = requests.get(SUMMARY_URL, timeout=10).json()
        print(json.dumps(summary, indent=2))

        rate = summary.get("recovery_rate_pct", 0)
        at_risk = summary.get("total_amount_at_risk_inr", 0)
        recovered = summary.get("total_amount_recovered_inr", 0)
        print(f"\n  Sanity check: recovery rate = {rate}%")
        if 0 < rate < 100:
            print("  OK: Recovery rate is in a believable range (not 0% or 100%)")
        else:
            print("  WARNING: Recovery rate looks suspicious.")
        print(f"  At risk: Rs.{at_risk:,}  |  Recovered: Rs.{recovered:,}")
    except Exception as exc:
        print(f"ERROR fetching summary: {exc}")


if __name__ == "__main__":
    main()
