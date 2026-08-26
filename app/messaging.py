"""
Generates the actual customer-facing recovery message.
Uses Groq (free tier) when available; falls back to templates so the whole
pipeline still runs with zero API keys configured.
"""
import os
from dotenv import load_dotenv

load_dotenv()

FALLBACK_HINGLISH_TEMPLATE = (
    "Hi {name}, aapka payment of ₹{amount} process nahi ho paya kyunki {reason}. "
    "Koi baat nahi, yahan click karke dobara try kar sakte hain: {link}. "
    "Koi dikkat ho toh reply karein, hum help karenge!"
)

FALLBACK_PAYMENT_LINK_TEMPLATE = (
    "Hi {name}, we noticed your recent payment of ₹{amount} didn't go through "
    "({reason}). Please use this secure link to complete it: {link}"
)


def _fake_payment_link(case_id: str) -> str:
    return f"https://rzp.io/l/test-{case_id[-8:]}"


def generate_hinglish_reminder(case: dict, reason: str) -> str:
    api_key = os.getenv("GROQ_API_KEY")
    link = _fake_payment_link(case["case_id"])
    if not api_key:
        return FALLBACK_HINGLISH_TEMPLATE.format(
            name=case["customer_name"].split()[0],
            amount=case["amount_inr"],
            reason=reason,
            link=link,
        )

    from groq import Groq
    client = Groq(api_key=api_key)
    prompt = f"""Write a short, warm SMS-style payment reminder in Hinglish (Roman script,
natural Hindi-English mix like a real Indian fintech app would send) for:
Customer: {case['customer_name'].split()[0]}
Amount: ₹{case['amount_inr']}
Reason payment failed: {reason}
Payment link to include: {link}

Keep it under 40 words, friendly, no pressure tactics, include the link as-is."""

    try:
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
            max_tokens=120,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return FALLBACK_HINGLISH_TEMPLATE.format(
            name=case["customer_name"].split()[0],
            amount=case["amount_inr"],
            reason=reason,
            link=link,
        )


def generate_payment_link_message(case: dict, reason: str) -> str:
    link = _fake_payment_link(case["case_id"])
    return FALLBACK_PAYMENT_LINK_TEMPLATE.format(
        name=case["customer_name"].split()[0],
        amount=case["amount_inr"],
        reason=reason,
        link=link,
    )
