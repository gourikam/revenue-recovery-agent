"""
Diagnosis layer: turns a raw Razorpay-style failure code/description into
a normalized root-cause category the decision layer can act on.

Rule-based first (fast, free, explainable) with LLM fallback only for
descriptions that don't match a known pattern -- this keeps cost near zero
and keeps every classification explainable, which is what the buildathon
bar explicitly asks for ("every action explainable").
"""
import os
import re
from dotenv import load_dotenv

load_dotenv()

ROOT_CAUSES = {
    "INSUFFICIENT_FUNDS": ["insufficient", "no funds", "balance"],
    "CARD_EXPIRED": ["expired"],
    "CARD_BLOCKED_FRAUD": ["stolen", "lost", "blocked", "fraud"],
    "AUTH_FAILURE": ["cvv", "3d secure", "authentication"],
    "TRANSIENT_GATEWAY_ISSUE": ["timeout", "unreachable", "server error"],
    "LIMIT_EXCEEDED": ["limit exceeded"],
    "GENERIC_DECLINE": ["declined"],
}

# Which root causes are safe to auto-retry vs never retry
RECOVERABLE = {
    "INSUFFICIENT_FUNDS": True,
    "CARD_EXPIRED": False,       # needs new card, not a retry
    "CARD_BLOCKED_FRAUD": False,  # NEVER retry -- compliance/stopping rule
    "AUTH_FAILURE": True,
    "TRANSIENT_GATEWAY_ISSUE": True,
    "LIMIT_EXCEEDED": True,
    "GENERIC_DECLINE": True,
    "UNKNOWN": False,
}


def classify_rule_based(description: str) -> tuple[str, float]:
    desc = description.lower()
    for cause, keywords in ROOT_CAUSES.items():
        if any(kw in desc for kw in keywords):
            return cause, 0.95
    return "UNKNOWN", 0.0


def classify_with_llm(description: str) -> tuple[str, float]:
    """Fallback for descriptions the rules don't catch. Requires GROQ_API_KEY."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return "UNKNOWN", 0.0

    from groq import Groq
    client = Groq(api_key=api_key)
    categories = list(ROOT_CAUSES.keys())
    prompt = f"""Classify this payment failure reason into exactly ONE category from this list: {categories}

Failure reason: "{description}"

Respond with ONLY the category name, nothing else."""

    try:
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=20,
        )
        answer = resp.choices[0].message.content.strip().upper()
        answer = re.sub(r"[^A-Z_]", "", answer)
        if answer in ROOT_CAUSES:
            return answer, 0.75
    except Exception:
        pass
    return "UNKNOWN", 0.0


def diagnose(description: str) -> dict:
    cause, confidence = classify_rule_based(description)
    method = "rule"
    if cause == "UNKNOWN":
        llm_cause, llm_conf = classify_with_llm(description)
        if llm_cause != "UNKNOWN":
            cause, confidence, method = llm_cause, llm_conf, "llm"

    return {
        "root_cause": cause,
        "root_cause_confidence": confidence,
        "diagnosis_method": method,
        "is_recoverable": RECOVERABLE.get(cause, False),
    }
