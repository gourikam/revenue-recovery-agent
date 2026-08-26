# AI Revenue Recovery Agent

Built for the Razorpay AI Buildathon — **Track 03: AI Revenue Recovery**.

Diagnoses why a subscription/payment failed, decides a single bounded
intervention (retry / payment link / Hinglish reminder / escalate), executes
it, and logs every step to an audit trail — then reports honest recovery
metrics on a batch, including the cases it could **not** resolve.

## Why this exists

Revenue loss rarely happens in one clean step — a payment degrades, a
subscription fails, a customer just needs a nudge. This agent closes the
loop: detect → diagnose → decide → act → measure, with hard stopping rules
so it never blindly retries a fraud-flagged card or retries forever.

## Architecture

```
synthetic_data.py  →  generates a batch of realistic failed-payment events
        ↓
diagnosis.py       →  rule-based root-cause classification (LLM fallback for
                       ambiguous cases, via Groq)
        ↓
decision.py        →  bounded decision logic: picks ONE action, with
                       explicit hard stopping rules (never retry fraud,
                       max 3 retries, low-confidence → human)
        ↓
messaging.py       →  generates the customer-facing message (Hinglish
                       reminder via LLM, or a payment-link template)
        ↓
recovery_engine.py →  orchestrates the above + simulates execution against
                       Razorpay test-mode-style outcomes
        ↓
db.py              →  SQLite audit trail — every case, every decision,
                       every step, nothing deleted
        ↓
dashboard.py        (Streamlit) → recovery rate, breakdown by root cause,
main.py              (FastAPI)  → and an honest exception list of
                                   unresolved cases
```

## Hard stopping rules (the safety bar)

1. **Never retry a card flagged as lost/stolen/fraud** — hard compliance
   stop, always escalates to human, 0% auto-recovery attempted.
2. **Max 3 retries** — after that, automatically escalates instead of
   retrying forever.
3. **Low-confidence diagnosis → human**, never a guessed money action.

Every decision returns a human-readable `reason` — nothing happens silently.

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env   # optional — works with zero API keys via rule-based fallback

# Option A: Streamlit dashboard (recommended for the demo)
streamlit run dashboard.py

# Option B: FastAPI backend
uvicorn app.main:app --reload
# then: POST /run-batch, GET /metrics, GET /cases/{id}/audit
```

No API keys are required to run this end to end — `diagnosis.py` and
`messaging.py` both fall back to rule-based logic / templates if
`GROQ_API_KEY` isn't set, and `recovery_engine.py` simulates Razorpay
test-mode outcomes so you can demo without live keys.

## Results on a synthetic 75-case batch

- **51.4% recovery rate** (₹1,11,852 of ₹2,17,669 at-risk recovered)
- **22 cases correctly hard-stopped** (fraud-flagged cards) — 0 recovery
  attempted on any of them, as required
- **25 cases** genuinely failed retry and are shown in the honest exception
  list, not hidden

## What's simulated vs real

- Razorpay API calls (`execute_action` in `recovery_engine.py`) are stubbed
  with realistic probability-based outcomes. Swap in real
  `razorpay-python` SDK calls (`payment_link.create`, retry logic) once
  you have test-mode keys wired to a live checkout flow.
- Everything else — diagnosis, decision logic, stopping rules, audit trail,
  metrics — runs for real.

## Next steps if extending

- Real Razorpay Subscriptions webhook listener instead of synthetic batch
- WhatsApp/SMS gateway integration for actually sending the Hinglish
  reminder
- A/B test which intervention actually recovers more per root-cause
  category
