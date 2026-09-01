"""
verifier/dashboard.py - ZEROAUDIT verifier API (external DMZ)

The auditor-facing service. It holds the prover's public keys and the public
Kafka topics, and nothing else - no master key, no lattice secret, no amounts.

Two corrections from an earlier revision are worth stating, because they were
the difference between a demo and a working audit path:

  1. It imported the prover's in-process CommitmentStore via get_store(). The
     verifier is a SEPARATE CONTAINER, so that store was always empty: /stats
     reported total_commitments 0 forever, /ledger/export returned [], and
     /verify and /anomaly/{id} could only 404. State now comes from the Kafka
     ring buffers and, optionally, a read-only Cassandra view - which is the
     only state a DMZ observer should ever have.

  2. It ran the anomaly detector on records that, by design, contain no amount,
     passing amount_cents=0. Scoring happens in the prover where the amount
     legitimately exists; the score travels in the published envelope.

Endpoints
---------
  GET  /health              liveness
  GET  /stats               throughput, verification counters, chain head
  GET  /keys                public keys this verifier is checking against
  GET  /transactions        recent committed records
  GET  /anomalies           quarantine queue
  GET  /anomaly/{txn_id}    single record with its verification report
  GET  /chain/verify        walk the received chain and report any break
  POST /verify/opening      check a disclosed opening against a commitment
  POST /resolve/{txn_id}    analyst decision on a quarantined record
  GET  /ledger/export       full zero-PII audit export
  GET  /charts/*            real chart data
  GET  /sidebar/*           real component health
  GET  /stream              SSE feed
"""

import os
import json
import time
import asyncio
import logging
import threading
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from prover.config.settings import settings
from prover.crypto.lwe import LWEPublicKey, open_commitment
from prover.crypto.commitment import compute_chain_hash, GENESIS_HASH
from .verify import ExternalVerifier
from .components.charts import (
    tps_history, anomaly_distribution, graph_proximity,
    benford_chart, extract_benford_counts, commitment_size_distribution,
)
from .components.sidebar import system_status, alert_feed, pipeline_nodes

logger = logging.getLogger("zeroaudit.verifier.api")

app = FastAPI(
    title="ZEROAUDIT Verifier API",
    description="External DMZ verifier - zero PII, zero raw amounts",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

PROVER_URL = os.environ.get("PROVER_URL", "http://prover:8000")

_consumer = None
_verifier = ExternalVerifier()
_reports: dict = {}                 # txn_id -> latest verification report
_reports_lock = threading.Lock()
_prover_keys: dict = {}
_resolutions: dict = {}             # txn_id -> analyst decision


def _record_verification(record: dict):
    """Consumer callback: verify, then retain the report for the API."""
    report = _verifier.verify_envelope(record)
    with _reports_lock:
        if len(_reports) > 5000:
            for key in list(_reports)[:1000]:
                _reports.pop(key, None)
        _reports[record.get("txn_id", "")] = report
    return report


def _fetch_prover_keys(retries: int = 30, delay: float = 3.0):
    """Retrieve the prover's public keys so signatures can be checked.

    Without these the verifier can still check chain continuity and the PII
    assertion, but SIGNATURE reports SKIP rather than PASS - and a verifier
    that cannot check signatures is not verifying authorship at all.
    """
    global _prover_keys
    try:
        import httpx
    except ImportError:
        logger.error("httpx not installed - cannot fetch prover public keys")
        return

    for attempt in range(1, retries + 1):
        try:
            resp = httpx.get("%s/keys" % PROVER_URL, timeout=5.0)
            resp.raise_for_status()
            _prover_keys = resp.json()
            _verifier.set_public_key(_prover_keys.get("ed25519_public_key_b64"))
            logger.info("fetched prover public keys (signing_key_id=%s, lwe=%s)",
                        _prover_keys.get("signing_key_id"),
                        _prover_keys.get("lwe_fingerprint"))
            return
        except Exception as exc:
            logger.warning("prover /keys attempt %d/%d failed: %s",
                           attempt, retries, exc)
            time.sleep(delay)

    logger.error("could not fetch prover keys - signature checks will be skipped")


@app.on_event("startup")
async def startup():
    global _consumer
    threading.Thread(target=_fetch_prover_keys, name="key-fetch", daemon=True).start()
    try:
        from .kafka_client.consumer import get_verifier_consumer
        _consumer = get_verifier_consumer(on_committed=_record_verification)
        _consumer.start()
    except Exception as exc:
        logger.error("verifier consumer unavailable: %s", exc)
    logger.info("verifier API started - external DMZ, 0 bytes PII")


@app.on_event("shutdown")
async def shutdown():
    if _consumer:
        _consumer.stop()


# -- models -------------------------------------------------------------------

class ResolveRequest(BaseModel):
    action: str                      # AUTHORIZE | TERMINATE
    resolved_by: str = "CISO_DASHBOARD"
    note: str = ""


class OpeningRequest(BaseModel):
    """A selective disclosure, as handed over by the bank for one transaction."""
    txn_id: str
    amount_cents: int
    blinding_seed_hex: str
    commitment_b64: Optional[str] = None
    lwe_public_key_b64: Optional[str] = None


# -- helpers ------------------------------------------------------------------

def _committed(n: int = 50) -> list:
    return _consumer.recent_committed(n) if _consumer else []


def _find(txn_id: str) -> Optional[dict]:
    for rec in reversed(_committed(500)):
        if rec.get("txn_id") == txn_id:
            return rec
    return None


# -- core ---------------------------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "zeroaudit-verifier",
        "dmz": True,
        "pii_bytes": 0,
        "consumer_connected": bool(_consumer and _consumer.stats().get("connected")),
        "has_prover_key": _verifier.has_key,
    }


@app.get("/stats")
def stats():
    consumer_stats = _consumer.stats() if _consumer else {}
    v = _verifier.stats()
    records = _committed(500)
    quarantined = sum(1 for r in records if r.get("status") == "QUARANTINED")

    return {
        "tps": consumer_stats.get("tps", 0.0),
        "total_commitments": v.get("records", 0),
        "verified": v.get("verified", 0),
        "failed": v.get("failed", 0),
        "quarantined": quarantined,
        "chain_integrity_pct": v.get("integrity_pct", 0.0),
        "chain_head": v.get("chain_head", GENESIS_HASH),
        "chain_last_seq": v.get("last_seq"),
        "signature_verified": v.get("signature_ok", 0),
        "signature_failed": v.get("signature_failed", 0),
        "signature_unchecked": v.get("signature_unchecked", 0),
        "binding_failed": v.get("binding_failed", 0),
        "chain_broken": v.get("chain_broken", 0),
        "pii_failed": v.get("pii_failed", 0),
        "kafka_lag_records": consumer_stats.get("lag_records", 0),
        "kafka_lag_ms": consumer_stats.get("lag_ms", 0.0),
        "buffer_size": consumer_stats.get("committed_buffer_size", 0),
        "pii_bytes": 0,
    }


@app.get("/keys")
def keys():
    """Public keys this verifier checks against. Public by construction."""
    return {
        "prover": _prover_keys,
        "has_prover_key": _verifier.has_key,
        "expected_lwe_params": {"n": 256, "k": 2, "q": 3329, "eta": 2},
    }


@app.get("/transactions")
def transactions(n: int = 50):
    records = _committed(n)
    with _reports_lock:
        for rec in records:
            report = _reports.get(rec.get("txn_id"))
            rec["verified"] = report["verified"] if report else None
    return records


@app.get("/anomalies")
def anomalies(n: int = 20):
    if not _consumer:
        return []
    queue = _consumer.recent_anomalies(n)
    for rec in queue:
        rec["resolution"] = _resolutions.get(rec.get("txn_id"))
    return queue


@app.get("/anomaly/{txn_id}")
def anomaly_detail(txn_id: str):
    record = _find(txn_id)
    if not record:
        raise HTTPException(status_code=404, detail="TXN %s not in the verifier buffer" % txn_id)

    with _reports_lock:
        report = _reports.get(txn_id)

    hops = 8
    flag = record.get("flag_reason", "NONE")
    if flag in ("OFAC_SANCTION_LIST",):
        hops = 1
    elif flag in ("RBI_FLAG_2024",):
        hops = 2
    elif flag in ("FATF_GREY_LIST",):
        hops = 3

    return {
        **record,
        "verification": report,
        "graph_proximity": graph_proximity(txn_id, record.get("account_hash", ""), hops, flag),
        "resolution": _resolutions.get(txn_id),
        "note": "anomaly scoring happens in the prover, where the amount exists; "
                "the DMZ receives only the resulting score",
    }


@app.get("/chain/verify")
def chain_verify(n: int = 500):
    """Independently walk the received hash chain and report the first break."""
    records = sorted(_committed(n), key=lambda r: r.get("seq", 0))
    if not records:
        return {"intact": True, "checked": 0, "breaks": [], "detail": "no records received yet"}

    breaks = []
    prev_hash = records[0].get("prev_chain_hash", GENESIS_HASH)
    prev_seq = records[0].get("seq", 1) - 1

    for rec in records:
        seq = rec.get("seq", 0)
        if rec.get("prev_chain_hash") != prev_hash:
            breaks.append({"seq": seq, "txn_id": rec.get("txn_id"),
                           "reason": "PREV_HASH_MISMATCH"})
        expected = compute_chain_hash(
            rec.get("prev_chain_hash", ""), rec.get("binding_hash", ""),
            rec.get("txn_id", ""), rec.get("timestamp_ns", 0))
        if expected != rec.get("chain_hash"):
            breaks.append({"seq": seq, "txn_id": rec.get("txn_id"),
                           "reason": "CHAIN_HASH_MISMATCH"})
        if seq != prev_seq + 1:
            breaks.append({"seq": seq, "txn_id": rec.get("txn_id"),
                           "reason": "SEQUENCE_GAP (expected %d)" % (prev_seq + 1)})
        prev_hash = rec.get("chain_hash", "")
        prev_seq = seq

    return {
        "intact": not breaks,
        "checked": len(records),
        "seq_range": [records[0].get("seq"), records[-1].get("seq")],
        "head": prev_hash,
        "breaks": breaks[:20],
    }


@app.post("/verify/opening")
def verify_opening(req: OpeningRequest):
    """Check a selective disclosure: does this amount open this commitment?

    The bank hands over (amount, blinding) for one transaction under audit.
    The verifier recomputes the commitment from the PUBLIC key and compares.
    A bank that misstates the amount cannot produce a blinding factor that
    makes the recomputation match.
    """
    record = _find(req.txn_id)
    commitment = req.commitment_b64 or (record or {}).get("commitment_b64")
    if not commitment:
        raise HTTPException(status_code=404,
                            detail="no commitment on file for %s" % req.txn_id)

    pubkey_b64 = req.lwe_public_key_b64 or _prover_keys.get("lwe_public_key_b64")
    if not pubkey_b64:
        raise HTTPException(status_code=503, detail="prover LWE public key unavailable")

    try:
        pubkey = LWEPublicKey.from_b64(pubkey_b64)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="malformed public key: %s" % exc)

    ok = open_commitment(pubkey, commitment, req.amount_cents, req.blinding_seed_hex)

    return {
        "txn_id": req.txn_id,
        "opening_valid": ok,
        "detail": ("the disclosed amount opens the published commitment"
                   if ok else
                   "MISMATCH - the disclosed amount does not open this commitment"),
        "verified_against": "LWE public key %s" % _prover_keys.get("lwe_fingerprint", "supplied"),
        "trace": [
            {"step": "LOAD_PUBLIC_KEY", "detail": "seed + t, no secret material", "status": "DONE"},
            {"step": "RECOMPUTE", "detail": "u = A^T.r + e1 ; v = <t,r> + e2 + encode(m)",
             "status": "DONE"},
            {"step": "COMPARE", "detail": "constant-time comparison against published commitment",
             "status": "DONE"},
            {"step": "RESULT", "detail": "OPENING VALID" if ok else "OPENING INVALID",
             "status": "VERIFIED" if ok else "FAILED"},
        ],
    }


@app.post("/resolve/{txn_id}")
def resolve(txn_id: str, req: ResolveRequest):
    if req.action not in ("AUTHORIZE", "TERMINATE"):
        raise HTTPException(status_code=400, detail="unknown action: %s" % req.action)
    if not _find(txn_id):
        raise HTTPException(status_code=404, detail="TXN %s not found" % txn_id)

    _resolutions[txn_id] = {
        "action": req.action,
        "resolved_by": req.resolved_by,
        "note": req.note,
        "resolved_at_ns": time.time_ns(),
    }
    logger.info("TXN %s resolved: %s by %s", txn_id, req.action, req.resolved_by)
    return {"txn_id": txn_id, **_resolutions[txn_id], "pii_bytes": 0}


@app.get("/ledger/export")
def export_ledger(n: int = 500):
    records = _committed(n)
    return {
        "records": records,
        "count": len(records),
        "chain": chain_verify(n),
        "keys": {"signing_key_id": _prover_keys.get("signing_key_id"),
                 "lwe_fingerprint": _prover_keys.get("lwe_fingerprint")},
        "pii_bytes": 0,
    }


# -- charts -------------------------------------------------------------------

@app.get("/charts/tps")
def charts_tps(n_seconds: int = 30):
    samples = _consumer.tps_samples(n_seconds) if _consumer else []
    return tps_history(samples=samples, n_bars=n_seconds)


@app.get("/charts/anomaly_dist")
def charts_anomaly_dist(txn_id: Optional[str] = None):
    records = _committed(500)
    scores = [r.get("anomaly_score", 0.0) for r in records if r.get("anomaly_score") is not None]
    highlight = None
    if txn_id:
        rec = _find(txn_id)
        highlight = rec.get("anomaly_score") if rec else None
    return anomaly_distribution(anomaly_scores=scores, highlight_score=highlight)


@app.get("/charts/benford")
def charts_benford():
    return benford_chart(observed_counts=extract_benford_counts(_committed(1000)))


@app.get("/charts/sizes")
def charts_sizes():
    return commitment_size_distribution(records=_committed(500))


# -- sidebar ------------------------------------------------------------------

@app.get("/sidebar/status")
def sidebar_status():
    cs = _consumer.stats() if _consumer else {}
    v = _verifier.stats()
    return system_status(
        kafka_ok=bool(cs.get("connected")),
        sgx_ok=_verifier.has_key,
        intent_engine_ok=v.get("records", 0) > 0,
        chain_ok=chain_verify(500).get("intact", True),
        kafka_lag_ms=cs.get("lag_ms", 0.0),
    )


@app.get("/sidebar/alerts")
def sidebar_alerts(n: int = 10):
    return alert_feed(recent_anomalies=_consumer.recent_anomalies(n) if _consumer else [], n=n)


@app.get("/sidebar/pipeline")
def sidebar_pipeline():
    cs = _consumer.stats() if _consumer else {}
    records = _committed(500)
    sizes = [r.get("size_kb", 0.0) for r in records if r.get("size_kb", 0.0) > 0]
    vstats = _verifier.stats()
    chain = chain_verify(500)
    return pipeline_nodes(
        kafka_lag_ms=cs.get("lag_ms", 0.0),
        kafka_connected=bool(cs.get("connected")),
        has_prover_key=_verifier.has_key,
        lwe_payload_avg_kb=round(sum(sizes) / len(sizes), 2) if sizes else 0.0,
        verified=vstats.get("verified", 0),
        total=vstats.get("records", 0),
        chain_intact=chain.get("intact", True),
    )


# -- SSE ----------------------------------------------------------------------

@app.get("/stream")
async def sse_stream():
    """Live feed of real committed records and real stats."""
    async def generator():
        seen = set()
        while True:
            try:
                for rec in _committed(100):
                    txn_id = rec.get("txn_id")
                    if txn_id and txn_id not in seen:
                        seen.add(txn_id)
                        yield "data: %s\n\n" % json.dumps({**rec, "event": "transaction"})
                if len(seen) > 5000:
                    seen = set(list(seen)[-1000:])

                yield "data: %s\n\n" % json.dumps({"event": "stats", **stats()})
                await asyncio.sleep(1)
            except Exception as exc:
                logger.error("SSE error: %s", exc)
                await asyncio.sleep(2)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format=settings.LOG_FORMAT,
    )
    uvicorn.run(app, host=settings.API_HOST, port=int(os.environ.get("VERIFIER_PORT", "8001")))
