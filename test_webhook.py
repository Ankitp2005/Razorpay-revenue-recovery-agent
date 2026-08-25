import requests
import json
payload = {"event": "subscription.halted", "payload": {"subscription": {"entity": {"id": "sub_test_new_fake_id_1", "current_status": "halted"}}, "payment": {"entity": {"amount_inr": 500, "error_code": "insufficient_funds"}}}}
r = requests.post("http://127.0.0.1:8000/webhook/razorpay", json=payload)
print('Status Code:', r.status_code)
print('Raw Body:', r.text)
