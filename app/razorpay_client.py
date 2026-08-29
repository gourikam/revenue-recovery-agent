"""
Thin wrapper around the real Razorpay test-mode API.

Design decision: Razorpay (like all card networks, per RBI rules) does not let
you silently re-charge a customer's saved card without a pre-authorized
recurring mandate. So "retry" in this agent means: generate a real, live
Payment Link via the API and send it to the customer -- then poll whether
they actually paid it. This is the realistic version of "automated recovery,"
not a limitation we're hiding.

If no keys are configured, every function returns None and the caller falls
back to simulated behavior -- so the app still runs with zero setup.
"""
import os
import razorpay
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

_client = None


def get_client():
    global _client
    if _client is not None:
        return _client
    key_id = os.getenv("RAZORPAY_KEY_ID")
    key_secret = os.getenv("RAZORPAY_KEY_SECRET")
    if not key_id or not key_secret or "xxxx" in key_id:
        return None
    _client = razorpay.Client(auth=(key_id, key_secret))
    return _client


def create_payment_link(case: dict, description: str) -> dict | None:
    """
    Creates a REAL Razorpay test-mode payment link. Returns
    {id, short_url, status} or None if Razorpay isn't configured / call fails.
    """
    client = get_client()
    if client is None:
        return None

    try:
        link = client.payment_link.create({
            "amount": int(round(case["amount_inr"] * 100)),  # paise
            "currency": "INR",
            "accept_partial": False,
            "description": description[:255],
            "customer": {
                "name": case["customer_name"],
                "contact": case["customer_phone"],
            },
            "notify": {"sms": True, "email": False},
            "reminder_enable": True,
            "notes": {
                "case_id": case["case_id"],
                "source": "ai-revenue-recovery-agent",
            },
        })
        return {
            "id": link["id"],
            "short_url": link["short_url"],
            "status": link["status"],  # 'created', 'paid', 'cancelled', 'expired'
        }
    except Exception as e:
        print(f"[razorpay_client] create_payment_link failed: {e}")
        return None


def fetch_payment_link_status(link_id: str) -> str | None:
    """Returns 'created' | 'paid' | 'cancelled' | 'expired', or None on failure."""
    client = get_client()
    if client is None:
        return None
    try:
        link = client.payment_link.fetch(link_id)
        return link["status"]
    except Exception as e:
        print(f"[razorpay_client] fetch_payment_link_status failed: {e}")
        return None


def is_configured() -> bool:
    return get_client() is not None


# --- WEBHOOK HANDLING (real-time, replaces polling a synthetic batch) ---

def verify_webhook_signature(payload_body: bytes, signature: str) -> bool:
    """
    Verifies the X-Razorpay-Signature header against RAZORPAY_WEBHOOK_SECRET.
    This is mandatory before trusting ANY webhook payload -- otherwise anyone
    who finds your webhook URL could inject fake 'failed payment' events.
    """
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")
    if not secret:
        print("[webhook] REJECTED: RAZORPAY_WEBHOOK_SECRET is not set in environment")
        return False
    client = get_client()
    if client is None:
        print("[webhook] REJECTED: Razorpay client not configured -- check "
              "RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are set (not just the webhook secret)")
        return False
    try:
        client.utility.verify_webhook_signature(
            payload_body.decode("utf-8"), signature, secret
        )
        return True
    except Exception as e:
        print(f"[webhook] REJECTED: signature mismatch -- {e}. "
              f"Check RAZORPAY_WEBHOOK_SECRET matches EXACTLY what's shown in "
              f"Razorpay Dashboard > Settings > Webhooks for this webhook "
              f"(secret_len={len(secret)}).")
        return False


def parse_failed_payment_event(payload: dict) -> dict | None:
    """
    Turns a Razorpay 'payment.failed' webhook payload into the same shape
    process_case() expects from a synthetic batch row. Returns None if the
    event isn't a payment failure we care about.
    """
    event = payload.get("event")
    if event != "payment.failed":
        return None

    entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
    if not entity:
        return None

    payment_id = entity.get("id", "unknown")
    raw_created = entity.get("created_at")
    created_at = (
        datetime.utcfromtimestamp(raw_created).isoformat()
        if isinstance(raw_created, (int, float)) else datetime.utcnow().isoformat()
    )
    return {
        "case_id": f"case_{payment_id}",
        "payment_id": payment_id,
        "subscription_id": entity.get("subscription_id"),
        # Razorpay's payment.failed payload rarely includes the customer's name --
        # only contact/email -- so we fall back gracefully rather than guessing.
        "customer_name": entity.get("notes", {}).get("customer_name", "Customer"),
        "customer_phone": entity.get("contact", "unknown"),
        "amount_inr": round(entity.get("amount", 0) / 100, 2),  # paise -> INR
        "raw_failure_code": entity.get("error_code", "UNKNOWN"),
        "raw_failure_description": entity.get("error_description", "Unknown failure"),
        "created_at": created_at,
    }