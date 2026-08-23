from __future__ import annotations
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path("data/subscriptions.db")


def get_connection() -> sqlite3.Connection:
    """Open and return a new SQLite connection with row_factory set."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_audit_table() -> None:
    """
    Creates the audit_log table in the existing subscriptions.db if it
    does not already exist. Called once at FastAPI startup.
    Using IF NOT EXISTS so the app is safely re-startable.
    """
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            subscription_id TEXT    NOT NULL,
            event_type      TEXT    NOT NULL,
            error_code      TEXT    NOT NULL,
            bucket          TEXT    NOT NULL,
            attempt_number  INTEGER NOT NULL,
            action          TEXT    NOT NULL,
            channel         TEXT    NOT NULL,
            reasoning       TEXT    NOT NULL,
            outcome         TEXT,
            amount_inr      INTEGER,
            timestamp       TEXT    NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def log_decision(
    subscription_id: str,
    event_type: str,
    error_code: str,
    bucket: str,
    attempt_number: int,
    action: str,
    channel: str,
    reasoning: str,
    outcome: str | None,
    amount_inr: int | None,
) -> None:
    """Insert one row into audit_log."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO audit_log
            (subscription_id, event_type, error_code, bucket, attempt_number,
             action, channel, reasoning, outcome, amount_inr, timestamp)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (subscription_id, event_type, error_code, bucket, attempt_number,
         action, channel, reasoning, outcome, amount_inr, ts),
    )
    conn.commit()
    conn.close()


def get_audit_for_subscription(subscription_id: str) -> list[dict]:
    """Return all audit_log rows for a subscription, ordered by timestamp."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM audit_log
        WHERE subscription_id = ?
        ORDER BY timestamp ASC
        """,
        (subscription_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_summary_data() -> dict:
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
