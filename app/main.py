from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db, synthetic_data, recovery_engine

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
                      "/metrics", "/reset"],
    }


@app.post("/generate-batch")
def generate_batch(n: int = 75):
    """Creates a fresh synthetic batch of failed payments and saves it to disk."""
    batch = synthetic_data.generate_batch(n)
    path = synthetic_data.save_batch(batch)
    return {"generated": len(batch), "path": str(path)}


@app.post("/run-batch")
def run_batch(n: int = 75, reset: bool = True):
    """Full pipeline: generate synthetic batch -> diagnose -> decide -> execute -> log."""
    if reset:
        db.reset_db()
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


@app.get("/metrics")
def metrics():
    return recovery_engine.compute_metrics()


@app.post("/reset")
def reset():
    db.reset_db()
    return {"status": "reset"}
