# Twilio SMS Integration — Notes and TRAI DLT Blocker

## What was implemented

**Package 9** wired up real SMS delivery via Twilio for the `sms` recovery channel.

When the decision engine returns `channel: "sms"`, the system now:

1. Calls `send_recovery_sms(to_phone, message)` in `app/sms_client.py`
2. Uses the Twilio REST API to dispatch an actual SMS to the subscriber's phone number
3. Records the Twilio `message_sid` (or error) in the `audit_log` row for that recovery event
4. Surfaces the SMS delivery result on the live dashboard

The integration is **fully functional** from a code perspective. The Twilio client initializes only when all three credentials are present in `.env`:

```
TWILIO_ACCOUNT_SID=<your_sid>
TWILIO_AUTH_TOKEN=<your_auth_token>
TWILIO_FROM_NUMBER=<your_twilio_number>
```

If any credential is missing or is still a `<placeholder>`, the function short-circuits and returns `{"success": False, "error": "Twilio credentials not configured properly"}` — no exception is raised, the rest of the recovery flow continues normally.

---

## Why SMS to India is blocked — TRAI DLT Registration

### The blocker

**TRAI (Telecom Regulatory Authority of India)** mandates that any entity sending commercial/transactional SMS to Indian mobile numbers must be registered on the **Distributed Ledger Technology (DLT) platform** operated by one of the licensed telecom operators (Jio, Airtel, Vi, BSNL, etc.).

Without DLT registration, the **telecom operator's MSC (Mobile Switching Centre) blocks the SMS at the carrier level** — the message is silently dropped before it reaches the handset, even though Twilio reports a successful `202 Accepted` API response.

### What DLT registration requires

| Requirement | Details |
|---|---|
| **Entity registration** | Business must register on a DLT portal (e.g., Jio DLT, Airtel DLT) with GST, PAN, and company documents |
| **Sender ID (Header)** | A 6-character alphanumeric sender ID (e.g., RZPAGNT) must be pre-approved by the operator |
| **Template registration** | Every SMS message body must be pre-registered as a template with placeholders. Deviation from the registered template = blocked |
| **Content scrubbing** | DLT platform scrubs each message against the registered template before forwarding to the operator |
| **Twilio India support** | Twilio supports DLT but requires the DLT Entity ID, Header ID, and Template ID to be passed as extra parameters in the API call |

### How it manifests in this project

When a Twilio account without DLT registration sends to an Indian number (+91xxxxxxxxxx), one of two things happens:

- **Trial account**: Error 21608 — "The number ... is unverified. Trial accounts can only send to verified numbers." (Twilio blocks before it even reaches carriers)
- **Paid account**: Twilio API returns HTTP 201 and a valid `message_sid`, but the carrier silently drops the message. No delivery receipt arrives.

The `send_recovery_sms()` function will log the Twilio `message_sid` as a success in the `audit_log`, but the actual SMS will not be received by the subscriber.

---

## How to unblock (production path)

### Option A — Full DLT registration (recommended for production)

1. Register your entity at Jio DLT (https://trueconnect.jio.com/) or Airtel DLT (https://dltconnect.airtel.in/) — both are free
2. Apply for a Sender ID (Header) — takes 1–3 working days
3. Register each message template with {#var#} placeholders
4. Update send_recovery_sms() in app/sms_client.py to pass the DLT metadata

Twilio docs for DLT: https://www.twilio.com/docs/sms/india-dlt-registration

### Option B — Test with a non-India number

Add a non-Indian number to your Twilio trial account's verified callers list (e.g., a US/UK number). The send_recovery_sms() function will deliver successfully to those numbers without DLT.

### Option C — WhatsApp via Twilio Sandbox (no DLT needed)

WhatsApp messages through Twilio's sandbox are exempt from TRAI DLT because they travel over WhatsApp's protocol, not the telecom SMS channel. If the whatsapp channel is implemented in a future package, it will work in India without DLT registration (during sandbox testing).

---

## Files changed in Package 9

| File | Change |
|---|---|
| app/sms_client.py | New — Twilio SMS helper with credential guard |
| app/main.py | Calls send_recovery_sms() when channel == "sms", stores sms_result in audit log |
| app/db.py | Extended audit_log schema to store sms_sid and sms_error columns |
| app/templates/dashboard.html | Surfaces SMS delivery status (SID or error) in the live dashboard table |
| requirements.txt | Added twilio dependency |
| .env | Added TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER placeholders (not committed — git-ignored) |

---

## Commit reference

```
9155f87 feat: real SMS delivery via Twilio for sms-channel recovery actions
```
