"""
commitment.py — Commitment Store & Batch Operations
ZEROAUDIT Prover Service

Wraps lwe.py into a production-ready commitment pipeline:
  - CommitmentStore: in-memory + Cassandra-backed ledger
  - batch_commit(): process a list of transactions
  - audit_export(): export ledger for external verifier (zero PII)
"""

import time
import uuid
import json
import hashlib
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional
from .lwe import commit, verify, get_keypair, get_master_key

logger = logging.getLogger("zeroaudit.commitment")


@dataclass
class CommitmentRecord:
    txn_id: str
    commitment_b64: str
    binding_hash: str
    size_kb: float
    lwe_params: dict
    timestamp_ns: int
    pii_bytes: int = 0
    account_hash: str = ""     # SHA3-256(account_id) — never raw
    txn_type: str = ""
    status: str = "PENDING"    # PENDING | VERIFIED | QUARANTINED | REJECTED
    anomaly_score: float = 0.0

    def to_export_dict(self) -> dict:
        """Safe export — zero PII."""
        return {
            "txn_id": self.txn_id,
            "binding_hash": self.binding_hash,
            "size_kb": self.size_kb,
            "lwe_params": self.lwe_params,
            "timestamp_ns": self.timestamp_ns,
            "pii_bytes": self.pii_bytes,
            "account_hash": self.account_hash,
            "txn_type": self.txn_type,
            "status": self.status,
            "anomaly_score": self.anomaly_score,
        }


class CommitmentStore:
    """
    In-memory commitment ledger with Cassandra write-through.
    For production: replace _store with cassandra-driver session.
    """

    def __init__(self, cassandra_session=None):
        self._store: dict[str, CommitmentRecord] = {}
        self._session = cassandra_session
        self._keypair = get_keypair()
        self._master_key = get_master_key()

        if cassandra_session:
            self._prepare_statements()

    def _prepare_statements(self):
        self._insert_stmt = self._session.prepare("""
            INSERT INTO zeroaudit.commitments (
                txn_id, binding_hash, commitment_b64, size_kb,
                lwe_params, timestamp_ns, pii_bytes,
                account_hash, txn_type, status, anomaly_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """)

    def add(
        self,
        txn_id: str,
        amount_cents: int,
        account_id: str,
        txn_type: str,
        anomaly_score: float = 0.0,
    ) -> CommitmentRecord:
        """Generate and store a new LWE commitment for a transaction."""

        # Hash account_id — no raw PII stored
        account_hash = hashlib.sha3_256(account_id.encode()).hexdigest()

        # Generate LWE commitment
        lwe_result = commit(
            keypair=self._keypair,
            amount_cents=amount_cents,
            txn_id=txn_id,
            master_key=self._master_key,
        )

        status = "QUARANTINED" if anomaly_score > 0.75 else "VERIFIED"

        record = CommitmentRecord(
            txn_id=txn_id,
            commitment_b64=lwe_result["commitment_b64"],
            binding_hash=lwe_result["binding_hash"],
            size_kb=lwe_result["size_kb"],
            lwe_params=lwe_result["lwe_params"],
            timestamp_ns=lwe_result["timestamp_ns"],
            pii_bytes=0,
            account_hash=account_hash,
            txn_type=txn_type,
            status=status,
            anomaly_score=anomaly_score,
        )

        self._store[txn_id] = record
        logger.info(f"Committed {txn_id} [{status}] size={record.size_kb}KB")

        if self._session:
            self._write_cassandra(record)

        return record

    def _write_cassandra(self, record: CommitmentRecord):
        try:
            self._session.execute(self._insert_stmt, (
                record.txn_id,
                record.binding_hash,
                record.commitment_b64,
                record.size_kb,
                json.dumps(record.lwe_params),
                record.timestamp_ns,
                record.pii_bytes,
                record.account_hash,
                record.txn_type,
                record.status,
                record.anomaly_score,
            ))
        except Exception as e:
            logger.error(f"Cassandra write failed for {record.txn_id}: {e}")

    def get(self, txn_id: str) -> Optional[CommitmentRecord]:
        return self._store.get(txn_id)

    def verify_txn(
        self,
        txn_id: str,
        amount_cents: int,
    ) -> dict:
        """Run full LWE verification for a transaction ID."""
        record = self._store.get(txn_id)
        if not record:
            return {
                "verified": False,
                "error": f"TXN {txn_id} not found in ledger",
                "trace": [],
            }

        result = verify(
            keypair=self._keypair,
            commitment_record=record.to_export_dict(),
            amount_cents=amount_cents,
            txn_id=txn_id,
            master_key=self._master_key,
        )
        return result

    def quarantine(self, txn_id: str) -> bool:
        if txn_id in self._store:
            self._store[txn_id].status = "QUARANTINED"
            return True
        return False

    def authorize(self, txn_id: str) -> bool:
        if txn_id in self._store:
            self._store[txn_id].status = "VERIFIED"
            return True
        return False

    def reject(self, txn_id: str) -> bool:
        if txn_id in self._store:
            self._store[txn_id].status = "REJECTED"
            return True
        return False

    def audit_export(self) -> list[dict]:
        """Export full ledger — zero PII."""
        return [r.to_export_dict() for r in self._store.values()]

    def stats(self) -> dict:
        records = list(self._store.values())
        return {
            "total": len(records),
            "verified": sum(1 for r in records if r.status == "VERIFIED"),
            "quarantined": sum(1 for r in records if r.status == "QUARANTINED"),
            "rejected": sum(1 for r in records if r.status == "REJECTED"),
            "pending": sum(1 for r in records if r.status == "PENDING"),
            "chain_integrity_pct": round(
                100 * sum(1 for r in records if r.status in ("VERIFIED",)) / max(len(records), 1), 1
            ),
        }


def batch_commit(
    transactions: list[dict],
    store: CommitmentStore,
) -> list[CommitmentRecord]:
    """
    Process a batch of raw transactions through the commitment pipeline.

    Each transaction dict must have:
      - txn_id: str
      - amount_cents: int
      - account_id: str
      - txn_type: str
      - anomaly_score: float (0.0–1.0, from anomaly detector)
    """
    results = []
    for txn in transactions:
        try:
            record = store.add(
                txn_id=txn["txn_id"],
                amount_cents=txn["amount_cents"],
                account_id=txn["account_id"],
                txn_type=txn["txn_type"],
                anomaly_score=txn.get("anomaly_score", 0.0),
            )
            results.append(record)
        except Exception as e:
            logger.error(f"batch_commit failed for {txn.get('txn_id')}: {e}")
    return results


# ── Module-level singleton ─────────────────────────────────────────────────────

_store_instance: CommitmentStore = None


def get_store(cassandra_session=None) -> CommitmentStore:
    global _store_instance
    if _store_instance is None:
        _store_instance = CommitmentStore(cassandra_session)
    return _store_instance