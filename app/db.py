"""
Audit trail database layer.

Every payment failure that enters the system gets one row that is updated
as it moves through: diagnosed -> decided -> executed -> resolved/escalated/exhausted.
Nothing is deleted. This table IS the audit trail the buildathon bar asks for.
"""
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).parent.parent / "data" / "recovery.db"
DB_PATH.parent.mkdir(exist_ok=True)


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cases (
                case_id TEXT PRIMARY KEY,
                payment_id TEXT,
                subscription_id TEXT,
                customer_name TEXT,
                customer_phone TEXT,
                amount_inr REAL,
                currency TEXT DEFAULT 'INR',
                raw_failure_code TEXT,
                raw_failure_description TEXT,
                created_at TEXT,

                -- diagnosis stage
                root_cause TEXT,
                root_cause_confidence REAL,
                diagnosis_method TEXT,          -- 'rule' or 'llm'

                -- decision stage
                intervention TEXT,              -- retry / payment_link / hinglish_reminder / escalate / no_action
                stopping_reason TEXT,           -- filled only if the agent decided NOT to act further
                retry_count INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 3,

                -- execution stage
                message_sent TEXT,
                execution_status TEXT,          -- 'pending','recovered','failed_retry','escalated','exhausted'
                amount_recovered_inr REAL DEFAULT 0,
                payment_link_id TEXT,           -- real Razorpay payment link id, if created
                payment_link_url TEXT,          -- real Razorpay payment link url, if created
                is_live_razorpay INTEGER DEFAULT 0,  -- 1 if this case used a real API call, 0 if simulated
                source TEXT DEFAULT 'batch',    -- 'batch' (synthetic demo run) or 'webhook' (real Razorpay event)
                next_retry_at TEXT,              -- for mandate-linked failures: compliant next retry date, not instant
                voice_note BLOB,                 -- TTS audio of the Hinglish reminder, when generated

                updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT,
                timestamp TEXT,
                stage TEXT,
                detail TEXT
            )
        """)


def upsert_case(case: dict):
    case = dict(case)
    case["updated_at"] = datetime.utcnow().isoformat()
    with get_conn() as conn:
        cols = ", ".join(case.keys())
        placeholders = ", ".join(["?"] * len(case))
        updates = ", ".join([f"{k}=excluded.{k}" for k in case.keys() if k != "case_id"])
        conn.execute(
            f"INSERT INTO cases ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(case_id) DO UPDATE SET {updates}",
            list(case.values()),
        )


def log_event(case_id: str, stage: str, detail: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO audit_log (case_id, timestamp, stage, detail) VALUES (?, ?, ?, ?)",
            (case_id, datetime.utcnow().isoformat(), stage, detail),
        )


# All case columns EXCEPT voice_note (raw audio bytes) -- bytes can't be
# JSON-serialized by FastAPI, so any endpoint returning case data as JSON
# must never include the blob. has_voice_note flags its presence instead;
# the actual audio is served separately via get_voice_note() / a binary endpoint.
_CASE_COLUMNS_NO_BLOB = """
    case_id, payment_id, subscription_id, customer_name, customer_phone,
    amount_inr, currency, raw_failure_code, raw_failure_description, created_at,
    root_cause, root_cause_confidence, diagnosis_method,
    intervention, stopping_reason, retry_count, max_retries,
    message_sent, execution_status, amount_recovered_inr,
    payment_link_id, payment_link_url, is_live_razorpay, source,
    next_retry_at, updated_at,
    (voice_note IS NOT NULL) AS has_voice_note
"""


def get_case(case_id: str):
    with get_conn() as conn:
        row = conn.execute(
            f"SELECT {_CASE_COLUMNS_NO_BLOB} FROM cases WHERE case_id=?", (case_id,)
        ).fetchone()
        return dict(row) if row else None


def get_all_cases():
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT {_CASE_COLUMNS_NO_BLOB} FROM cases ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_voice_note(case_id: str):
    """Returns raw MP3 bytes for a case's Hinglish voice reminder, or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT voice_note FROM cases WHERE case_id=?", (case_id,)
        ).fetchone()
        return row["voice_note"] if row and row["voice_note"] else None


def get_audit_log(case_id: str = None):
    with get_conn() as conn:
        if case_id:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE case_id=? ORDER BY timestamp", (case_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM audit_log ORDER BY timestamp").fetchall()
        return [dict(r) for r in rows]


def get_pending_live_cases():
    """Cases with a real payment link still awaiting payment -- used for polling."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM cases WHERE is_live_razorpay=1 AND execution_status='pending'"
        ).fetchall()
        return [dict(r) for r in rows]


def reset_batch_cases():
    """
    Clears ONLY batch-sourced demo cases -- preserves real webhook-sourced
    cases and their audit trail. This is what /run-batch uses by default,
    since real evidence (a genuinely recovered payment) must never be
    silently wiped out by clicking a demo button.
    """
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM audit_log WHERE case_id IN "
            "(SELECT case_id FROM cases WHERE source='batch')"
        )
        conn.execute("DELETE FROM cases WHERE source='batch'")


def reset_db():
    """Wipes EVERYTHING, including real webhook-sourced cases. Only for
    deliberate full resets (the explicit /reset endpoint), never called
    automatically by a batch run."""
    with get_conn() as conn:
        conn.execute("DROP TABLE IF EXISTS cases")
        conn.execute("DROP TABLE IF EXISTS audit_log")
    init_db()