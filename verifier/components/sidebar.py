"""
verifier/components/sidebar.py — Sidebar State & Navigation Data
ZEROAUDIT Verifier Service

Produces the sidebar/status data consumed by the React dashboard:
  - system_status()    → health of all pipeline components
  - alert_feed()       → recent alerts sorted by severity (real anomalies only)
  - pipeline_nodes()   → topology node states for the pipeline diagram

Zero fake/random data anywhere. If real data is unavailable, returns
empty or zero-state structures — never simulated values.
"""

import time
from typing import List, Dict, Any, Optional


# ── System Status ─────────────────────────────────────────────────────────────

def system_status(
    kafka_ok: bool = True,
    sgx_ok: bool = True,
    intent_engine_ok: bool = True,
    chain_ok: bool = True,
    kafka_lag_ms: float = 0.0,
) -> Dict[str, Any]:
    """
    Returns health status for the components a DMZ observer can actually see.

    Earlier versions of this function reported a Cassandra component that was
    always hardcoded ONLINE (the verifier has no connection to Cassandra and
    never did - that store lives on the prover side, on the other side of the
    enclave boundary this whole system is built around), a "ZKP GEN" component
    that was unconditionally ONLINE regardless of whether the chain actually
    verified, and an S3/Parquet vault component describing infrastructure that
    does not exist anywhere in this codebase. All three were fabricated.

    What remains is exactly what the verifier can check without trusting
    anyone: is Kafka connected, does it hold a prover public key, has the
    intent engine actually scored anything, and does the hash chain it has
    independently recomputed still verify.
    """
    components = [
        {
            "id": "kafka",
            "label": "KAFKA",
            "status": "ONLINE" if kafka_ok else "DEGRADED",
            "color": "#00e5ff" if kafka_ok else "#ffd700",
            "detail": f"Lag {kafka_lag_ms:.0f}ms" if kafka_ok else "Disconnected",
        },
        {
            "id": "sgx_enclave",
            "label": "SGX ENCLAVE",
            "status": "ONLINE" if sgx_ok else "FAULT",
            "color": "#00ff88" if sgx_ok else "#ff0033",
            "detail": "Prover public key held" if sgx_ok else "No key exchanged yet",
        },
        {
            "id": "intent_engine",
            "label": "INTENT ENGINE",
            "status": "ONLINE" if intent_engine_ok else "DEGRADED",
            "color": "#ffd700" if intent_engine_ok else "#ff6b35",
            "detail": "Scoring transactions" if intent_engine_ok else "No records scored yet",
        },
        {
            "id": "hash_chain",
            "label": "HASH CHAIN",
            "status": "ONLINE" if chain_ok else "FAULT",
            "color": "#00e5ff" if chain_ok else "#ff0033",
            "detail": "Independently verified" if chain_ok else "BROKEN — see /chain/verify",
        },
    ]

    all_ok = all(c["status"] == "ONLINE" for c in components)
    return {
        "components": components,
        "overall": "NOMINAL" if all_ok else "DEGRADED",
        "timestamp_ns": time.time_ns(),
        "pii_bytes": 0,
    }


# ── Alert Feed ────────────────────────────────────────────────────────────────

def alert_feed(
    recent_anomalies: List[Dict],
    n: int = 10,
) -> List[Dict[str, Any]]:
    """
    Generate alert feed from real anomaly records only.
    If recent_anomalies is empty, returns empty list — never fake alerts.
    All severity/color assignments are deterministic from real anomaly_score.
    """
    if not recent_anomalies:
        return []

    alerts = []
    for rec in recent_anomalies[:n]:
        flag = rec.get("flag_reason", "HIGH_ANOMALY_SCORE")
        score = rec.get("anomaly_score", 0.0)

        # Deterministic severity from real score
        if score >= 0.90:
            severity = "CRITICAL"
            color = "#ff0033"
        elif score >= 0.75:
            severity = "HIGH"
            color = "#ff6b35"
        elif score >= 0.50:
            severity = "MEDIUM"
            color = "#ffd700"
        else:
            severity = "LOW"
            color = "#00e5ff"

        txn_id = rec.get("txn_id", "UNKNOWN")
        alerts.append({
            "id": txn_id,
            "severity": severity,
            "color": color,
            "message": f"{flag}: {txn_id[-12:]}",
            "timestamp_ns": rec.get("timestamp_ns", time.time_ns()),
            "txn_id": txn_id,
            "anomaly_score": score,
        })

    _SEV = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
    return sorted(alerts, key=lambda a: (_SEV.get(a["severity"], 9), -a["timestamp_ns"]))


# ── Pipeline Node States ──────────────────────────────────────────────────────

def pipeline_nodes(
    kafka_lag_ms: float = 0.0,
    kafka_connected: bool = False,
    has_prover_key: bool = False,
    lwe_payload_avg_kb: float = 0.0,
    verified: int = 0,
    total: int = 0,
    chain_intact: bool = True,
) -> List[Dict[str, Any]]:
    """
    Returns pipeline topology node states for the diagram, restricted to what
    a DMZ observer can legitimately measure.

    This used to describe a Postgres -> Debezium CDC -> ... -> S3 Parquet
    pipeline that does not exist in this system (there is no Postgres, no
    Debezium, no S3 vault - the real path is Kafka -> prover -> Kafka), and
    it reported a Cassandra write rate that was actually Kafka consumer
    throughput divided by 1000. Both were fabricated.

    The verifier is deliberately walled off from Cassandra and from the
    prover's internals - that isolation is the point of the DMZ design, not
    an oversight to paper over. So this reports only what the verifier
    itself observes: the public Kafka topics it consumes, whether it holds
    the prover's public key, and its own independent verification tally.
    Enclave-internal stages (signing, LWE commitment, the hash chain) are
    real, but happen behind a boundary this service cannot instrument -
    the caller renders them as a static architecture diagram, not as a
    live-metriced node, so the UI never implies visibility that isn't there.
    """
    return [
        {
            "id": "kafka",
            "short": "MQ",
            "label": "KAFKA\nCOMMITTED + ANOMALIES",
            "sublabel": f"lag {kafka_lag_ms:.0f}ms" if kafka_connected else "disconnected",
            "status": "active" if kafka_connected and kafka_lag_ms < 500 else "warning",
            "color": "#00e5ff" if kafka_connected and kafka_lag_ms < 500 else "#ffd700",
        },
        {
            "id": "prover_key",
            "short": "KEY",
            "label": "PROVER\nPUBLIC KEY",
            "sublabel": "fetched at boot" if has_prover_key else "unavailable",
            "status": "active" if has_prover_key else "warning",
            "color": "#00e5ff" if has_prover_key else "#ffd700",
        },
        {
            "id": "verifier",
            "short": "VER",
            "label": "VERIFIER\n5 INDEPENDENT CHECKS",
            "sublabel": f"{verified}/{total} verified" if total else "awaiting records",
            "status": "active" if (total == 0 or verified == total) else "warning",
            "color": "#00e5ff" if (total == 0 or verified == total) else "#ff6b35",
        },
        {
            "id": "chain",
            "short": "CHAIN",
            "label": "HASH CHAIN\nCONTINUITY",
            "sublabel": "intact" if chain_intact else "BROKEN",
            "status": "active" if chain_intact else "fault",
            "color": "#00e5ff" if chain_intact else "#ff0033",
        },
        {
            "id": "commitments",
            "short": "LWE",
            "label": "COMMITMENT\nSIZE",
            "sublabel": f"avg {lwe_payload_avg_kb:.1f}KB" if lwe_payload_avg_kb > 0 else "awaiting data",
            "status": "active",
            "color": "#00e5ff",
        },
    ]