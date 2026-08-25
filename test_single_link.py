import requests
from app.razorpay_client import create_recovery_payment_link

original_post = requests.post
raw_response = None

def mock_post(*args, **kwargs):
    global raw_response
    raw_response = original_post(*args, **kwargs)
    return raw_response

requests.post = mock_post

result = create_recovery_payment_link(
    customer_name="Test User",
    customer_phone="+919999999999",
    amount_inr=500,
    subscription_id="sub_test_123",
    description="Test standalone script link"
)

print("Result from create_recovery_payment_link:")
print(result)

print("\n--- RAW HTTP RESPONSE BODY ---")
if raw_response is not None:
    print(f"Status Code: {raw_response.status_code}")
    print(raw_response.text)
else:
    print("No HTTP request was made.")
