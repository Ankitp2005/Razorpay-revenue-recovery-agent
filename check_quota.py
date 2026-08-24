"""
check_quota.py
==============
Diagnostic script — reads RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET from .env
and calls GET /v1/payment_links to report exactly how many payment links
already exist on this test-mode account.

Run from the project root:
    python check_quota.py

This script is READ-ONLY — it creates nothing.
"""
from __future__ import annotations

import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

KEY_ID     = os.getenv("RAZORPAY_KEY_ID", "")
KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")

PLACEHOLDER = "<placeholder>"

if not KEY_ID or KEY_ID == PLACEHOLDER or not KEY_SECRET or KEY_SECRET == PLACEHOLDER:
    print("ERROR: RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET not set or still <placeholder> in .env")
    sys.exit(1)

BASE_URL = "https://api.razorpay.com/v1/payment_links"
AUTH     = (KEY_ID, KEY_SECRET)

def fetch_all_links() -> list[dict]:
    """
    Page through GET /v1/payment_links (Razorpay returns max 100 per page)
    and collect every item until there are no more.
    """
    all_items: list[dict] = []
    params: dict = {"count": 100, "skip": 0}

    while True:
        resp = requests.get(BASE_URL, auth=AUTH, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        items = data.get("items", [])
        all_items.extend(items)

        # Razorpay pagination: if we got fewer items than we asked for, we're done
        if len(items) < params["count"]:
            break

        params["skip"] += params["count"]

    return all_items

def main() -> None:
    print("Querying Razorpay test-mode account for existing payment links …")
    print(f"  Key ID (masked): {KEY_ID[:8]}{'*' * (len(KEY_ID) - 8)}")
    print()

    try:
        links = fetch_all_links()
    except requests.exceptions.HTTPError as exc:
        print(f"HTTP error from Razorpay API: {exc.response.status_code}")
        try:
            print(exc.response.json())
        except Exception:
            print(exc.response.text[:300])
        sys.exit(1)
    except Exception as exc:
        print(f"Unexpected error: {exc}")
        sys.exit(1)

    total = len(links)

    # Break down by status for extra context
    status_counts: dict[str, int] = {}
    for lnk in links:
        status = lnk.get("status", "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1

    print(f"Total payment links on account : {total}")
    print(f"Razorpay test-mode hard cap    : 30")
    print(f"Remaining before cap           : {max(0, 30 - total)}")
    print()
    print("Breakdown by status:")
    for status, count in sorted(status_counts.items(), key=lambda x: -x[1]):
        print(f"  {status:<20}: {count}")
    print()

    if total >= 30:
        print("[!] Account is AT or OVER the 30-link cap -- all new link creation will 429.")
    elif total >= 25:
        print(f"[!] Only {30 - total} slot(s) left -- set REAL_LINK_QUOTA conservatively.")
    else:
        print(f"[OK] {30 - total} slot(s) available on this account.")

if __name__ == "__main__":
    main()
