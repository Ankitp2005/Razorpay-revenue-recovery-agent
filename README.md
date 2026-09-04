# Revenue Recovery Agent

An agent that diagnoses *why* a Razorpay subscription payment failed and
takes the right recovery action — instead of relying on Razorpay's
built-in retry, which treats every failure identically regardless of
cause.

Built for Razorpay Buildathon 2026 — **Track 3: AI Revenue Recovery**.

---

## The problem

When a subscription's recurring payment fails, Razorpay's own engine
auto-retries it once a day for three days on a fixed schedule, then
marks the subscription `halted` and stops. It doesn't matter whether the
card expired, the bank flagged it as risky, or the customer simply ran
short on funds that day — every case gets the identical treatment.
Recovering the revenue from that point on is entirely manual.

This agent sits in that gap: it classifies *why* a payment failed using
Razorpay's own documented decline-code taxonomy, decides the right
response for that specific failure type, and — where appropriate — takes
real action through Razorpay's live API.

## What makes this different from a retry bot

- **Explainable, not a black box.** Every decision is rule-based against
  Razorpay's own error codes, with a plain-English reason attached to
  every action in the audit log.
- **Genuinely bounded.** A hard cap of 3 recovery attempts per
  subscriber. Failures flagged by the bank as risk/fraud are **never**
  auto-contacted or auto-retried under any code path — they always
  escalate to a human, because automating outreach on a fraud-flagged
  decline can look like abuse to the issuing bank.
- **Really integrated, not simulated.** Recovery actions call
  Razorpay's live Payment Links API and create real, payable checkout
  links. A real, signed webhook from Razorpay's own production
  infrastructure was captured and processed during development — not a
  hand-built test payload.
- **Closes the loop.** When a customer actually pays a recovery link,
  Razorpay's own `payment_link.paid` webhook confirms it back, and the
  agent updates its record with the genuine payment ID — not a simulated
  guess.

## How it works

```
Razorpay webhook (subscription.pending / subscription.halted / payment.failed)
        │
        ▼
  ┌───────────┐   error_code mapped to one of 6 failure buckets using
  │ CLASSIFY  │   Razorpay's own documented decline-code taxonomy
  └───────────┘
        │
        ▼
  ┌───────────┐   priority-ordered rules: hard attempt cap → risk-block
  │  DECIDE   │   escalation → bucket-specific action (defer / recover /
  └───────────┘   nudge / escalate / stop)
        │
        ▼
  ┌───────────┐   real POST to Razorpay's Payment Links API when the
  │    ACT    │   decision calls for customer outreach; graceful
  └───────────┘   degradation (circuit breaker) if the account's
        │         test-mode link quota is exhausted
        ▼
  ┌───────────┐   payment_link.paid webhook flips the outcome from
  │  CONFIRM  │   simulated to a real, confirmed payment ID
  └───────────┘
        │
        ▼
  ┌───────────┐   every decision, reasoning, and outcome written to an
  │    LOG    │   audit trail, visible in the dashboard
  └───────────┘
```

### The six failure buckets

| Bucket | Razorpay decline codes | Typical response |
|---|---|---|
| `funds_related` | `insufficient_funds` | Defer to Razorpay's auto-retry; low-pressure follow-up if halted |
| `card_action_needed` | `card_expired`, `debit_instrument_blocked`, `card_not_enrolled` | Skip the doomed retry — send a card-update link immediately |
| `auth_failure` | `authentication_failed` | Defer initially; likely a clean retry will succeed |
| `transient` | `gateway_technical_error`, `bank_technical_error` | Defer — infrastructure hiccup, not the customer's fault |
| `customer_intent` | `payment_cancelled`, `transaction_limit_exceeded` | One low-pressure nudge, then stop — respects likely intentional opt-out |
| `risk_block` | `payment_risk_check_failed` | **Always** escalate to a human. Never auto-contacted, under any circumstance |

## Locked results (final verified batch)

| Metric | Value |
|---|---|
| Subscriptions processed | 65 |
| Total revenue at risk | ₹1,39,535 |
| Total recovered | ₹45,329 |
| Recovery rate | 32.49% |
| Escalated to human | 8 |
| Stopped (attempt cap reached) | 10 |

These numbers come from a deterministic, seeded synthetic dataset with
illustrative (not empirically observed) recovery probabilities per
failure bucket — clearly flagged as an assumption in the code, not a
claim about real-world performance.

## Tech stack

- **Backend:** Python, FastAPI
- **Database:** SQLite (hackathon scale — see [Limitations](#honest-limitations--path-to-production))
- **Dashboard:** Server-rendered HTML, Chart.js
- **Integrations:** Razorpay Payment Links API (live), Twilio (SMS, code-complete)
- **Security:** HMAC-SHA256 webhook signature verification, idempotent event processing

## Setup

```bash
git clone <repo-url>
cd razorpayBuildathon
pip install -r requirements.txt
```

Create a `.env` file:

```
RAZORPAY_KEY_ID=your_test_mode_key_id
RAZORPAY_KEY_SECRET=your_test_mode_key_secret
RAZORPAY_WEBHOOK_SECRET=your_webhook_secret
TWILIO_ACCOUNT_SID=your_twilio_sid
TWILIO_AUTH_TOKEN=your_twilio_auth_token
TWILIO_FROM_NUMBER=your_twilio_number
```

Generate the synthetic dataset:

```bash
python generate_data.py
```

Start the server:

```bash
uvicorn app.main:app --reload --port 8000
```

Replay the full batch against the live service:

```bash
python replay_batch.py
```

View the dashboard at `http://localhost:8000/dashboard`.

## API

| Endpoint | Method | Purpose |
|---|---|---|
| `/webhook/razorpay` | POST | Main entry point for subscription-lifecycle events |
| `/webhook/razorpay/payment-link-paid` | POST | Handles real payment confirmation, closes the loop |
| `/summary` | GET | Aggregate recovery metrics |
| `/audit/{subscription_id}` | GET | Full decision trail for one subscriber |
| `/audit/all` | GET | Full audit log |
| `/dashboard` | GET | Visual dashboard |

## Honest limitations & path to production

This is a hackathon-proven prototype, not a production deployment. Being
precise about the gap matters more than pretending it isn't there:

- **Infrastructure.** Single-process, SQLite-backed. Real Razorpay
  volume needs a concurrent database, a message queue between webhook
  receipt and decisioning, and horizontal scaling.
- **Multi-tenancy.** Built for one merchant account. A real product
  needs per-merchant isolation designed in from the start, not
  retrofitted.
- **Webhook signature verification.** Implemented and confirmed to
  correctly *reject* a tampered signature. Full round-trip acceptance
  testing against a live, validly-signed event was blocked by a
  dashboard access issue on the test account during development — a
  known, honestly-documented open item, not a silent gap.
- **Subscription-lifecycle webhooks weren't proven live.** The test
  account didn't have the Subscriptions product enabled, so real-webhook
  verification was done against `payment_link.paid` instead (still a
  genuine, signed, unprompted event from Razorpay's production
  infrastructure — just a different event family than the main decision
  loop is built around).
- **Real SMS delivery to Indian numbers is blocked by regulation, not
  code.** Twilio integration is complete and functional; actual delivery
  requires TRAI DLT registration (India's mandatory sender/template
  registration for commercial SMS since 2021) — a multi-day business
  verification process outside hackathon scope.
- **No real historical outcome data yet.** Recovery-probability
  assumptions are illustrative, not learned. A real pilot would replace
  them with measured rates.
- **No encryption at rest, no formal secrets manager, no data-retention
  policy.** Appropriate flags for a pre-production build, not oversights.

## What we actually hit building this

Real, load-bearing bugs were found and fixed during development, not
just theoretical edge cases:

- An in-memory attempt counter could, under specific conditions, have
  silently bypassed the fraud-escalation safety path. Found and fixed
  before it shipped, by testing the exact adversarial sequence rather
  than trusting a clean happy-path run.
- Razorpay's test-mode environment enforces a hard cap of 30 Payment
  Links per business, with no confirmed reset. Discovered mid-development
  when a full batch run hit it — the fix was a circuit breaker that
  detects the first rate-limit response and gracefully falls back to
  simulated outcomes for the remainder of the session, rather than
  retrying blindly into a wall.
- Razorpay's real webhook delivery was observed retrying the same event
  seven times after our endpoint briefly returned an error — direct,
  live confirmation of why idempotent event handling isn't optional.

## License

Built for Razorpay Buildathon 2026.