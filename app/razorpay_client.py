"""
app/razorpay_client.py
======================
Makes real Razorpay Payment Link API calls in test mode.

Security contract (enforced at module import time):
Credentials loaded ONLY from .env via python-dotenv.
Keys are NEVER hardcoded, logged, returned in responses, or stored in audit_log.
If either key is missing/empty the module raises RuntimeError so the server
fails loudly at startup, not silently at call time.
"""
from __future__ import annotations

import os
import requests
from dotenv import load_dotenv

load_dotenv()

_KEY_ID: str = os.getenv("RAZORPAY_KEY_ID", "")
_KEY_SECRET: str = os.getenv("RAZORPAY_KEY_SECRET", "")

_PLACEHOLDER = "<placeholder>"

if (not _KEY_ID or _KEY_ID == _PLACEHOLDER
        or not _KEY_SECRET or _KEY_SECRET == _PLACEHOLDER):
    raise RuntimeError(
        "\n\n"
        "  *** RAZORPAY CREDENTIALS NOT CONFIGURED ***\n"
        "  RAZORPAY_KEY_ID and/or RAZORPAY_KEY_SECRET are missing or still\n"
        "  set to <placeholder> in your .env file.\n\n"
        "  Action required:\n"
        "    1. Open .env at the project root.\n"
        "    2. Replace <placeholder> with your real Razorpay TEST-mode keys.\n"
        "       (Dashboard -> Settings -> API Keys -> Generate Test Key)\n"
        "    3. Restart the server.\n"
    )

_PAYMENT_LINKS_URL = "https://api.razorpay.com/v1/payment_links"
_TIMEOUT_SECONDS = 5


def create_recovery_payment_link(
    customer_name: str,
    customer_phone: str,
    amount_inr: int,
    subscription_id: str,
    description: str,
) -> dict:
    """
    Create a real Razorpay Payment Link via the v1 API.

    Returns on success:
        {"success": True, "payment_link_id": str, "short_url": str}

    Returns on any failure (network, timeout, non-200, bad JSON):
        {"success": False, "error": str}

    This function NEVER raises -- callers always get a dict back, so
    a failed API call cannot crash the webhook handler.
    """
    payload = {
        "amount": amount_inr * 100,
        "currency": "INR",
        "description": description,
        "customer": {
            "name": customer_name,
            "contact": customer_phone,
        },
        "notify": {
            "sms": False,
            "email": False,
        },
        "reference_id": subscription_id,
    }

    try:
        response = requests.post(
            _PAYMENT_LINKS_URL,
            json=payload,
            auth=(_KEY_ID, _KEY_SECRET),
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        return {
            "success": True,
            "payment_link_id": data.get("id", ""),
            "short_url": data.get("short_url", ""),
        }

    except requests.exceptions.Timeout:
        return {
            "success": False,
            "error": f"Razorpay API timed out after {_TIMEOUT_SECONDS}s",
        }
    except requests.exceptions.HTTPError as exc:
        try:
            detail = exc.response.json()
        except Exception:
            detail = exc.response.text[:300]
        return {
            "success": False,
            "error": f"HTTP {exc.response.status_code}: {detail}",
        }
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
        }
