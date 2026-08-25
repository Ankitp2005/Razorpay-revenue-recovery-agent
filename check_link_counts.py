import sqlite3

conn = sqlite3.connect("data/subscriptions.db")
c = conn.cursor()

real_links = c.execute(
    "SELECT COUNT(*) FROM audit_log WHERE razorpay_payment_link_id IS NOT NULL"
).fetchone()[0]

rate_limited = c.execute(
    "SELECT COUNT(*) FROM audit_log WHERE reasoning LIKE '%RATE_LIMIT_EXCEEDED%'"
).fetchone()[0]

print("Real links created:", real_links)
print("Rate limited:", rate_limited)

conn.close()