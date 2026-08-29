"""
generate_data.py
================
Synthetic "at-risk" subscription dataset generator for the Razorpay
failed-auto-debit recovery agent.

Every subscription in this batch has ALREADY experienced at least one
failed charge attempt.  Downstream steps (failure classifier + decision
engine) are built on top of this data, so field names / structure are
intentionally stable and must not be changed here.

NOTE ON ERROR-CODE DISTRIBUTION
---------------------------------
The weighted distribution used below for error_code is a reasonable
assumption based on typical subscription-failure patterns; it is NOT a
verified Razorpay statistic and should be treated as illustrative only.

Usage:
    python generate_data.py
"""

import random
import sqlite3
import string
import textwrap
from datetime import date, datetime, timedelta
from pathlib import Path

# -- Reproducibility -----------------------------------------------------------
SEED = 42
random.seed(SEED)

# -- Configuration -------------------------------------------------------------
DB_PATH = Path("data/subscriptions.db")
TOTAL_SUBSCRIPTIONS = 65
TODAY = date(2026, 8, 23)   # anchored to current local date

# -- Helpers -------------------------------------------------------------------

def rand_alphanum(n):
    chars = string.ascii_letters + string.digits
    return "".join(random.choices(chars, k=n))


def make_id(prefix):
    return prefix + rand_alphanum(14)


# -- Indian Names --------------------------------------------------------------
INDIAN_NAMES = [
    "Aarav Sharma", "Priya Verma", "Rohan Gupta", "Ananya Singh",
    "Vikram Yadav", "Pooja Mishra", "Rahul Tiwari", "Neha Srivastava",
    "Arjun Pandey", "Ritika Joshi", "Saurabh Agarwal", "Divya Rajput",
    "Karan Khanna", "Sneha Dubey", "Manish Chauhan", "Deepika Saxena",
    "Ajay Bhatia", "Kavya Mehta", "Rohit Kapoor", "Simran Malhotra",
    "Pankaj Rana", "Tanvi Chandra", "Gaurav Shukla", "Riya Tripathi",
    "Arjun Nair", "Lakshmi Iyer", "Karthik Menon", "Preethi Subramaniam",
    "Arun Krishnamurthy", "Meena Pillai", "Suresh Rajan", "Anjali Venkatesh",
    "Balaji Swaminathan", "Revathy Chandrasekar", "Murugan Palani", "Anitha Selvam",
    "Senthil Murugesan", "Kavitha Ramasamy", "Dinesh Govindaraj", "Saranya Natarajan",
    "Vijay Annamalai", "Deepa Shanmugam", "Prabhu Sundaram", "Lavanya Krishnan",
    "Souvik Banerjee", "Rishita Chakraborty", "Arnab Mukherjee", "Puja Bose",
    "Debashis Ghosh", "Tanushree Sen", "Kaushik Das", "Moumita Roy",
    "Subhrajit Dey", "Ankita Mondal", "Rajesh Panda", "Smita Misra",
    "Santosh Mahapatra", "Priyanka Sahu", "Biswajit Sahoo", "Susmita Nayak",
    "Pratik Desai", "Sonal Joshi", "Nikhil Patil", "Madhuri Kulkarni",
    "Sachin Pawar", "Supriya Shinde", "Amol Deshpande", "Vrushali Gaikwad",
    "Hardik Patel", "Bhavna Shah", "Dhruv Mehta", "Tejal Thakkar",
    "Nirav Modi", "Foram Trivedi", "Chirag Vora", "Riddhi Jain",
    "Manav Oswal", "Nisha Bafna",
]


def pick_name():
    return random.choice(INDIAN_NAMES)


def make_phone():
    first_digit = random.choice(["6", "7", "8", "9"])
    rest = "".join(random.choices(string.digits, k=9))
    return "+91" + first_digit + rest


# -- Plans ---------------------------------------------------------------------
PLANS = {
    "Fitness Pro Monthly": {
        "frequency": "monthly",
        "amount_range": (999, 2499),
    },
    "OTT Premium": {
        "frequency": "monthly",
        "amount_range": (199, 799),
    },
    "SaaS Team Plan": {
        "frequency": "monthly",
        "amount_range": (1499, 4999),
    },
    "Meal Subscription Weekly": {
        "frequency": "weekly",
        "amount_range": (999, 2999),
    },
    "Insurance Premium Monthly": {
        "frequency": "monthly",
        "amount_range": (2000, 8000),
    },
    "Cloud Storage Plus": {
        "frequency": "monthly",
        "amount_range": (149, 599),
    },
}

PLAN_NAMES = list(PLANS.keys())


def pick_plan():
    name = random.choice(PLAN_NAMES)
    cfg = PLANS[name]
    lo, hi = cfg["amount_range"]
    base = random.randint(lo, hi - 1)
    remainder = base % 100
    if remainder < 50:
        amount = (base // 100) * 100 + 49
    else:
        amount = (base // 100) * 100 + 99
    amount = max(lo, min(hi, amount))
    return name, cfg["frequency"], amount


# -- Mandate Types -------------------------------------------------------------
MANDATE_TYPES = ["UPI Autopay", "eNACH", "Card e-mandate"]
MANDATE_WEIGHTS = [0.50, 0.15, 0.35]


def pick_mandate():
    return random.choices(MANDATE_TYPES, weights=MANDATE_WEIGHTS, k=1)[0]


# -- Error Codes ---------------------------------------------------------------
# NOTE: Weights below are reasonable assumptions for typical subscription-
# failure patterns and are NOT verified Razorpay statistics.
ERROR_CODES = [
    "insufficient_funds",
    "card_expired",
    "debit_instrument_blocked",
    "card_not_enrolled",
    "authentication_failed",
    "gateway_technical_error",
    "bank_technical_error",
    "payment_cancelled",
    "transaction_limit_exceeded",
    "payment_risk_check_failed",
]

ERROR_WEIGHTS = [0.35, 0.15, 0.08, 0.05, 0.15, 0.10, 0.05, 0.04, 0.02, 0.01]

ERROR_DESCRIPTIONS = {
    "insufficient_funds":         "Transaction declined due to insufficient funds in the account.",
    "card_expired":               "The card used for this mandate has expired.",
    "debit_instrument_blocked":   "The debit instrument (UPI/card/bank account) has been blocked.",
    "card_not_enrolled":          "Card is not enrolled for 3D Secure / e-mandate verification.",
    "authentication_failed":      "Customer authentication failed or OTP was not completed.",
    "gateway_technical_error":    "Payment gateway encountered a technical error. No amount was debited.",
    "bank_technical_error":       "Issuing bank returned a technical error. Retry after some time.",
    "payment_cancelled":          "Payment was cancelled before completion.",
    "transaction_limit_exceeded": "Transaction amount exceeds the per-transaction or daily limit set on the account.",
    "payment_risk_check_failed":  "Payment blocked by risk/fraud detection system.",
}


def pick_error():
    code = random.choices(ERROR_CODES, weights=ERROR_WEIGHTS, k=1)[0]
    return code, ERROR_DESCRIPTIONS[code]


# -- Date helpers --------------------------------------------------------------

def random_start_date():
    days_back = random.randint(30, 365)
    d = TODAY - timedelta(days=days_back)
    return d.isoformat()


def make_attempt_timestamps(base_date_str, num_attempts):
    base = datetime.fromisoformat(base_date_str + "T09:00:00")
    base += timedelta(hours=random.randint(0, 12))
    timestamps = []
    for i in range(num_attempts):
        offset_hours = random.randint(-2, 2)
        ts = base + timedelta(hours=i * 24 + offset_hours)
        timestamps.append(ts.strftime("%Y-%m-%dT%H:%M:%S"))
    return timestamps


# -- Main generation -----------------------------------------------------------

def generate_subscriptions():
    n_halted = round(TOTAL_SUBSCRIPTIONS * 0.40)
    n_pending = TOTAL_SUBSCRIPTIONS - n_halted

    statuses = (["halted"] * n_halted) + (["pending"] * n_pending)
    random.shuffle(statuses)

    subscriptions = []
    payment_attempts = []

    for status in statuses:
        sub_id = make_id("sub_")
        cust_id = make_id("cust_")
        name = pick_name()
        phone = make_phone()
        plan_name, frequency, amount = pick_plan()
        mandate = pick_mandate()
        start_date = random_start_date()
        total_count = 12
        paid_count = random.randint(0, 8)

        # ASSUMPTION: Real-world consent-opt-out rate is ~10%. This is an illustrative assumption.
        contact_consent = random.random() < 0.90
        
        sub = {
            "subscription_id":         sub_id,
            "customer_id":             cust_id,
            "customer_name":           name,
            "customer_phone":          phone,
            "plan_name":               plan_name,
            "plan_amount_inr":         amount,
            "billing_frequency":       frequency,
            "mandate_type":            mandate,
            "subscription_start_date": start_date,
            "total_count":             total_count,
            "paid_count":              paid_count,
            "current_status":          status,
            "contact_consent":         contact_consent,
        }
        subscriptions.append(sub)

        if status == "halted":
            num_attempts = 3
        else:
            num_attempts = random.choice([1, 2])

        days_since_start = (TODAY - date.fromisoformat(start_date)).days
        first_fail_days_ago = random.randint(
            num_attempts,
            max(num_attempts, min(30, days_since_start))
        )
        first_fail_date = (TODAY - timedelta(days=first_fail_days_ago)).isoformat()
        timestamps = make_attempt_timestamps(first_fail_date, num_attempts)

        for attempt_num, ts in enumerate(timestamps, start=1):
            error_code, error_desc = pick_error()
            attempt = {
                "attempt_id":        make_id("pay_"),
                "subscription_id":   sub_id,
                "attempt_number":    attempt_num,
                "attempt_timestamp": ts,
                "amount_inr":        amount,
                "status":            "failed",
                "error_code":        error_code,
                "error_description": error_desc,
            }
            payment_attempts.append(attempt)

    return subscriptions, payment_attempts


# -- Database ------------------------------------------------------------------

def create_db(subscriptions, payment_attempts):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE subscriptions (
            subscription_id         TEXT PRIMARY KEY,
            customer_id             TEXT NOT NULL,
            customer_name           TEXT NOT NULL,
            customer_phone          TEXT NOT NULL,
            plan_name               TEXT NOT NULL,
            plan_amount_inr         INTEGER NOT NULL,
            billing_frequency       TEXT NOT NULL,
            mandate_type            TEXT NOT NULL,
            subscription_start_date TEXT NOT NULL,
            total_count             INTEGER,
            paid_count              INTEGER NOT NULL,
            current_status          TEXT NOT NULL,
            contact_consent         BOOLEAN NOT NULL
        );

        CREATE TABLE payment_attempts (
            attempt_id          TEXT PRIMARY KEY,
            subscription_id     TEXT NOT NULL,
            attempt_number      INTEGER NOT NULL,
            attempt_timestamp   TEXT NOT NULL,
            amount_inr          INTEGER NOT NULL,
            status              TEXT NOT NULL,
            error_code          TEXT NOT NULL,
            error_description   TEXT NOT NULL,
            FOREIGN KEY (subscription_id) REFERENCES subscriptions(subscription_id)
        );
    """)

    cur.executemany(
        """INSERT INTO subscriptions VALUES
           (:subscription_id, :customer_id, :customer_name, :customer_phone,
            :plan_name, :plan_amount_inr, :billing_frequency, :mandate_type,
            :subscription_start_date, :total_count, :paid_count, :current_status, :contact_consent)""",
        subscriptions,
    )

    cur.executemany(
        """INSERT INTO payment_attempts VALUES
           (:attempt_id, :subscription_id, :attempt_number, :attempt_timestamp,
            :amount_inr, :status, :error_code, :error_description)""",
        payment_attempts,
    )

    conn.commit()
    conn.close()


# -- Acceptance-criteria report ------------------------------------------------

def print_report(subscriptions, payment_attempts):
    print("=" * 70)
    print("  ACCEPTANCE CRITERIA REPORT -- Synthetic Subscription Dataset")
    print("=" * 70)

    print(f"\n[1] Total subscriptions generated : {len(subscriptions)}")

    pending = sum(1 for s in subscriptions if s["current_status"] == "pending")
    halted  = sum(1 for s in subscriptions if s["current_status"] == "halted")
    print(f"\n[2] Status breakdown")
    print(f"    pending : {pending:>3}  ({pending/len(subscriptions)*100:.1f}%)")
    print(f"    halted  : {halted:>3}  ({halted/len(subscriptions)*100:.1f}%)")

    from collections import Counter
    ec_counts = Counter(a["error_code"] for a in payment_attempts)
    total_attempts = len(payment_attempts)
    print(f"\n[3] Error-code breakdown across {total_attempts} payment_attempts")
    print(f"    {'Error Code':<35} {'Count':>6}   {'Actual %':>8}   {'Target %':>8}")
    print(f"    {'-'*35} {'-'*6}   {'-'*8}   {'-'*8}")
    targets = dict(zip(ERROR_CODES, ERROR_WEIGHTS))
    for code in ERROR_CODES:
        cnt  = ec_counts.get(code, 0)
        pct  = cnt / total_attempts * 100 if total_attempts else 0
        tpct = targets[code] * 100
        print(f"    {code:<35} {cnt:>6}   {pct:>7.1f}%   {tpct:>7.1f}%")

    total_at_risk = sum(s["plan_amount_inr"] for s in subscriptions)
    print(f"\n[4] Total revenue at risk : Rs.{total_at_risk:,}")

    print(f"\n[5] Sample records (3 subscriptions with their payment attempts)")
    att_map = {}
    for a in payment_attempts:
        att_map.setdefault(a["subscription_id"], []).append(a)

    for sub in subscriptions[:3]:
        sid = sub["subscription_id"]
        print(f"\n  -- Subscription --------------------------------------------------")
        for k, v in sub.items():
            print(f"    {k:<30} : {v}")
        print(f"  -- Attempts ------------------------------------------------------")
        for att in att_map.get(sid, []):
            print(f"    Attempt #{att['attempt_number']}  |  {att['attempt_timestamp']}")
            print(f"      attempt_id   : {att['attempt_id']}")
            print(f"      amount_inr   : Rs.{att['amount_inr']}")
            print(f"      status       : {att['status']}")
            print(f"      error_code   : {att['error_code']}")
            print(f"      description  : {att['error_description']}")

    print("\n" + "=" * 70)
    print(f"  Database written to : {DB_PATH.resolve()}")
    print("=" * 70)



def migrate_existing_db():
    if DB_PATH.exists():
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute("ALTER TABLE subscriptions ADD COLUMN contact_consent BOOLEAN DEFAULT 1")
            conn.commit()
            print("Migrated existing subscriptions table to add contact_consent.")
        except Exception as e:
            pass
        conn.close()

# -- Entry point ---------------------------------------------------------------

if __name__ == "__main__":
    subs, attempts = generate_subscriptions()
    create_db(subs, attempts)
    print_report(subs, attempts)
