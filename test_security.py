import hmac
import hashlib
import json
import requests
import os
import time

# Set up test secret
os.environ["RAZORPAY_WEBHOOK_SECRET"] = "my_test_secret"

payload_dict = {
    "event": "subscription.halted",
    "payload": {
        "subscription": {
            "entity": {
                "id": "sub_test_security_1",
                "current_status": "halted",
                "customer_contact": "+919876543210"
            }
        },
        "payment": {
            "entity": {
                "amount_inr": 500,
                "error_code": "insufficient_funds"
            }
        }
    }
}
raw_body = json.dumps(payload_dict, separators=(',', ':')).encode('utf-8')
secret = "my_test_secret".encode('utf-8')
valid_signature = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()

headers = {
    "X-Razorpay-Signature": valid_signature,
    "X-Razorpay-Event-Id": "evt_test_abc123",
    "Content-Type": "application/json"
}

# 1. Test Valid Signature
print("--- 1. Testing Valid Signature ---")
r = requests.post("http://127.0.0.1:8000/webhook/razorpay", data=raw_body, headers=headers)
print("Status:", r.status_code)
print("Response:", r.text)

# 2. Test Invalid Signature (flip a character)
print("\n--- 2. Testing Invalid Signature ---")
invalid_signature = valid_signature[:-1] + ('a' if valid_signature[-1] != 'a' else 'b')
headers_invalid = headers.copy()
headers_invalid["X-Razorpay-Signature"] = invalid_signature
headers_invalid["X-Razorpay-Event-Id"] = "evt_test_def456" # new event id so it doesn't get blocked by idempotency
r = requests.post("http://127.0.0.1:8000/webhook/razorpay", data=raw_body, headers=headers_invalid)
print("Status:", r.status_code)
print("Response:", r.text)

# 3. Test Idempotency (resend the first valid one)
print("\n--- 3. Testing Idempotency (Duplicate Event) ---")
r = requests.post("http://127.0.0.1:8000/webhook/razorpay", data=raw_body, headers=headers)
print("Status:", r.status_code)
print("Response:", r.text)
