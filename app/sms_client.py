import os
from dotenv import load_dotenv
from twilio.rest import Client

load_dotenv()

def send_recovery_sms(to_phone: str, message: str) -> dict:
    account_sid = os.getenv('TWILIO_ACCOUNT_SID')
    auth_token = os.getenv('TWILIO_AUTH_TOKEN')
    from_number = os.getenv('TWILIO_FROM_NUMBER')

    if not all([account_sid, auth_token, from_number]) or account_sid == '<placeholder>':
        return {"success": False, "error": "Twilio credentials not configured properly"}

    try:
        client = Client(account_sid, auth_token)
        msg = client.messages.create(
            body=message,
            from_=from_number,
            to=to_phone
        )
        return {"success": True, "message_sid": msg.sid}
    except Exception as e:
        return {"success": False, "error": str(e)}