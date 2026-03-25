"""
prover/main.py – ZEROAUDIT Prover FastAPI Server
Robust startup: consumer thread runs non-daemon, uvicorn blocks.
"""

import uvicorn
import logging
import threading
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config.settings import settings
from .consumer import ProverConsumer

logger = logging.getLogger("zeroaudit.prover")

app = FastAPI(title="ZEROAUDIT Prover", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

consumer = ProverConsumer()

@app.on_event("startup")
async def startup():
    logger.info("Starting ProverConsumer in background thread")
    thread = threading.Thread(target=consumer.run, daemon=False)
    thread.start()
    logger.info("ProverConsumer thread started")

@app.on_event("shutdown")
async def shutdown():
    logger.info("Stopping ProverConsumer...")
    consumer.stop()

@app.get("/health")
async def health():
    return {"status": "ok", "service": "prover"}

@app.get("/stats")
async def stats():
    return {
        "tps": consumer.tps(),
        "processed": consumer._stats["processed"],
        "errors": consumer._stats["errors"],
        "signature_failures": consumer._stats.get("signature_failures", 0),
    }

if __name__ == "__main__":
    uvicorn.run(
        "prover.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        log_level=settings.LOG_LEVEL.lower(),
    )
