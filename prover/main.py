"""
main.py — ZEROAUDIT Prover FastAPI Server
Exposes REST endpoints consumed by the React dashboard.

Endpoints:
  GET  /health
  GET  /stats
  GET  /ledger              — full audit export (zero PII)
  GET  /ledger/{txn_id}     — single commitment record
  POST /verify              — run LWE verification
  POST /quarantine/{txn_id}
  POST /authorize/{txn_id}
  POST /reject/{txn_id}
  GET  /stream              — SSE stream for live dashboard updates
"""

import asyncio
import json
import time
import logging
import threading
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .config.settings import settings
from .crypto.commitment import get_store
from .consumer import ProverConsumer

logging.basicConfig(level=settings.LOG_LEVEL, format=settings.LOG_FORMAT)
logger = logging.getLogger("zeroaudit.main")

app = FastAPI(
    title="ZEROAUDIT Prover API",
    description="Post-quantum ZK commitment engine for financial transactions",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global state ───────────────────────────────────────────────────────────────
store = get_store()
_consumer: Optional[ProverConsumer] = None
_consumer_thread: Optional[threading.Thread] = None


@app.on_event("startup")
async def startup():
    global _consumer, _consumer_thread
    _consumer = ProverConsumer()
    _consumer_thread = threading.Thread(target=_consumer.run, daemon=True)
    _consumer_thread.start()
    logger.info("ZEROAUDIT Prover API started")


@app.on_event("shutdown")
async def shutdown():
    if _consumer:
        _consumer.stop()


# ── Pydantic models ────────────────────────────────────────────────────────────

class VerifyRequest(BaseModel):
    txn_id: str
    amount_cents: int


class CommitRequest(BaseModel):
    txn_id: str
    amount_cents: int
    account_id: str
    txn_type: str
    anomaly_score: float = 0.0


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "zeroaudit-prover", "pii_bytes": 0}


@app.get("/stats")
def stats():
    s = store.stats()
    tps = _consumer.tps() if _consumer else 0.0
    return {**s, "tps": tps, "pii_bytes": 0}


@app.get("/ledger")
def get_ledger():
    return store.audit_export()


@app.get("/ledger/{txn_id}")
def get_commitment(txn_id: str):
    record = store.get(txn_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"TXN {txn_id} not found")
    return record.to_export_dict()


@app.post("/verify")
def verify_transaction(req: VerifyRequest):
    result = store.verify_txn(req.txn_id, req.amount_cents)
    return result


@app.post("/commit")
def commit_transaction(req: CommitRequest):
    """Manual commit endpoint (for testing / simulator)."""
    record = store.add(
        txn_id=req.txn_id,
        amount_cents=req.amount_cents,
        account_id=req.account_id,
        txn_type=req.txn_type,
        anomaly_score=req.anomaly_score,
    )
    return record.to_export_dict()


@app.post("/quarantine/{txn_id}")
def quarantine_transaction(txn_id: str):
    ok = store.quarantine(txn_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"TXN {txn_id} not found")
    return {"txn_id": txn_id, "status": "QUARANTINED"}


@app.post("/authorize/{txn_id}")
def authorize_transaction(txn_id: str):
    ok = store.authorize(txn_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"TXN {txn_id} not found")
    return {"txn_id": txn_id, "status": "VERIFIED"}


@app.post("/reject/{txn_id}")
def reject_transaction(txn_id: str):
    ok = store.reject(txn_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"TXN {txn_id} not found")
    return {"txn_id": txn_id, "status": "REJECTED"}


@app.get("/stream")
async def sse_stream():
    """Server-Sent Events stream for live dashboard updates."""
    async def event_generator():
        last_count = 0
        while True:
            current_records = store.audit_export()
            if len(current_records) != last_count:
                # Send new records only
                new_records = current_records[last_count:]
                last_count = len(current_records)
                for record in new_records:
                    data = json.dumps(record)
                    yield f"data: {data}\n\n"

            # Also stream stats every second
            s = store.stats()
            tps = _consumer.tps() if _consumer else 0.0
            stats_data = json.dumps({**s, "tps": tps, "event": "stats"})
            yield f"data: {stats_data}\n\n"

            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "prover.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        workers=1,   # SSE requires single worker or Redis pub/sub
        reload=False,
    )