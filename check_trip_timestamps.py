import sqlite3

conn = sqlite3.connect("data/subscriptions.db")
c = conn.cursor()

rows = c.execute(
    "SELECT timestamp, subscription_id FROM audit_log "
    "WHERE reasoning LIKE '%disabled for remainder%' "
    "ORDER BY timestamp"
).fetchall()

print(len(rows), "trips")
for r in rows:
    print(r[0], r[1])

conn.close()
