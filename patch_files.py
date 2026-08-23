import re

# Patch app/db.py
with open('app/db.py', 'r', encoding='utf-8') as f:
    db_content = f.read()

new_summary = '''def get_summary_data() -> dict:
    """
    Aggregate stats for the /summary endpoint.
    Returns a dict with all required keys exactly as specified.
    """
    conn = get_connection()

    total_processed = conn.execute(
        "SELECT COUNT(DISTINCT subscription_id) FROM audit_log"
    ).fetchone()[0]

    total_at_risk = conn.execute(
        "SELECT COALESCE(SUM(plan_amount_inr), 0) FROM subscriptions"
    ).fetchone()[0]

    recovered_rows = conn.execute(
        """
        SELECT a.subscription_id, s.plan_amount_inr
        FROM audit_log a
        JOIN subscriptions s ON s.subscription_id = a.subscription_id
        WHERE a.outcome = 'recovered'
          AND a.id = (
              SELECT MAX(id) FROM audit_log
              WHERE subscription_id = a.subscription_id
                AND outcome = 'recovered'
          )
        GROUP BY a.subscription_id
        """
    ).fetchall()
    total_recovered = sum(r[1] for r in recovered_rows)

    recovery_rate = (
        round(total_recovered / total_at_risk * 100, 2) if total_at_risk else 0
    )

    buckets = conn.execute(
        """
        SELECT bucket,
               COUNT(*) AS total_events,
               SUM(CASE WHEN action = 'defer_to_razorpay' THEN 1 ELSE 0 END) AS deferred_count,
               SUM(CASE WHEN action IN ('send_recovery_link', 'send_nudge', 'send_card_update_link') THEN 1 ELSE 0 END) AS active_attempts,
               SUM(CASE WHEN outcome = 'recovered' THEN 1 ELSE 0 END) AS recovered_count
        FROM audit_log
        GROUP BY bucket
        """
    ).fetchall()

    breakdown_by_bucket = {}
    for row in buckets:
        bucket = row[0]
        total_events = row[1]
        deferred_count = row[2]
        active_attempts = row[3]
        recovered_count = row[4]

        active_rate = 0.0
        if active_attempts > 0:
            active_rate = round((recovered_count / active_attempts) * 100, 2)

        breakdown_by_bucket[bucket] = {
            "total_events": total_events,
            "deferred_count": deferred_count,
            "active_attempts": active_attempts,
            "recovered_count": recovered_count,
            "active_recovery_rate_pct": active_rate,
        }

    count_escalated = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action='escalate_to_human'"
    ).fetchone()[0]

    count_stopped = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action='stop'"
    ).fetchone()[0]

    conn.close()

    return {
        "total_subscriptions_processed":  total_processed,
        "total_amount_at_risk_inr":        total_at_risk,
        "total_amount_recovered_inr":      total_recovered,
        "recovery_rate_pct":               recovery_rate,
        "breakdown_by_bucket":             breakdown_by_bucket,
        "count_escalated_to_human":        count_escalated,
        "count_stopped_exceptions":        count_stopped,
    }
'''

db_content = re.sub(r'def get_summary_data\(\) -> dict:.*', new_summary, db_content, flags=re.DOTALL)
with open('app/db.py', 'w', encoding='utf-8') as f:
    f.write(db_content)

# Patch replay_batch.py
with open('replay_batch.py', 'r', encoding='utf-8') as f:
    replay_content = f.read()

clear_db_code = '''
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
'''
replay_content = replay_content.replace('''
    # ---- Load data ---------------------------------------------------------
    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}.")
        print("Run generate_data.py first.")
        sys.exit(1)''', clear_db_code)

with open('replay_batch.py', 'w', encoding='utf-8') as f:
    f.write(replay_content)

print("Patched app/db.py and replay_batch.py")
