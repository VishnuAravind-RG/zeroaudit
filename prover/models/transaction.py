"""
transaction.py — Transaction Data Models
ZEROAUDIT Prover Service

Raw transaction (from Cassandra LSM / Kafka) → ProverTransaction (internal)
No PII fields survive beyond this boundary — all are hashed or dropped.
"""

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class TxnType(str, Enum):
    RTGS = "RTGS"
    NEFT = "NEFT"
    WIRE_TRANSFER = "WIRE_TRANSFER"
    TRADE_SETTLEMENT = "TRADE_SETTLEMENT"
    INTERNAL_TRANSFER = "INTERNAL_TRANSFER"
    FX_CONVERSION = "FX_CONVERSION"


class TxnStatus(str, Enum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    QUARANTINED = "QUARANTINED"
    REJECTED = "REJECTED"
    PROCESSING = "PROCESSING"


@dataclass
class RawTransaction:
    """
    As received from Cassandra CDC / Kafka topic.
    Contains PII — must NOT leave this class unredacted.
    """
    txn_id: str
    account_id: str          # PII — hashed before storage
    counterparty_id: str     # PII — hashed before storage
    amount_cents: int        # Sensitive — committed via LWE, never stored raw
    currency: str
    txn_type: str
    timestamp_ns: int
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_kafka_msg(cls, msg: dict) -> "RawTransaction":
        """Parse from Kafka message payload."""
        return cls(
            txn_id=msg.get("txn_id") or str(uuid.uuid4()),
            account_id=msg.get("account_id", ""),
            counterparty_id=msg.get("counterparty_id", ""),
            amount_cents=int(msg.get("amount_cents", 0)),
            currency=msg.get("currency", "INR"),
            txn_type=msg.get("txn_type", TxnType.RTGS),
            timestamp_ns=msg.get("timestamp_ns") or time.time_ns(),
            metadata=msg.get("metadata", {}),
        )

    def to_prover_transaction(self) -> "ProverTransaction":
        """Redact PII and produce prover-safe record."""
        return ProverTransaction(
            txn_id=self.txn_id,
            account_hash=hashlib.sha3_256(self.account_id.encode()).hexdigest(),
            counterparty_hash=hashlib.sha3_256(self.counterparty_id.encode()).hexdigest(),
            amount_cents=self.amount_cents,   # consumed by LWE, then dropped
            currency=self.currency,
            txn_type=self.txn_type,
            timestamp_ns=self.timestamp_ns,
        )


@dataclass
class ProverTransaction:
    """
    Prover-internal representation.
    amount_cents is held only during commitment generation, then cleared.
    """
    txn_id: str
    account_hash: str        # SHA3-256(account_id)
    counterparty_hash: str   # SHA3-256(counterparty_id)
    amount_cents: int        # TEMPORARY — cleared after commit()
    currency: str
    txn_type: str
    timestamp_ns: int
    status: TxnStatus = TxnStatus.PENDING
    anomaly_score: float = 0.0
    commitment_binding: Optional[str] = None
    signature_b64: Optional[str] = None

    def clear_sensitive(self):
        """Zero out amount after commitment is generated."""
        self.amount_cents = 0

    def to_ledger_record(self) -> dict:
        """Export-safe dict — zero PII, zero raw amounts."""
        return {
            "txn_id": self.txn_id,
            "account_hash": self.account_hash,
            "counterparty_hash": self.counterparty_hash,
            "currency": self.currency,
            "txn_type": self.txn_type,
            "timestamp_ns": self.timestamp_ns,
            "status": self.status,
            "anomaly_score": self.anomaly_score,
            "commitment_binding": self.commitment_binding,
            "signature_b64": self.signature_b64,
            "pii_bytes": 0,
        }


@dataclass
class AnomalyFlag:
    """Produced by the ONNX FP16 Isolation Forest model."""
    txn_id: str
    anomaly_score: float           # 0.0 (normal) → 1.0 (extreme outlier)
    reconstruction_loss: float     # model's raw output
    benford_deviation: float       # deviation from Benford's Law
    graph_hops_to_blacklist: int   # hops to nearest OFAC/RBI flagged entity
    flag_reason: str               # e.g. "OFAC_SANCTION_LIST", "RBI_FLAG_2024"
    behavioral_delta: dict = field(default_factory=dict)
    # e.g. {"expected": "macOS/Mumbai/10AM", "actual": "Windows/Cayman/3AM"}


@dataclass
class VerifiedBatch:
    """Result of processing a Kafka batch through the full pipeline."""
    batch_id: str
    total: int
    committed: int
    quarantined: int
    rejected: int
    processing_time_ms: float
    tps: float
    timestamp_ns: int = field(default_factory=time.time_ns)

    def summary(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "total": self.total,
            "committed": self.committed,
            "quarantined": self.quarantined,
            "rejected": self.rejected,
            "processing_time_ms": round(self.processing_time_ms, 2),
            "tps": round(self.tps, 1),
            "chain_integrity_pct": round(100 * self.committed / max(self.total, 1), 1),
        }