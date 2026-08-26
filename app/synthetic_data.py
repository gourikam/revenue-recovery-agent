"""
Generates a synthetic batch of failed Razorpay-style payment/subscription events.

Why synthetic: Razorpay test mode won't naturally give you 50-100 varied failure
reasons on demand. We simulate realistic failure codes (mirroring what Razorpay's
actual webhook payloads look like) so the agent can be evaluated on a proper batch,
not 1-2 cherry-picked demo cases -- this directly matches the buildathon's
"honest metrics on a batch" requirement.
"""
import random
import uuid
from datetime import datetime, timedelta

random.seed(42)  # reproducible batch for consistent demo numbers

FAILURE_CODES = [
    # (code, description, ease_of_recovery) - ease used only to make synthetic outcomes realistic
    ("BAD_REQUEST_ERROR", "Card declined by issuing bank", "medium"),
    ("GATEWAY_ERROR", "Insufficient funds in account", "medium"),
    ("GATEWAY_ERROR", "Card expired", "hard"),
    ("SERVER_ERROR", "Payment gateway timeout", "easy"),
    ("BAD_REQUEST_ERROR", "Incorrect CVV entered", "easy"),
    ("BAD_REQUEST_ERROR", "Card reported lost or stolen", "impossible"),
    ("GATEWAY_ERROR", "Bank server unreachable", "easy"),
    ("BAD_REQUEST_ERROR", "Transaction limit exceeded", "medium"),
    ("GATEWAY_ERROR", "3D Secure authentication failed", "medium"),
    ("BAD_REQUEST_ERROR", "Card blocked by issuer for suspected fraud", "impossible"),
]

INDIAN_FIRST_NAMES = ["Aarav", "Priya", "Rohan", "Ananya", "Vikram", "Sneha", "Karan",
                       "Ishita", "Aditya", "Meera", "Rahul", "Divya", "Arjun", "Pooja",
                       "Nikhil", "Kavya", "Siddharth", "Riya", "Aman", "Neha"]
INDIAN_LAST_NAMES = ["Sharma", "Verma", "Iyer", "Reddy", "Gupta", "Nair", "Joshi",
                      "Malhotra", "Kapoor", "Menon", "Chawla", "Bose"]


def _random_phone():
    return "+91" + str(random.randint(7000000000, 9999999999))


def generate_batch(n: int = 75) -> list[dict]:
    batch = []
    now = datetime.utcnow()
    for i in range(n):
        code, desc, ease = random.choice(FAILURE_CODES)
        name = f"{random.choice(INDIAN_FIRST_NAMES)} {random.choice(INDIAN_LAST_NAMES)}"
        amount = round(random.choice([299, 499, 999, 1499, 2499, 4999, 9999]) * random.uniform(0.9, 1.1), 2)
        created_at = now - timedelta(hours=random.randint(1, 240))

        batch.append({
            "case_id": f"case_{uuid.uuid4().hex[:10]}",
            "payment_id": f"pay_{uuid.uuid4().hex[:14]}",
            "subscription_id": f"sub_{uuid.uuid4().hex[:14]}" if random.random() > 0.3 else None,
            "customer_name": name,
            "customer_phone": _random_phone(),
            "amount_inr": amount,
            "raw_failure_code": code,
            "raw_failure_description": desc,
            "created_at": created_at.isoformat(),
            "_synthetic_ease": ease,  # ground-truth-ish hint, used only in eval, stripped before agent sees it
        })
    return batch


def save_batch(batch: list[dict], path: str = "data/failed_payments_batch.json"):
    import json
    from pathlib import Path
    full_path = Path(__file__).parent.parent / path
    with open(full_path, "w") as f:
        json.dump(batch, f, indent=2)
    return full_path


if __name__ == "__main__":
    b = generate_batch(75)
    path = save_batch(b)
    print(f"Generated {len(b)} synthetic failed-payment events -> {path}")
