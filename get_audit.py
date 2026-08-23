import requests
import json

sub_id = 'sub_TP9gyv1plBArp5'
r = requests.get(f'http://localhost:8000/audit/{sub_id}')
trail = r.json()

print(f'=== AUDIT TRAIL: {sub_id} ===')
print(f'Total log entries: {len(trail)}')
for row in trail:
    print()
    print(f'  Step {row["id"]}  |  {row["timestamp"]}')
    print(f'    event_type     : {row["event_type"]}')
    print(f'    error_code     : {row["error_code"]}')
    print(f'    bucket         : {row["bucket"]}')
    print(f'    attempt_number : {row["attempt_number"]}')
    print(f'    action         : {row["action"]}')
    print(f'    channel        : {row["channel"]}')
    print(f'    outcome        : {row["outcome"]}')
    print(f'    amount_inr     : Rs.{row["amount_inr"]}')
    print(f'    reasoning      : {row["reasoning"]}')
print()
print('=== END AUDIT TRAIL ===')
