"""
test_integration.py - Full pipeline, in process, no Kafka or Cassandra required

Walks a transaction the whole way:

    simulator -> ingress signature -> intent engine -> LWE commit
              -> hash chain -> Ed25519 -> zero-PII envelope
              -> external verifier -> selective disclosure

    pytest tests/test_integration.py -v
"""

import copy
import hashlib
import pytest

from simulator.bank_sim import (
    generate_normal_transaction, generate_anomalous_transaction,
    payload_hash, ANOMALY_TYPES, BankSimulator,
)
from prover.crypto.commitment import CommitmentStore, batch_commit
from prover.crypto.lwe import LWEPublicKey, open_commitment
from prover.crypto.signature import SigningKey, verify_transaction_signature
from verifier.verify import ExternalVerifier
from verifier.anomaly_detector import AnomalyDetector


FORBIDDEN = ("amount", "amount_cents", "account_id", "counterparty_id",
             "currency", "metadata")


@pytest.fixture
def pipeline():
    """A prover store plus a verifier holding only its public keys."""
    store = CommitmentStore(ledger=None)
    keys = store.public_keys()
    verifier = ExternalVerifier(keys["ed25519_public_key_b64"])
    return store, verifier, keys


def _ingest(store, detector, txn):
    """One transaction through the prover path, returning the public envelope."""
    account_hash = hashlib.sha3_256(txn["account_id"].encode()).hexdigest()
    counterparty_hash = hashlib.sha3_256(txn["counterparty_id"].encode()).hexdigest()
    verdict = detector.score(
        txn_id=txn["txn_id"], account_hash=account_hash,
        counterparty_hash=counterparty_hash, amount_cents=txn["amount_cents"],
        txn_type=txn["txn_type"], timestamp_ns=txn["timestamp_ns"],
    )
    record = store.add(
        txn_id=txn["txn_id"], amount_cents=txn["amount_cents"],
        account_id=txn["account_id"], txn_type=txn["txn_type"],
        anomaly_score=verdict["anomaly_score"], flag_reason=verdict["flag_reason"],
    )
    return record.to_export_dict(), verdict


class TestFullPipeline:
    def test_end_to_end_verifies(self, pipeline):
        store, verifier, _ = pipeline
        detector = AnomalyDetector()
        import random
        random.seed(11)

        for _ in range(30):
            envelope, _ = _ingest(store, detector, generate_normal_transaction())
            assert verifier.verify_envelope(envelope)["verified"] is True

    def test_zero_pii_throughout(self, pipeline):
        store, verifier, _ = pipeline
        detector = AnomalyDetector()
        txn = generate_normal_transaction()
        envelope, _ = _ingest(store, detector, txn)

        blob = str(envelope)
        assert str(txn["amount_cents"]) not in blob
        assert txn["account_id"] not in blob
        assert txn["counterparty_id"] not in blob
        assert not any(field in envelope for field in FORBIDDEN)
        assert envelope["pii_bytes"] == 0

    def test_account_is_hashed_not_dropped(self, pipeline):
        """The auditor still needs to correlate activity, without identities."""
        store, _, _ = pipeline
        detector = AnomalyDetector()
        txn = generate_normal_transaction()
        envelope, _ = _ingest(store, detector, txn)
        expected = hashlib.sha3_256(txn["account_id"].encode()).hexdigest()
        assert envelope["account_hash"] == expected

    def test_chain_survives_the_whole_run(self, pipeline):
        store, verifier, _ = pipeline
        detector = AnomalyDetector()
        for _ in range(40):
            envelope, _ = _ingest(store, detector, generate_normal_transaction())
            verifier.verify_envelope(envelope)
        assert store.verify_chain()["intact"] is True
        assert verifier.stats()["chain_broken"] == 0

    def test_anomalies_reach_quarantine(self, pipeline):
        store, _, _ = pipeline
        detector = AnomalyDetector()
        import random
        random.seed(5)
        quarantined = 0
        for _ in range(40):
            txn = generate_anomalous_transaction("sanctions_adjacent")
            envelope, _ = _ingest(store, detector, txn)
            if envelope["status"] == "QUARANTINED":
                quarantined += 1
        assert quarantined > 30

    def test_scoring_is_not_supplied_by_the_producer(self):
        """
        Regression. The simulator used to emit `anomaly_score` and the prover
        used it verbatim, so the detector never ran. The simulator now emits
        ground truth for evaluation only.
        """
        txn = generate_normal_transaction()
        assert "anomaly_score" not in txn
        assert "ground_truth_anomaly" in txn


class TestSelectiveDisclosure:
    def test_auditor_opens_one_commitment(self, pipeline):
        store, _, _ = pipeline
        txn = generate_normal_transaction()
        store.add(txn["txn_id"], txn["amount_cents"], txn["account_id"],
                  txn["txn_type"], 0.1)

        opening = store.opening_for(txn["txn_id"], txn["amount_cents"])
        pub = LWEPublicKey.from_b64(opening["lwe_public_key_b64"])
        assert open_commitment(pub, opening["commitment_b64"],
                               txn["amount_cents"], opening["blinding_seed_hex"])

    def test_a_lying_bank_cannot_open(self, pipeline):
        store, _, _ = pipeline
        txn = generate_normal_transaction()
        store.add(txn["txn_id"], txn["amount_cents"], txn["account_id"],
                  txn["txn_type"], 0.1)

        opening = store.opening_for(txn["txn_id"], txn["amount_cents"])
        pub = LWEPublicKey.from_b64(opening["lwe_public_key_b64"])
        for delta in (1, -1, 10**6, 1 << 33):
            assert not open_commitment(pub, opening["commitment_b64"],
                                       txn["amount_cents"] + delta,
                                       opening["blinding_seed_hex"])

    def test_disclosure_reveals_only_one_transaction(self, pipeline):
        """Opening TXN-A must not help an auditor open TXN-B."""
        store, _, _ = pipeline
        store.add("TXN-A", 111_111, "ACC-1", "RTGS", 0.1)
        store.add("TXN-B", 222_222, "ACC-2", "RTGS", 0.1)

        a = store.opening_for("TXN-A", 111_111)
        b = store.opening_for("TXN-B", 222_222)
        pub = LWEPublicKey.from_b64(a["lwe_public_key_b64"])

        assert a["blinding_seed_hex"] != b["blinding_seed_hex"]
        # A's blinding factor cannot open B.
        assert not open_commitment(pub, b["commitment_b64"], 222_222,
                                   a["blinding_seed_hex"])


class TestIngressFirewall:
    def test_signed_transaction_accepted(self):
        sim = BankSimulator(kafka_bootstrap="unused", sign=True)
        txn = sim._sign(generate_normal_transaction())
        assert verify_transaction_signature(txn) is True

    def test_tampered_amount_rejected(self):
        sim = BankSimulator(kafka_bootstrap="unused", sign=True)
        txn = sim._sign(generate_normal_transaction())
        txn["amount_cents"] += 1
        txn["payload_hash"] = payload_hash(txn)     # re-hash, but cannot re-sign
        assert verify_transaction_signature(txn) is False

    def test_unsigned_allowed_when_not_required(self, monkeypatch):
        monkeypatch.setenv("INGRESS_REQUIRE_SIGNATURE", "false")
        assert verify_transaction_signature(generate_normal_transaction()) is True

    def test_unsigned_rejected_when_required(self, monkeypatch):
        monkeypatch.setenv("INGRESS_REQUIRE_SIGNATURE", "true")
        assert verify_transaction_signature(generate_normal_transaction()) is False


class TestTamperDetection:
    @pytest.fixture
    def ledger(self, pipeline):
        store, verifier, keys = pipeline
        records = [store.add("TXN-%03d" % i, 1000 + i, "ACC-%d" % (i % 3),
                             "RTGS", 0.1).to_export_dict() for i in range(20)]
        return store, verifier, keys, records

    def test_baseline_is_clean(self, ledger):
        _, verifier, _, records = ledger
        assert all(verifier.verify_envelope(r)["verified"] for r in records)

    def test_edited_record_detected(self, ledger):
        _, verifier, _, records = ledger
        forged = copy.deepcopy(records)
        forged[9]["binding_hash"] = "de" + "ad" * 31
        assert verifier.verify_envelope(forged[9])["verified"] is False

    def test_reordered_records_detected(self, ledger):
        _, verifier, _, records = ledger
        shuffled = copy.deepcopy(records)
        shuffled[4], shuffled[5] = shuffled[5], shuffled[4]
        results = [verifier.verify_envelope(r) for r in shuffled]
        assert not all(r["verified"] for r in results)

    def test_deleted_record_detected(self, ledger):
        _, verifier, _, records = ledger
        results = [verifier.verify_envelope(r) for r in records if r["seq"] != 11]
        assert not all(r["verified"] for r in results)


class TestBatchAndStats:
    def test_batch_commit(self, pipeline):
        store, _, _ = pipeline
        txns = [{"txn_id": "B-%d" % i, "amount_cents": 1000 + i,
                 "account_id": "ACC", "txn_type": "NEFT"} for i in range(10)]
        assert len(batch_commit(txns, store)) == 10
        assert store.stats()["total"] == 10

    def test_stats_are_consistent(self, pipeline):
        store, _, _ = pipeline
        for i in range(20):
            store.add("TXN-%d" % i, 100, "ACC", "RTGS", 0.95 if i % 4 == 0 else 0.1)
        stats = store.stats()
        assert stats["total"] == 20
        assert stats["quarantined"] == 5
        assert stats["verified"] == 15
        assert stats["chain_seq"] == 20

    def test_every_anomaly_type_is_generated(self):
        for atype in ANOMALY_TYPES:
            txn = generate_anomalous_transaction(atype)
            assert txn["ground_truth_type"] == atype
            assert txn["ground_truth_anomaly"] is True


class TestChainTopicOrdering:
    """
    Regression: committed/anomalies were published with no partition key,
    so Kafka's default partitioner spread a hash-linked stream across all 6
    partitions. A single-consumer verifier polling all of them received
    records interleaved rather than in production order, and its online
    chain check flagged false breaks on nearly every record even though the
    records themselves were valid. A constant key per chain-linked topic
    keeps them on one partition, which is free here since the chain is
    already fully serialized by CommitmentStore.add()'s lock.
    """

    def test_chain_linked_topics_get_a_partition_key(self, monkeypatch):
        from prover.consumer import ProverConsumer
        from prover.config.settings import settings

        monkeypatch.setattr("prover.consumer._KAFKA", True)
        consumer = ProverConsumer()
        assert settings.KAFKA_TOPIC_COMMITTED in consumer._chain_topics
        assert settings.KAFKA_TOPIC_ANOMALIES in consumer._chain_topics
        assert settings.KAFKA_TOPIC_INGEST not in consumer._chain_topics

        sent = []
        consumer._producer = type("FakeProducer", (), {
            "send": lambda self, topic, key=None, value=None: sent.append((topic, key)),
        })()

        consumer._publish(settings.KAFKA_TOPIC_COMMITTED, {"txn_id": "TXN-1"})
        consumer._publish(settings.KAFKA_TOPIC_ANOMALIES, {"txn_id": "TXN-2"})

        topic, key = sent[0]
        assert key == b"zeroaudit-chain"
        # Every chain-linked record uses the SAME key, so they all land on
        # the same partition regardless of which of the two topics it is.
        assert sent[0][1] == sent[1][1]
