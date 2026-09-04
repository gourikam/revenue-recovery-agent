from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from app import db, synthetic_data, recovery_engine, razorpay_client

app = FastAPI(title="AI Revenue Recovery Agent", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

db.init_db()


@app.get("/")
def root():
    return {
        "service": "AI Revenue Recovery Agent",
        "endpoints": ["/generate-batch", "/run-batch", "/cases", "/cases/{case_id}/audit",
                      "/cases/{case_id}/voice", "/metrics", "/check-pending-links",
                      "/webhook/razorpay", "/reset", "/status"],
    }


@app.get("/status")
def status():
    """Lets any client (e.g. the dashboard) know if this backend has real Razorpay
    keys configured, without needing local access to razorpay_client itself."""
    return {"razorpay_configured": razorpay_client.is_configured()}


@app.post("/generate-batch")
def generate_batch(n: int = 75):
    """Creates a fresh synthetic batch of failed payments and saves it to disk."""
    batch = synthetic_data.generate_batch(n)
    path = synthetic_data.save_batch(batch)
    return {"generated": len(batch), "path": str(path)}


@app.post("/run-batch")
def run_batch(n: int = 75, reset: bool = True):
    """Full pipeline: generate synthetic batch -> diagnose -> decide -> execute -> log.
    reset=True clears only PREVIOUS BATCH cases -- real webhook-sourced cases
    (and their audit trail) are always preserved, never silently wiped by
    clicking the demo button."""
    if reset:
        db.reset_batch_cases()
    batch = synthetic_data.generate_batch(n)
    results = recovery_engine.process_batch(batch)
    metrics = recovery_engine.compute_metrics()
    return {"processed": len(results), "metrics": metrics}


@app.get("/cases")
def list_cases():
    return db.get_all_cases()


@app.get("/cases/{case_id}/audit")
def case_audit(case_id: str):
    return {"case": db.get_case(case_id), "audit_log": db.get_audit_log(case_id)}


@app.get("/cases/{case_id}/voice")
def case_voice(case_id: str):
    """Returns the raw MP3 audio of a case's Hinglish voice reminder, if one was generated."""
    audio = db.get_voice_note(case_id)
    if audio is None:
        raise HTTPException(status_code=404, detail="No voice note for this case")
    return Response(content=audio, media_type="audio/mpeg")


@app.get("/metrics")
def metrics():
    return recovery_engine.compute_metrics()


@app.post("/check-pending-links")
def check_pending_links():
    """Polls Razorpay for real payment links awaiting customer payment and updates status."""
    return recovery_engine.check_pending_links()


@app.post("/webhook/razorpay")
async def razorpay_webhook(request: Request, x_razorpay_signature: str = Header(None)):
    """
    Real-time entry point: Razorpay POSTs here the moment a payment fails --
    no polling, no batch, no synthetic data. This is what makes the agent a
    production-shaped system rather than a one-off script run on demand.

    Setup (required before this will receive anything):
    1. Set RAZORPAY_WEBHOOK_SECRET in .env (create the webhook + secret in
       Razorpay Dashboard -> Settings -> Webhooks, subscribe to 'payment.failed').
    2. Razorpay can't reach localhost -- expose this server publicly during
       development with a tunnel, e.g.: `ngrok http 8000`, then point the
       Razorpay webhook URL at https://<ngrok-id>.ngrok.io/webhook/razorpay
    """
    raw_body = await request.body()

    if not x_razorpay_signature:
        raise HTTPException(status_code=400, detail="Missing X-Razorpay-Signature header")

    if not razorpay_client.verify_webhook_signature(raw_body, x_razorpay_signature):
        # Reject unverified payloads outright -- never process a webhook body
        # we can't cryptographically confirm came from Razorpay.
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    import json
    payload = json.loads(raw_body)
    raw_case = razorpay_client.parse_failed_payment_event(payload)

    if raw_case is None:
        # Not a payment.failed event (Razorpay sends many event types to the
        # same URL) -- acknowledge receipt so Razorpay doesn't retry, but do nothing.
        return {"status": "ignored", "reason": "not a payment.failed event"}

    result = recovery_engine.process_case(raw_case, source="webhook")
    return {"status": "processed", "case_id": result["case_id"],
            "root_cause": result["root_cause"], "intervention": result["intervention"]}


@app.post("/reset")
def reset():
    db.reset_db()
    return {"status": "reset"}