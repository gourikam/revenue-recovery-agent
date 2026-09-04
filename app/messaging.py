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

# Internal root-cause codes (e.g. "GENERIC_DECLINE") are fine for logic and
# the audit trail, but reading them aloud via TTS spells them out letter by
# letter -- ugly for text too, unreadable for voice. Convert to natural
# language before it ever reaches a customer-facing message.
ROOT_CAUSE_HUMAN = {
    "INSUFFICIENT_FUNDS": "account mein balance kam tha",
    "CARD_EXPIRED": "aapka card expire ho chuka hai",
    "CARD_BLOCKED_FRAUD": "aapka card block hai",
    "AUTH_FAILURE": "verification complete nahi ho paya",
    "TRANSIENT_GATEWAY_ISSUE": "ek technical issue ki wajah se",
    "LIMIT_EXCEEDED": "transaction limit cross ho gaya",
    "GENERIC_DECLINE": "bank ne payment decline kar diya",
    "UNKNOWN": "ek technical issue ki wajah se",
}

ROOT_CAUSE_HUMAN_EN = {
    "INSUFFICIENT_FUNDS": "insufficient balance",
    "CARD_EXPIRED": "your card has expired",
    "CARD_BLOCKED_FRAUD": "your card is blocked",
    "AUTH_FAILURE": "verification could not be completed",
    "TRANSIENT_GATEWAY_ISSUE": "a temporary technical issue",
    "LIMIT_EXCEEDED": "your transaction limit was exceeded",
    "GENERIC_DECLINE": "your bank declined the payment",
    "UNKNOWN": "a technical issue",
}


def _humanize(reason: str, hinglish: bool = True) -> str:
    table = ROOT_CAUSE_HUMAN if hinglish else ROOT_CAUSE_HUMAN_EN
    return table.get(reason, reason.replace("_", " ").lower())


def _fake_payment_link(case_id: str) -> str:
    return f"https://rzp.io/l/test-{case_id[-8:]}"


def generate_hinglish_reminder(case: dict, reason: str, real_link: str = None) -> str:
    api_key = os.getenv("GROQ_API_KEY")
    link = real_link or _fake_payment_link(case["case_id"])
    human_reason = _humanize(reason, hinglish=True)
    if not api_key:
        return FALLBACK_HINGLISH_TEMPLATE.format(
            name=case["customer_name"].split()[0],
            amount=case["amount_inr"],
            reason=human_reason,
            link=link,
        )

    from groq import Groq
    client = Groq(api_key=api_key)
    prompt = f"""Write a short, warm SMS-style payment reminder in Hinglish (Roman script,
natural Hindi-English mix like a real Indian fintech app would send) for:
Customer: {case['customer_name'].split()[0]}
Amount: ₹{case['amount_inr']}
Reason payment failed: {human_reason}
Payment link to include: {link}

Keep it under 40 words, friendly, no pressure tactics, include the link as-is.
Do NOT use ALL_CAPS or underscore-style codes anywhere in the message -- write
naturally, as this text may also be read aloud by text-to-speech."""

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
            reason=human_reason,
            link=link,
        )


def generate_payment_link_message(case: dict, reason: str, real_link: str = None) -> str:
    link = real_link or _fake_payment_link(case["case_id"])
    return FALLBACK_PAYMENT_LINK_TEMPLATE.format(
        name=case["customer_name"].split()[0],
        amount=case["amount_inr"],
        reason=_humanize(reason, hinglish=False),
        link=link,
    )