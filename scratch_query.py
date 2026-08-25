import sqlite3
conn = sqlite3.connect('data/subscriptions.db')
conn.row_factory = sqlite3.Row
row = conn.execute("SELECT * FROM subscriptions WHERE current_status='halted' LIMIT 1").fetchone()
print(dict(row))
