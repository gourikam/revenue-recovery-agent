"""
Orchestrates: diagnose -> decide -> execute -> log, for one case or a batch.

Execution against real Razorpay is stubbed via `execute_action()` -- swap in
real Razorpay Python SDK calls (payment_link.create, order retry, etc.) once
you have test-mode keys. The pipeline, decision logic, and audit trail work
identically either way.
"""
import random
from datetime import datetime

from app import db, diagnosis, decision, messaging

random.seed(7)  # reproducible synthetic outcomes for demo consistency


def execute_action(case: dict, action: str, message: str) -> dict:
    """
    Simulates calling Razorpay test-mode APIs / SMS-WhatsApp gateway.
    Replace the random outcome with real razorpay-python calls, e.g.:
        client.payment_link.create({...})
        client.payment.capture(payment_id, amount)
    Recovery likelihood here is intentionally tied to how recoverable the
    underlying case is (via _synthetic_ease), same as a real system would see
    easy cases recover more often than hard ones.
    """
    ease = case.get("_synthetic_ease", "medium")
    recovery_prob = {"easy": 0.75, "medium": 0.45, "hard": 0.15, "impossible": 0.0}[ease]

    if action == "escalate_to_human":
        return {"status": "escalated", "recovered_amount": 0}

    if action == "no_action_stop":
        return {"status": "stopped", "recovered_amount": 0}

    recovered = random.random() < recovery_prob
    if recovered:
        return {"status": "recovered", "recovered_amount": case["amount_inr"]}
    return {"status": "failed_retry", "recovered_amount": 0}


def process_case(raw_case: dict) -> dict:
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

    # 3. GENERATE MESSAGE + EXECUTE
    message = ""
    if dec["action"] == "send_hinglish_reminder":
        message = messaging.generate_hinglish_reminder(raw_case, diag["root_cause"])
    elif dec["action"] == "send_payment_link":
        message = messaging.generate_payment_link_message(raw_case, diag["root_cause"])

    result = execute_action(raw_case, dec["action"], message)
    db.log_event(case_id, "execution", f"status={result['status']} "
                 f"recovered_amount={result['recovered_amount']}")

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
    })

    return db.get_case(case_id)


def process_batch(batch: list[dict]) -> list[dict]:
    results = []
    for raw_case in batch:
        results.append(process_case(raw_case))
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
        "by_root_cause": by_cause,
    }
