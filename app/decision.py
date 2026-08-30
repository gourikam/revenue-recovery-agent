"""
Decision layer: given a diagnosed root cause, decide the ONE bounded action to take.

This is the part of the system the buildathon bar cares about most:
"Every money action explainable, bounded and gated" / "compliant escalation,
stopping rules". So every decision here returns a human-readable `reason`
alongside the action -- nothing happens silently.
"""

MAX_RETRIES = 3

# action space is intentionally small and explicit -- no open-ended "do whatever" agent behavior
ACTIONS = [
    "retry_payment",
    "send_payment_link",
    "send_hinglish_reminder",
    "escalate_to_human",
    "no_action_stop",
]


def decide(case: dict, diagnosis: dict) -> dict:
    """
    Returns: {action, reason, stopping_reason (optional)}
    """
    cause = diagnosis["root_cause"]
    recoverable = diagnosis["is_recoverable"]
    retry_count = case.get("retry_count", 0)

    # --- HARD STOPPING RULES (compliance / safety first, checked before anything else) ---
    if cause == "CARD_BLOCKED_FRAUD":
        return {
            "action": "escalate_to_human",
            "reason": "Card flagged as lost/stolen/fraud-blocked. Never auto-retry a "
                       "flagged card -- this is a hard compliance stop.",
            "stopping_reason": "fraud_flag_hard_stop",
        }

    # --- Unrecognized or low-confidence diagnosis: escalate BEFORE any money
    # action, never guess. This check must come before the recoverability
    # check below -- otherwise an UNKNOWN cause (is_recoverable defaults to
    # False) would incorrectly fall into the "not recoverable -> send a
    # payment link" branch instead of going to a human. ---
    if cause == "UNKNOWN" or diagnosis["root_cause_confidence"] < 0.5:
        return {
            "action": "escalate_to_human",
            "reason": f"Diagnosis confidence too low or cause unrecognized ('{cause}', "
                      f"confidence={diagnosis['root_cause_confidence']}). Routing to "
                      f"human rather than guessing with money actions.",
            "stopping_reason": "low_confidence_hard_stop",
        }

    if not recoverable:
        return {
            "action": "send_payment_link",
            "reason": f"Root cause '{cause}' is not retry-recoverable (e.g. needs a new "
                      f"card). Sending a fresh payment link instead of retrying the same card.",
            "stopping_reason": None,
        }

    if retry_count >= MAX_RETRIES:
        return {
            "action": "escalate_to_human",
            "reason": f"Hit max retry limit ({MAX_RETRIES}) with no success. Stopping "
                      f"automated attempts and escalating to human collections workflow.",
            "stopping_reason": "max_retries_exhausted",
        }

    # --- Recoverable, within retry budget ---
    if cause == "TRANSIENT_GATEWAY_ISSUE":
        return {
            "action": "retry_payment",
            "reason": "Transient gateway/network issue -- safe to retry automatically "
                      "with backoff.",
            "stopping_reason": None,
        }

    if cause in ("INSUFFICIENT_FUNDS", "GENERIC_DECLINE", "LIMIT_EXCEEDED"):
        return {
            "action": "send_hinglish_reminder",
            "reason": f"'{cause}' is often a timing/awareness issue for the customer -- "
                      f"a friendly reminder nudges retry better than a silent auto-charge.",
            "stopping_reason": None,
        }

    if cause == "AUTH_FAILURE":
        return {
            "action": "send_payment_link",
            "reason": "Auth/CVV/3DS failure -- customer needs to re-enter details, so "
                      "send a fresh secure payment link rather than retrying blind.",
            "stopping_reason": None,
        }

    # --- Fallback: unknown, low-confidence cases never get auto-actioned ---
    return {
        "action": "escalate_to_human",
        "reason": f"Diagnosis confidence too low or cause unrecognized ('{cause}'). "
                  f"Routing to human rather than guessing with money actions.",
        "stopping_reason": "low_confidence_hard_stop",
    }