"""
commitment.py - Commitment ledger and hash chain
ZEROAUDIT Prover Service

Wraps lwe.py into the ingest pipeline and adds the two properties that make
the published ledger auditable rather than merely private:

  signed    every record carries an Ed25519 signature produced inside the
            prover. The verifier holds only the public key, so it can
            establish authorship without any shared secret.

  chained   every record commits to its predecessor:

                chain_hash_i = SHA3-256(
                    chain_hash_{i-1} || binding_hash_i || txn_id_i || ts_i
                )

            and the signature covers chain_hash_i. Altering, reordering,
            inserting, or dropping any record breaks every link after it,
            and re-chaining is impossible without the signing key. That is
            what "tamper-evident" has to mean to be worth claiming.

Storage is two-tier: a bounded in-memory ring for hot reads, and Cassandra
for durability. The ring is bounded on purpose - an unbounded dict on a
service ingesting continuously is a memory leak with a long fuse.
"""

import json
import time
import hashlib
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional

from .lwe import (
    commit, verify, get_keypair, get_master_key, derive_randomness,
    N, K, Q, ETA, AMOUNT_BITS,
)
from .signature import get_signing_key, sign_commitment

logger = logging.getLogger("zeroaudit.commitment")

GENESIS_HASH = "00" * 32
HOT_RING_SIZE = 5_000


def compute_chain_hash(prev_chain_hash: str, binding_hash: str,
                       txn_id: str, timestamp_ns: int) -> str:
    """One link of the ledger hash chain."""
    h = hashlib.sha3_256()
    h.update(prev_chain_hash.encode())
    h.update(binding_hash.encode())
    h.update(txn_id.encode())
    h.update(str(timestamp_ns).encode())
    return h.hexdigest()


@dataclass
class CommitmentRecord:
    txn_id: str
    commitment_b64: str
    binding_hash: str
    size_kb: float
    lwe_params: dict
    timestamp_ns: int
    seq: int = 0
    prev_chain_hash: str = GENESIS_HASH
    chain_hash: str = ""
    signature_b64: str = ""
    signing_key_id: str = ""
    sig_alg: str = "Ed25519"
    pubkey_fingerprint: str = ""
    pii_bytes: int = 0
    account_hash: str = ""          # SHA3-256(account_id) - never the raw ID
    txn_type: str = ""
    status: str = "PENDING"         # PENDING | VERIFIED | QUARANTINED | REJECTED
    anomaly_score: float = 0.0
    flag_reason: str = "NONE"
    novelty_score: float = 0.0      # autoencoder component of anomaly_score
    typology_score: float = 0.0     # rules-layer component of anomaly_score

    def to_export_dict(self) -> dict:
        """Zero-PII export. This is exactly what goes on the public Kafka topic.

        It includes the commitment itself - not just its hash - because an
        auditor who receives only a hash has nothing to verify against. The
        commitment is hiding: publishing it reveals no amount.
        """
        return {
            "txn_id": self.txn_id,
            "seq": self.seq,
            "commitment_b64": self.commitment_b64,
            "binding_hash": self.binding_hash,
            "prev_chain_hash": self.prev_chain_hash,
            "chain_hash": self.chain_hash,
            "signature_b64": self.signature_b64,
            "signing_key_id": self.signing_key_id,
            "sig_alg": self.sig_alg,
            "pubkey_fingerprint": self.pubkey_fingerprint,
            "size_kb": self.size_kb,
            "lwe_params": self.lwe_params,
            "timestamp_ns": self.timestamp_ns,
            "pii_bytes": self.pii_bytes,
            "account_hash": self.account_hash,
            "txn_type": self.txn_type,
            "status": self.status,
            "anomaly_score": self.anomaly_score,
            "flag_reason": self.flag_reason,
            "novelty_score": self.novelty_score,
            "typology_score": self.typology_score,
        }


class CommitmentStore:
    """Append-only commitment ledger: LWE commit, sign, chain, persist."""

    def __init__(self, ledger=None, ring_size: int = HOT_RING_SIZE):
        self._hot: "OrderedDict[str, CommitmentRecord]" = OrderedDict()
        self._ring_size = ring_size
        self._ledger = ledger                 # CassandraLedger or None
        self._lock = threading.RLock()

        self._keypair = get_keypair()
        self._master_key = get_master_key()
        self._signing_key = get_signing_key()

        self._seq = 0
        self._head = GENESIS_HASH
        self._counts = {"VERIFIED": 0, "QUARANTINED": 0, "REJECTED": 0, "PENDING": 0}
        self._total = 0

        self._resume_chain()

    # -- chain bootstrap ------------------------------------------------------

    def _resume_chain(self):
        """Pick the chain back up where the last run left it, if durable."""
        if not self._ledger:
            return
        head = self._ledger.load_head()
        if head:
            self._seq, self._head = head
            logger.info("Resumed hash chain at seq=%d head=%s...",
                        self._seq, self._head[:16])
        else:
            logger.info("No persisted chain head - starting from genesis")

    # -- write path -----------------------------------------------------------

    def add(
        self,
        txn_id: str,
        amount_cents: int,
        account_id: str,
        txn_type: str,
        anomaly_score: float = 0.0,
        flag_reason: str = "NONE",
        threshold: float = 0.75,
        novelty_score: float = 0.0,
        typology_score: float = 0.0,
    ) -> CommitmentRecord:
        """Commit one transaction: LWE commit -> chain -> sign -> persist."""
        account_hash = hashlib.sha3_256(account_id.encode()).hexdigest()
        lwe_result = commit(
            keypair=self._keypair,
            amount_cents=amount_cents,
            txn_id=txn_id,
            master_key=self._master_key,
        )

        status = "QUARANTINED" if anomaly_score >= threshold else "VERIFIED"

        with self._lock:
            prev = self._head
            seq = self._seq + 1
            chain_hash = compute_chain_hash(
                prev, lwe_result["binding_hash"], txn_id, lwe_result["timestamp_ns"]
            )
            self._head = chain_hash
            self._seq = seq

            envelope = sign_commitment(
                key=self._signing_key,
                txn_id=txn_id,
                binding_hash=lwe_result["binding_hash"],
                timestamp_ns=lwe_result["timestamp_ns"],
                chain_hash=chain_hash,
            )

            record = CommitmentRecord(
                txn_id=txn_id,
                commitment_b64=lwe_result["commitment_b64"],
                binding_hash=lwe_result["binding_hash"],
                size_kb=lwe_result["size_kb"],
                lwe_params=lwe_result["lwe_params"],
                timestamp_ns=lwe_result["timestamp_ns"],
                seq=seq,
                prev_chain_hash=prev,
                chain_hash=chain_hash,
                signature_b64=envelope["signature_b64"],
                signing_key_id=envelope["signing_key_id"],
                sig_alg=envelope["sig_alg"],
                pubkey_fingerprint=lwe_result["pubkey_fingerprint"],
                pii_bytes=0,
                account_hash=account_hash,
                txn_type=txn_type,
                status=status,
                anomaly_score=round(float(anomaly_score), 4),
                flag_reason=flag_reason,
                novelty_score=round(float(novelty_score), 4),
                typology_score=round(float(typology_score), 4),
            )

            self._hot[txn_id] = record
            while len(self._hot) > self._ring_size:
                self._hot.popitem(last=False)

            self._total += 1
            self._counts[status] = self._counts.get(status, 0) + 1

        if self._ledger:
            self._ledger.write(record.to_export_dict())
            self._ledger.save_head(seq, chain_hash)

        logger.debug("committed %s seq=%d [%s] %.2fKB", txn_id, seq, status, record.size_kb)
        return record

    # -- read path ------------------------------------------------------------

    def get(self, txn_id: str) -> Optional[CommitmentRecord]:
        with self._lock:
            rec = self._hot.get(txn_id)
        if rec:
            return rec
        if self._ledger:
            row = self._ledger.get(txn_id)
            if row:
                return self._record_from_row(row)
        return None

    @staticmethod
    def _record_from_row(row: dict) -> CommitmentRecord:
        return CommitmentRecord(
            txn_id=row.get("txn_id", ""),
            commitment_b64=row.get("commitment_b64", ""),
            binding_hash=row.get("binding_hash", ""),
            size_kb=float(row.get("size_kb") or 0.0),
            lwe_params=row.get("lwe_params") or {},
            timestamp_ns=int(row.get("timestamp_ns") or 0),
            seq=int(row.get("seq") or 0),
            prev_chain_hash=row.get("prev_chain_hash") or GENESIS_HASH,
            chain_hash=row.get("chain_hash") or "",
            signature_b64=row.get("signature_b64") or "",
            signing_key_id=row.get("signing_key_id") or "",
            pubkey_fingerprint=row.get("pubkey_fingerprint") or "",
            pii_bytes=int(row.get("pii_bytes") or 0),
            account_hash=row.get("account_hash") or "",
            txn_type=row.get("txn_type") or "",
            status=row.get("status") or "PENDING",
            anomaly_score=float(row.get("anomaly_score") or 0.0),
            flag_reason=row.get("flag_reason") or "NONE",
            novelty_score=float(row.get("novelty_score") or 0.0),
            typology_score=float(row.get("typology_score") or 0.0),
        )

    def audit_export(self, limit: int = 500) -> list:
        """Newest-first ledger export. Durable tier wins when it is available."""
        if self._ledger and self._ledger.available:
            rows = self._ledger.recent(limit=limit)
            if rows:
                return rows
        with self._lock:
            recs = list(self._hot.values())
        recs.sort(key=lambda r: r.seq, reverse=True)
        return [r.to_export_dict() for r in recs[:limit]]

    # -- opening / selective disclosure ---------------------------------------

    def opening_for(self, txn_id: str, amount_cents: int) -> Optional[dict]:
        """Produce the disclosure an auditor needs to open one commitment.

        Reveals the blinding factor for a single transaction and nothing else.
        Every other record in the ledger stays sealed.
        """
        record = self.get(txn_id)
        if not record:
            return None
        return {
            "txn_id": txn_id,
            "amount_cents": amount_cents,
            "blinding_seed_hex": derive_randomness(self._master_key, txn_id).hex(),
            "commitment_b64": record.commitment_b64,
            "binding_hash": record.binding_hash,
            "lwe_public_key_b64": self._keypair.public_key_b64(),
            "pubkey_fingerprint": self._keypair.fingerprint(),
        }

    def public_keys(self) -> dict:
        """Everything an external verifier needs, and nothing it should not have."""
        return {
            "lwe_public_key_b64": self._keypair.public_key_b64(),
            "lwe_fingerprint": self._keypair.fingerprint(),
            "ed25519_public_key_b64": self._signing_key.public_key_b64(),
            "signing_key_id": self._signing_key.key_id(),
            "lwe_params": {"n": N, "k": K, "q": Q, "eta": ETA, "amount_bits": AMOUNT_BITS},
        }

    def verify_txn(self, txn_id: str, amount_cents: int) -> dict:
        """Full LWE recomputation trace for one transaction."""
        record = self.get(txn_id)
        if not record:
            return {
                "verified": False,
                "error": "TXN %s not found in ledger" % txn_id,
                "trace": [],
            }
        return verify(
            keypair=self._keypair,
            commitment_record=record.to_export_dict(),
            amount_cents=amount_cents,
            txn_id=txn_id,
            master_key=self._master_key,
        )

    def verify_chain(self, limit: int = 500) -> dict:
        """Walk the hash chain oldest-first and report the first break."""
        records = sorted(self.audit_export(limit=limit), key=lambda r: r.get("seq", 0))
        if not records:
            return {"intact": True, "checked": 0, "breaks": [], "detail": "empty ledger"}

        breaks = []
        prev_hash = records[0].get("prev_chain_hash", GENESIS_HASH)
        prev_seq = records[0].get("seq", 1) - 1

        for rec in records:
            seq = rec.get("seq", 0)
            if rec.get("prev_chain_hash") != prev_hash:
                breaks.append({
                    "seq": seq, "txn_id": rec.get("txn_id"),
                    "reason": "PREV_HASH_MISMATCH",
                })
            expected = compute_chain_hash(
                rec.get("prev_chain_hash", ""), rec.get("binding_hash", ""),
                rec.get("txn_id", ""), rec.get("timestamp_ns", 0),
            )
            if expected != rec.get("chain_hash"):
                breaks.append({
                    "seq": seq, "txn_id": rec.get("txn_id"),
                    "reason": "CHAIN_HASH_RECOMPUTE_MISMATCH",
                })
            if seq != prev_seq + 1:
                breaks.append({
                    "seq": seq, "txn_id": rec.get("txn_id"),
                    "reason": "SEQUENCE_GAP (expected %d)" % (prev_seq + 1),
                })
            prev_hash = rec.get("chain_hash", "")
            prev_seq = seq

        return {
            "intact": not breaks,
            "checked": len(records),
            "range": [records[0].get("seq"), records[-1].get("seq")],
            "breaks": breaks[:20],
            "head": prev_hash,
        }

    # -- status transitions ---------------------------------------------------

    def _set_status(self, txn_id: str, status: str) -> bool:
        with self._lock:
            rec = self._hot.get(txn_id)
            if rec:
                self._counts[rec.status] = max(self._counts.get(rec.status, 1) - 1, 0)
                rec.status = status
                self._counts[status] = self._counts.get(status, 0) + 1
        persisted = self._ledger.set_status(txn_id, status) if self._ledger else False
        return bool(rec) or persisted

    def quarantine(self, txn_id: str) -> bool:
        return self._set_status(txn_id, "QUARANTINED")

    def authorize(self, txn_id: str) -> bool:
        return self._set_status(txn_id, "VERIFIED")

    def reject(self, txn_id: str) -> bool:
        return self._set_status(txn_id, "REJECTED")

    # -- metrics --------------------------------------------------------------

    def stats(self) -> dict:
        with self._lock:
            counts = dict(self._counts)
            total = self._total
            seq = self._seq
            head = self._head
            hot = len(self._hot)

        durable = 0
        if self._ledger and self._ledger.available:
            durable = self._ledger.count()

        settled = counts.get("VERIFIED", 0) + counts.get("QUARANTINED", 0) + counts.get("REJECTED", 0)
        return {
            "total": total,
            "verified": counts.get("VERIFIED", 0),
            "quarantined": counts.get("QUARANTINED", 0),
            "rejected": counts.get("REJECTED", 0),
            "pending": counts.get("PENDING", 0),
            "hot_ring": hot,
            "durable_rows": durable,
            "chain_seq": seq,
            "chain_head": head,
            "chain_integrity_pct": round(100 * counts.get("VERIFIED", 0) / max(settled, 1), 1),
        }


def batch_commit(transactions: list, store: "CommitmentStore") -> list:
    """Commit a batch of raw transactions.

    Each dict needs: txn_id, amount_cents, account_id, txn_type,
    and optionally anomaly_score and flag_reason.
    """
    results = []
    for txn in transactions:
        try:
            results.append(store.add(
                txn_id=txn["txn_id"],
                amount_cents=txn["amount_cents"],
                account_id=txn["account_id"],
                txn_type=txn["txn_type"],
                anomaly_score=txn.get("anomaly_score", 0.0),
                flag_reason=txn.get("flag_reason", "NONE"),
            ))
        except Exception as exc:
            logger.error("batch_commit failed for %s: %s", txn.get("txn_id"), exc)
    return results


# -- process-wide singleton ---------------------------------------------------

_store_instance: Optional[CommitmentStore] = None
_store_lock = threading.Lock()


def get_store(ledger=None, use_cassandra: bool = None) -> CommitmentStore:
    """Return the process-wide commitment store.

    `use_cassandra` defaults to the CASSANDRA_ENABLED env var. The verifier
    leaves it off: it is a read-only DMZ observer and must never hold a write
    handle to the bank's ledger.
    """
    global _store_instance
    with _store_lock:
        if _store_instance is None:
            if ledger is None and use_cassandra is None:
                import os
                use_cassandra = os.environ.get("CASSANDRA_ENABLED", "false").lower() == "true"
            if ledger is None and use_cassandra:
                from ..cassandra_store import get_ledger
                ledger = get_ledger()
            _store_instance = CommitmentStore(ledger=ledger)
    return _store_instance
