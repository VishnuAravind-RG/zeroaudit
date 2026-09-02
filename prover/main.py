"""
prover/main.py - ZEROAUDIT prover API (inside the enclave boundary)

Runs the Kafka ingest loop in a background thread and exposes an internal
API. This service is NOT in the DMZ: it holds the master key, the lattice
secret, and the signing key, and it is the only place raw amounts exist.

The only things it publishes outward are public keys and openings the bank
has explicitly chosen to disclose.
"""

import os
import logging
import threading

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config.settings import settings
from .consumer import ProverConsumer
from .crypto.commitment import get_store

logger = logging.getLogger("zeroaudit.prover")

app = FastAPI(title="ZEROAUDIT Prover", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

consumer = ProverConsumer()
_thread: threading.Thread = None


class OpeningRequest(BaseModel):
    """Selective disclosure request from an auditor."""
    txn_id: str
    amount_cents: int


@app.on_event("startup")
async def startup():
    global _thread
    _thread = threading.Thread(target=consumer.run, name="prover-ingest", daemon=True)
    _thread.start()
    logger.info("prover ingest thread started")


@app.on_event("shutdown")
async def shutdown():
    consumer.stop()


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "prover",
        "enclave": True,
        "ingest_alive": bool(_thread and _thread.is_alive()),
    }


@app.get("/stats")
async def stats():
    store_stats = get_store().stats()
    return {**consumer.stats(), "ledger": store_stats}


@app.get("/keys")
async def keys():
    """Public keys for the external verifier.

    Publishing these is the point: the auditor must be able to check the
    bank's signatures and openings without holding anything secret. No
    private key material is reachable from this process's API surface.
    """
    return get_store().public_keys()


@app.get("/chain/verify")
async def chain_verify(n: int = 500):
    """Prover-side hash-chain self-check."""
    return get_store().verify_chain(limit=n)


@app.post("/audit/open")
async def audit_open(req: OpeningRequest):
    """Disclose the opening for ONE transaction, for an auditor to verify.

    This is selective disclosure: it reveals the blinding factor for a single
    commitment. Every other record in the ledger remains sealed, and the
    master key never leaves this process.
    """
    opening = get_store().opening_for(req.txn_id, req.amount_cents)
    if not opening:
        raise HTTPException(status_code=404, detail="TXN %s not in ledger" % req.txn_id)
    return opening


@app.post("/verify")
async def verify_txn(req: OpeningRequest):
    """Prover-side recomputation trace for a transaction."""
    return get_store().verify_txn(req.txn_id, req.amount_cents)


@app.get("/ledger/export")
async def export(n: int = 500):
    return {"records": get_store().audit_export(limit=n), "pii_bytes": 0}


if __name__ == "__main__":
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format=settings.LOG_FORMAT,
    )

    # Mutual TLS, when certs are provided. This replaces "the verifier
    # fetches /keys over plain HTTP and trusts it on first use" with a real
    # TLS handshake in which the prover REQUIRES a client certificate signed
    # by the same CA - a network position that could intercept or spoof the
    # old plaintext exchange can no longer read it (TLS) or impersonate the
    # verifier to obtain it (client cert required). Generate the demo CA and
    # certs with `python -m scripts.gen_tls_certs`.
    #
    # Optional and off by default: with no cert env vars set, this serves
    # plain HTTP exactly as before, which is what the local test suite and
    # FastAPI's TestClient rely on (TestClient never touches the network
    # stack, so TLS config here doesn't affect it either way).
    tls_cert = os.environ.get("TLS_CERT_FILE")
    tls_key = os.environ.get("TLS_KEY_FILE")
    tls_ca = os.environ.get("TLS_CA_FILE")

    ssl_kwargs = {}
    if tls_cert and tls_key:
        import ssl
        ssl_kwargs = {
            "ssl_certfile": tls_cert,
            "ssl_keyfile": tls_key,
        }
        if tls_ca:
            ssl_kwargs["ssl_ca_certs"] = tls_ca
            ssl_kwargs["ssl_cert_reqs"] = ssl.CERT_REQUIRED
            logger.info("serving HTTPS with mutual TLS required (CA: %s)", tls_ca)
        else:
            logger.warning("TLS cert/key set but no CA - serving HTTPS without client auth")
    else:
        logger.warning("no TLS_CERT_FILE/TLS_KEY_FILE - serving plain HTTP "
                       "(fine for local dev, not for anything holding these secrets)")

    uvicorn.run(
        app,
        host=settings.API_HOST,
        port=settings.API_PORT,
        log_level=settings.LOG_LEVEL.lower(),
        **ssl_kwargs,
    )
