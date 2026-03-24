"""
test_integration.py — Full Pipeline Integration Tests
ZEROAUDIT

Tests the end-to-end flow:
  RawTransaction → ProverTransaction → LWE Commit → Sign → Verify
Without requiring Kafka or Cassandra (all in-process).

Run: pytest tests/test_integration.py -v
"""

import pytest
import time
import os
from prover.models.transaction import RawTransaction, TxnType
from prover.crypto.commitment import CommitmentStore
from prover.crypto.signature import SigningKey, sign_commitment, verify_signature
from prover.crypto.lwe import get_keypair, get_master_key, commit, verify
from verifier.anomaly_detector import AnomalyDetector
from verifier.verify import ExternalVerifier


class TestFullPipeline:
    """
    End-to-end: raw transaction → commit → sign → verify (no infra required)
    """

    def setup_method(self):
        self.store = CommitmentStore()
        self.signing_key = SigningKey()
        self.detector = AnomalyDetector()
        self.verifier = ExternalVerifier()

    def _make_raw_txn(self, txn_id="TXN-INTEG-001", amount=500000, txn_type="RTGS"):
        return RawTransaction(
            txn_id=txn_id,
            account_id="ACC-INTEG-TEST",
            counterparty_id="ACC-INTEG-COUNTER",
            amount_cents=amount,
            currency="INR",
            txn_type=txn_type,
            timestamp_ns=time.time_ns(),
        )

    def test_pii_redaction_on_conversion(self):
        raw = self._make_raw_txn()
        prover_txn = raw.to_prover_transaction()
        assert prover_txn.account_hash != raw.account_id
        assert len(prover_txn.account_hash) == 64  # SHA3-256 hex
        assert prover_txn.counterparty_hash != raw.counterparty_id

    def test_full_commit_and_verify(self):
        raw = self._make_raw_txn("TXN-INTEG-002", 750000)
        prover_txn = raw.to_prover_transaction()

        # Score for anomaly
        score_result = self.detector.score(
            txn_id=prover_txn.txn_id,
            account_hash=prover_txn.account_hash,
            counterparty_hash=prover_txn.counterparty_hash,
            amount_cents=prover_txn.amount_cents,
            txn_type=prover_txn.txn_type,
            timestamp_ns=prover_txn.timestamp_ns,
        )

        # Commit
        record = self.store.add(
            txn_id=prover_txn.txn_id,
            amount_cents=prover_txn.amount_cents,
            account_id=raw.account_id,
            txn_type=prover_txn.txn_type,
            anomaly_score=score_result["anomaly_score"],
        )
        assert record.pii_bytes == 0

        # Sign
        envelope = sign_commitment(
            self.signing_key,
            record.txn_id,
            record.binding_hash,
            record.timestamp_ns,
        )

        # External verify (signature only — no secrets)
        committed_record = {**record.to_export_dict(), "signature": envelope}
        verify_result = self.verifier.verify_envelope(committed_record)
        assert verify_result["overall"] == "PASS"

    def test_anomaly_quarantine_flow(self):
        raw = self._make_raw_txn("TXN-INTEG-ANOM", 9_999_999_99)  # suspicious amount
        record = self.store.add(
            txn_id=raw.txn_id,
            amount_cents=raw.amount_cents,
            account_id=raw.account_id,
            txn_type=raw.txn_type,
            anomaly_score=0.92,  # forced high
        )
        assert record.status == "QUARANTINED"

        # CISO authorizes
        self.store.authorize(raw.txn_id)
        fetched = self.store.get(raw.txn_id)
        assert fetched.status == "VERIFIED"

    def test_zero_pii_throughout_pipeline(self):
        for i in range(5):
            raw = self._make_raw_txn(f"TXN-PII-{i:03d}", 100000 * (i + 1))
            prover_txn = raw.to_prover_transaction()
            record = self.store.add(
                txn_id=prover_txn.txn_id,
                amount_cents=prover_txn.amount_cents,
                account_id=raw.account_id,
                txn_type=prover_txn.txn_type,
            )
            export = record.to_export_dict()
            # Check no PII fields
            assert export["pii_bytes"] == 0
            assert "account_id" not in export
            assert "counterparty_id" not in export
            assert str(raw.amount_cents) not in str(export)

    def test_batch_commit(self):
        from prover.crypto.commitment import batch_commit
        txns = [
            {
                "txn_id": f"TXN-BATCH-{i:04d}",
                "amount_cents": 50000 * (i + 1),
                "account_id": f"ACC-{i:04d}",
                "txn_type": "NEFT",
                "anomaly_score": 0.1,
            }
            for i in range(10)
        ]
        results = batch_commit(txns, self.store)
        assert len(results) == 10
        assert all(r.pii_bytes == 0 for r in results)

    def test_ledger_export_zero_pii(self):
        for i in range(3):
            self.store.add(f"TXN-EXPORT-{i}", 10000, f"ACC-{i}", "RTGS", 0.1)
        export = self.store.audit_export()
        for rec in export:
            assert rec["pii_bytes"] == 0
            assert "account_id" not in rec
            assert "amount_cents" not in rec

    def test_stats_consistency(self):
        store = CommitmentStore()
        store.add("TXN-STAT-1", 1000, "ACC-1", "NEFT", 0.1)   # VERIFIED
        store.add("TXN-STAT-2", 2000, "ACC-2", "RTGS", 0.9)   # QUARANTINED
        store.add("TXN-STAT-3", 3000, "ACC-3", "NEFT", 0.1)   # VERIFIED

        s = store.stats()
        assert s["total"] == 3
        assert s["verified"] == 2
        assert s["quarantined"] == 1
        assert s["chain_integrity_pct"] == pytest.approx(66.7, abs=0.5)