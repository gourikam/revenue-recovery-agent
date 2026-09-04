"""
Orchestrates: diagnose -> decide -> execute -> log, for one case or a batch.

Execution against real Razorpay is stubbed via `execute_action()` -- swap in
real Razorpay Python SDK calls (payment_link.create, order retry, etc.) once
you have test-mode keys. The pipeline, decision logic, and audit trail work
identically either way.
"""
import random
import time
from datetime import datetime, timedelta

from app import db, diagnosis, decision, messaging, razorpay_client, voice

random.seed(7)  # reproducible synthetic outcomes for demo consistency


def execute_action(case: dict, action: str, message: str, reason: str, attempt_real_api: bool = True) -> dict:
    """
    Tries a REAL Razorpay test-mode Payment Link first (if RAZORPAY_KEY_ID/SECRET
    are configured AND attempt_real_api is True). Falls back to a simulated
    outcome otherwise -- so the app always runs end to end.

    attempt_real_api=False is used for batch runs: firing 20+ payment-link
    creation calls in rapid succession reliably trips Razorpay's test-mode
    rate limit ("Too many requests"), which silently degrades an entire
    batch to simulation anyway. Rather than fight an unknown rate-limit
    ceiling, batch runs are simulated by design -- they exist to demonstrate
    measured metrics at scale. Real API calls are reserved for
    webhook-sourced single cases, where there's no burst risk and the real
    call actually has a chance to succeed.

    Real path: link is created via the API and starts 'pending' -- whether it's
    actually 'recovered' depends on the customer paying it, checked later by
    check_pending_links(). This is intentionally honest: we don't fabricate a
    'recovered' status for a real link nobody has paid yet.
    """
    if action == "escalate_to_human":
        return {"status": "escalated", "recovered_amount": 0, "is_live": False,
                "payment_link_id": None, "payment_link_url": None}

    if action == "no_action_stop":
        return {"status": "stopped", "recovered_amount": 0, "is_live": False,
                "payment_link_id": None, "payment_link_url": None}

    # retry_payment, send_payment_link, send_hinglish_reminder all funnel through
    # a real payment link when Razorpay is configured AND this is a single
    # (non-batch) case -- see docstring for why batch skips this entirely.
    if attempt_real_api:
        link = razorpay_client.create_payment_link(case, description=reason)
        if link is not None:
            return {"status": "pending", "recovered_amount": 0, "is_live": True,
                    "payment_link_id": link["id"], "payment_link_url": link["short_url"]}

    # --- simulated outcome: no Razorpay keys, real call failed, or batch mode ---
    ease = case.get("_synthetic_ease", "medium")
    recovery_prob = {"easy": 0.75, "medium": 0.45, "hard": 0.15, "impossible": 0.0}[ease]
    recovered = random.random() < recovery_prob
    if recovered:
        return {"status": "recovered", "recovered_amount": case["amount_inr"], "is_live": False,
                "payment_link_id": None, "payment_link_url": None}
    return {"status": "failed_retry", "recovered_amount": 0, "is_live": False,
            "payment_link_id": None, "payment_link_url": None}


def check_pending_links() -> dict:
    """
    Polls Razorpay for every case with a real pending payment link and updates
    its status if the customer has paid, cancelled, or let it expire.
    Call this periodically (e.g. a 'Refresh' button in the dashboard) --
    Razorpay doesn't push updates to this simple demo, so we pull.
    """
    pending = db.get_pending_live_cases()
    updated = 0
    for case in pending:
        status = razorpay_client.fetch_payment_link_status(case["payment_link_id"])
        if status is None:
            continue
        if status == "paid":
            db.upsert_case({"case_id": case["case_id"], "execution_status": "recovered",
                             "amount_recovered_inr": case["amount_inr"]})
            db.log_event(case["case_id"], "execution", "REAL payment link paid by customer")
            updated += 1
        elif status in ("cancelled", "expired"):
            db.upsert_case({"case_id": case["case_id"], "execution_status": "failed_retry"})
            db.log_event(case["case_id"], "execution", f"REAL payment link {status}")
            updated += 1
    return {"checked": len(pending), "updated": updated}


def process_case(raw_case: dict, source: str = "batch", synthesize_voice: bool = True,
                  attempt_real_api: bool = True) -> dict:
    case_id = raw_case["case_id"]

    # 1. DIAGNOSE
    diag = diagnosis.diagnose(raw_case["raw_failure_description"])
    db.log_event(case_id, "diagnosis",
                 f"root_cause={diag['root_cause']} method={diag['diagnosis_method']} "
                 f"confidence={diag['root_cause_confidence']}")

    # 2. DECIDE (bounded, with explicit stopping rules)
    current = db.get_case(case_id) or {}
    case_for_decision = {**raw_case, "retry_count": current.get("retry_count", 0)}
    dec = decision.decide(case_for_decision, diag)
    db.log_event(case_id, "decision", f"action={dec['action']} reason={dec['reason']}")

    # 3. EXECUTE (tries a real Razorpay payment link first; message is built after,
    #    since it needs the real link if one was created)
    result = execute_action(raw_case, dec["action"], message="", reason=dec["reason"],
                            attempt_real_api=attempt_real_api)
    db.log_event(case_id, "execution", f"status={result['status']} "
                 f"recovered_amount={result['recovered_amount']} "
                 f"live={result['is_live']}")

    real_link = result.get("payment_link_url")
    message = ""
    voice_audio = None
    if dec["action"] == "send_hinglish_reminder":
        message = messaging.generate_hinglish_reminder(raw_case, diag["root_cause"], real_link=real_link)
        # Real TTS synthesis of the Hinglish message -- optional, never blocks
        # the pipeline if it fails (no network, quota, etc). Skipped during
        # batch runs to keep the demo button fast (a 75-case batch making 75
        # real API calls plus dozens of real TTS calls can take minutes);
        # always runs for webhook-sourced single cases, where it matters more.
        if synthesize_voice:
            voice_audio = voice.synthesize_hinglish_voice(message)
            if voice_audio:
                db.log_event(case_id, "voice", "Synthesized Hinglish voice reminder (gTTS)")
    elif dec["action"] in ("send_payment_link", "retry_payment") and real_link:
        message = messaging.generate_payment_link_message(raw_case, diag["root_cause"], real_link=real_link)

    # Mandate-linked retries follow a compliant spaced schedule instead of an
    # instant retry -- decision.py computes this only for subscription-linked
    # TRANSIENT_GATEWAY_ISSUE cases; everything else gets None here.
    next_retry_at = None
    if dec.get("next_retry_in_days") is not None:
        next_retry_at = (datetime.utcnow() + timedelta(days=dec["next_retry_in_days"])).isoformat()
        db.log_event(case_id, "decision",
                     f"Mandate retry scheduled for {next_retry_at} "
                     f"(+{dec['next_retry_in_days']}d, not instant)")

    retry_count = current.get("retry_count", 0) + (1 if dec["action"] == "retry_payment" else 0)

    # 4. PERSIST TO AUDIT TRAIL (this row is the ground truth of what happened)
    db.upsert_case({
        "case_id": case_id,
        "payment_id": raw_case["payment_id"],
        "subscription_id": raw_case.get("subscription_id"),
        "customer_name": raw_case["customer_name"],
        "customer_phone": raw_case["customer_phone"],
        "amount_inr": raw_case["amount_inr"],
        "raw_failure_code": raw_case["raw_failure_code"],
        "raw_failure_description": raw_case["raw_failure_description"],
        "created_at": raw_case["created_at"],
        "root_cause": diag["root_cause"],
        "root_cause_confidence": diag["root_cause_confidence"],
        "diagnosis_method": diag["diagnosis_method"],
        "intervention": dec["action"],
        "stopping_reason": dec.get("stopping_reason"),
        "retry_count": retry_count,
        "max_retries": decision.MAX_RETRIES,
        "message_sent": message,
        "execution_status": result["status"],
        "amount_recovered_inr": result["recovered_amount"],
        "payment_link_id": result.get("payment_link_id"),
        "payment_link_url": result.get("payment_link_url"),
        "is_live_razorpay": 1 if result.get("is_live") else 0,
        "source": source,
        "next_retry_at": next_retry_at,
        "voice_note": voice_audio,
    })

    return db.get_case(case_id)


def process_batch(batch: list[dict]) -> list[dict]:
    # Batch runs are simulated by design (attempt_real_api=False) -- see
    # execute_action's docstring for why: bursts of real payment-link calls
    # reliably trip Razorpay's test-mode rate limit. Real API calls are
    # reserved for webhook-sourced single cases (see app/main.py's
    # /webhook/razorpay handler, which calls process_case with the default
    # attempt_real_api=True).
    results = []
    for raw_case in batch:
        results.append(process_case(raw_case, source="batch", synthesize_voice=False,
                                    attempt_real_api=False))
    return results


def compute_metrics() -> dict:
    """Honest metrics for the dashboard -- includes cases that were NOT recovered."""
    cases = db.get_all_cases()
    total_at_risk = sum(c["amount_inr"] for c in cases)
    total_recovered = sum(c["amount_recovered_inr"] for c in cases)
    n_total = len(cases)
    n_recovered = len([c for c in cases if c["execution_status"] == "recovered"])
    n_escalated = len([c for c in cases if c["execution_status"] == "escalated"])
    n_failed = len([c for c in cases if c["execution_status"] == "failed_retry"])
    n_pending_live = len([c for c in cases if c["execution_status"] == "pending"])

    by_cause = {}
    for c in cases:
        rc = c["root_cause"]
        by_cause.setdefault(rc, {"count": 0, "recovered": 0})
        by_cause[rc]["count"] += 1
        if c["execution_status"] == "recovered":
            by_cause[rc]["recovered"] += 1

    return {
        "total_cases": n_total,
        "total_amount_at_risk_inr": round(total_at_risk, 2),
        "total_amount_recovered_inr": round(total_recovered, 2),
        "recovery_rate_pct": round(100 * total_recovered / total_at_risk, 2) if total_at_risk else 0,
        "n_recovered": n_recovered,
        "n_escalated_unresolved": n_escalated,
        "n_failed_retry": n_failed,
        "n_pending_live_links": n_pending_live,
        "by_root_cause": by_cause,
    }