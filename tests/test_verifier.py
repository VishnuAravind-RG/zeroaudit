"""
test_verifier.py - External verification, intent engine, and chart helpers

    pytest tests/test_verifier.py -v
"""

import copy
import time
import base64
import pytest

from verifier.anomaly_detector import (
    AnomalyDetector, VelocityTracker, BenfordMonitor,
    benford_surprisal, threshold_proximity, graph_hops_to_blacklist,
    extract_features, to_vector, FEATURE_NAMES, N_FEATURES,
    REPORTING_THRESHOLDS, BENFORD_P,
)
from verifier.verify import ExternalVerifier
from verifier.components.charts import (
    tps_history, anomaly_distribution, graph_proximity,
    benford_chart, extract_benford_counts, commitment_size_distribution,
)
from verifier.components.sidebar import system_status, alert_feed, pipeline_nodes
from prover.crypto.commitment import CommitmentStore


class TestBenfordSurprisal:
    def test_leading_9_is_maximally_surprising(self):
        assert benford_surprisal(900_000) == pytest.approx(1.0, abs=1e-6)

    def test_leading_1_is_least_surprising(self):
        # -log2(0.301) / -log2(0.046) = 0.389
        assert benford_surprisal(100_000) < 0.45

    def test_monotonic_in_leading_digit(self):
        """Benford probability falls with the leading digit, so surprisal rises."""
        scores = [benford_surprisal(int(str(d) + "00000")) for d in range(1, 10)]
        assert scores == sorted(scores)

    def test_is_not_identically_zero(self):
        """
        Regression. The old implementation compared log10(1 + 1/d) against a
        table of log10(1 + 1/d) - the same quantity - so it returned ~0 for
        every input and the > 0.7 flag could never fire.
        """
        scores = [benford_surprisal(int(str(d) + "00000")) for d in range(1, 10)]
        assert max(scores) - min(scores) > 0.5

    def test_zero_amount(self):
        assert 0.0 <= benford_surprisal(0) <= 1.0

    def test_range(self):
        for amount in (1, 42, 999_999, 10**12):
            assert 0.0 <= benford_surprisal(amount) <= 1.0


class TestBenfordMonitor:
    def test_quiet_on_benford_conforming_stream(self):
        import random
        random.seed(3)
        monitor = BenfordMonitor()
        digits = list(BENFORD_P)
        weights = [BENFORD_P[d] for d in digits]
        for _ in range(3000):
            d = random.choices(digits, weights=weights)[0]
            monitor.record(int(str(d) + "12345"))
        assert monitor.chi_squared()["suspicious"] is False

    def test_fires_on_fabricated_stream(self):
        monitor = BenfordMonitor()
        for _ in range(1000):
            monitor.record(912_345)              # every amount leads with 9
        result = monitor.chi_squared()
        assert result["suspicious"] is True
        assert result["chi2"] > result["critical_value_p05"]

    def test_needs_a_sample_before_judging(self):
        monitor = BenfordMonitor()
        monitor.record(123)
        assert monitor.chi_squared()["suspicious"] is False


class TestThresholdProximity:
    def test_just_below_threshold_scores_high(self):
        for t in REPORTING_THRESHOLDS:
            assert threshold_proximity(t - 100) > 0.99

    def test_at_or_above_threshold_scores_zero(self):
        """One-sided by design: filing a report is unremarkable."""
        for t in REPORTING_THRESHOLDS:
            assert threshold_proximity(t) == 0.0
            assert threshold_proximity(t + 5000) == 0.0

    def test_far_from_threshold_scores_zero(self):
        assert threshold_proximity(734_512_00) == 0.0

    def test_band_is_tight_enough_to_be_specific(self):
        """A wide band lets ordinary amounts land inside it by chance."""
        t = REPORTING_THRESHOLDS[0]
        assert threshold_proximity(int(t * 0.90)) == 0.0


class TestVelocityTracker:
    def test_empty(self):
        assert VelocityTracker().count_1h("nobody") == 0

    def test_count_increases(self):
        tracker = VelocityTracker()
        now = time.time_ns()
        for i in range(5):
            tracker.record("acct", now + i * 10**9)
        assert tracker.count_1h("acct", now + 5 * 10**9) == 5

    def test_old_entries_evicted(self):
        tracker = VelocityTracker(window_seconds=60)
        now = time.time_ns()
        tracker.record("acct", now - 3600 * 10**9)      # an hour ago
        tracker.record("acct", now)
        assert tracker.count_1h("acct", now) == 1

    def test_separate_accounts(self):
        tracker = VelocityTracker()
        now = time.time_ns()
        tracker.record("a", now)
        tracker.record("b", now)
        tracker.record("b", now)
        assert tracker.count_1h("a", now) == 1
        assert tracker.count_1h("b", now) == 2

    def test_uses_event_time_not_wall_clock(self):
        """
        Regression. Counting against wall-clock returns 0 for any record whose
        event time is older than the window - which is every record during a
        Kafka replay or a backfill.
        """
        tracker = VelocityTracker()
        old = time.time_ns() - 30 * 86400 * 10**9        # 30 days ago
        for i in range(4):
            tracker.record("acct", old + i * 10**9)
        assert tracker.count_1h("acct", old + 4 * 10**9) == 4
        assert tracker.count_1h("acct") == 0             # correctly stale now


class TestGraphProximity:
    def test_deterministic(self):
        a, b = "00" + "f" * 62, "ab" + "c" * 62
        assert graph_hops_to_blacklist(a, b) == graph_hops_to_blacklist(a, b)

    def test_sanctioned_prefix_is_one_hop(self):
        assert graph_hops_to_blacklist("000" + "f" * 61, "fff" + "f" * 61)[0] == 1

    def test_clean_accounts_are_distant(self):
        hops, flag = graph_hops_to_blacklist("fff" + "a" * 61, "eee" + "b" * 61)
        assert hops >= 4 and flag == "NONE"

    def test_specificity_is_realistic(self):
        """
        A 2-hex-char bucket marked ~6% of all accounts as sanctions-adjacent,
        which alone produced an 8.7% false-positive rate.
        """
        import hashlib
        flagged = sum(
            1 for i in range(5000)
            if graph_hops_to_blacklist(
                hashlib.sha3_256(str(i).encode()).hexdigest(),
                hashlib.sha3_256(("c%d" % i).encode()).hexdigest())[0] <= 3
        )
        assert flagged / 5000 < 0.01


class TestFeatureExtraction:
    def test_vector_shape_and_order(self):
        features, _ = extract_features(
            "TXN-1", "ab" * 32, "cd" * 32, 150_000, "RTGS",
            time.time_ns(), VelocityTracker())
        assert list(features) == FEATURE_NAMES
        assert len(to_vector(features)) == N_FEATURES

    def test_all_features_normalised(self):
        features, _ = extract_features(
            "TXN-1", "ab" * 32, "cd" * 32, 10**14, "FX_CONVERSION",
            time.time_ns(), VelocityTracker())
        assert all(0.0 <= v <= 1.0 for v in to_vector(features))

    def test_deterministic(self):
        args = ("TXN-1", "ab" * 32, "cd" * 32, 150_000, "RTGS", 1_700_000_000_000_000_000)
        a, _ = extract_features(*args, VelocityTracker())
        b, _ = extract_features(*args, VelocityTracker())
        assert a == b


class TestAnomalyDetector:
    @pytest.fixture(scope="class")
    def detector(self):
        return AnomalyDetector()

    def test_score_shape(self, detector):
        result = detector.score("TXN-1", "ab" * 32, "cd" * 32, 150_000,
                                "RTGS", time.time_ns())
        for key in ("anomaly_score", "novelty_score", "typology_score",
                    "flag_reason", "quarantine", "backend"):
            assert key in result

    def test_score_in_range(self, detector):
        for amount in (1, 150_000, 10**13):
            result = detector.score("TXN-X", "ab" * 32, "cd" * 32, amount,
                                    "RTGS", time.time_ns())
            assert 0.0 <= result["anomaly_score"] <= 1.0

    def test_no_pii_in_result(self, detector):
        result = detector.score("TXN-1", "ab" * 32, "cd" * 32, 987_654_321,
                                "RTGS", time.time_ns())
        assert "987654321" not in str(result)

    def test_deterministic(self):
        """
        Determinism is over a STREAM, not over repeated calls: the detector
        carries velocity state, so scoring the same transaction twice legitimately
        differs. Two fresh detectors fed the same sequence must agree exactly.
        """
        seq = [("TXN-%d" % i, "ab" * 32, "cd" * 32, 100_000 + i,
                "RTGS", 1_700_000_000_000_000_000 + i * 10**9) for i in range(20)]
        d1, d2 = AnomalyDetector(), AnomalyDetector()
        assert [d1.score(*a)["anomaly_score"] for a in seq] ==                [d2.score(*a)["anomaly_score"] for a in seq]

    def test_sanctioned_counterparty_is_flagged(self, detector):
        result = detector.score("TXN-S", "000" + "a" * 61, "fff" + "b" * 61,
                                500_000, "WIRE_TRANSFER", time.time_ns())
        assert result["anomaly_score"] >= 0.75
        assert result["flag_reason"] == "OFAC_SANCTION_LIST"

    def test_structuring_is_flagged(self, detector):
        result = detector.score("TXN-T", "fff" + "a" * 61, "eee" + "b" * 61,
                                REPORTING_THRESHOLDS[0] - 100, "RTGS", time.time_ns())
        assert result["anomaly_score"] >= 0.75
        assert result["flag_reason"] == "STRUCTURING_PATTERN"

    def test_ordinary_transaction_is_not_flagged(self, detector):
        result = detector.score("TXN-N", "fff" + "a" * 61, "eee" + "b" * 61,
                                734_512_00, "NEFT", 1_700_000_000_000_000_000)
        assert result["anomaly_score"] < 0.75


class TestExternalVerifier:
    @pytest.fixture
    def signed_ledger(self):
        store = CommitmentStore(ledger=None)
        records = [store.add("TXN-%03d" % i, 1000 + i, "ACC-%d" % (i % 3),
                             "RTGS", 0.1).to_export_dict() for i in range(15)]
        return store.public_keys(), records

    def test_honest_ledger_verifies(self, signed_ledger):
        keys, records = signed_ledger
        verifier = ExternalVerifier(keys["ed25519_public_key_b64"])
        assert all(verifier.verify_envelope(r)["verified"] for r in records)

    def test_all_five_checks_run(self, signed_ledger):
        keys, records = signed_ledger
        verifier = ExternalVerifier(keys["ed25519_public_key_b64"])
        checks = [c["check"] for c in verifier.verify_envelope(records[0])["checks"]]
        assert checks == ["SIGNATURE", "BINDING_HASH", "CHAIN_LINK",
                          "LWE_PARAMS", "PII_ASSERTION"]

    def test_swapped_commitment_rejected(self, signed_ledger):
        keys, records = signed_ledger
        verifier = ExternalVerifier(keys["ed25519_public_key_b64"])
        forged = copy.deepcopy(records)
        forged[5]["commitment_b64"] = base64.b64encode(b"\x00" * 1536).decode()
        results = [verifier.verify_envelope(r) for r in forged]
        assert results[5]["verified"] is False

    def test_rogue_signature_rejected(self, signed_ledger):
        keys, records = signed_ledger
        verifier = ExternalVerifier(keys["ed25519_public_key_b64"])
        forged = copy.deepcopy(records)
        forged[3]["signature_b64"] = base64.b64encode(b"\x01" * 64).decode()
        assert verifier.verify_envelope(forged[3])["verified"] is False

    def test_pii_leak_rejected(self, signed_ledger):
        keys, records = signed_ledger
        verifier = ExternalVerifier(keys["ed25519_public_key_b64"])
        leaky = copy.deepcopy(records[0])
        leaky["amount_cents"] = 150_000
        result = verifier.verify_envelope(leaky)
        assert result["verified"] is False
        assert any(c["check"] == "PII_ASSERTION" and c["status"] == "FAIL"
                   for c in result["checks"])

    def test_dropped_record_breaks_chain(self, signed_ledger):
        keys, records = signed_ledger
        verifier = ExternalVerifier(keys["ed25519_public_key_b64"])
        results = [verifier.verify_envelope(r) for r in records if r["seq"] != 7]
        assert not all(r["verified"] for r in results)

    def test_signature_skipped_without_key(self, signed_ledger):
        """No public key means no authorship claim - it must not silently PASS."""
        _, records = signed_ledger
        verifier = ExternalVerifier(None)
        checks = verifier.verify_envelope(records[0])["checks"]
        sig = next(c for c in checks if c["check"] == "SIGNATURE")
        assert sig["status"] == "SKIP"

    def test_stats_use_the_documented_keys(self, signed_ledger):
        """Regression: /stats read `verified`/`failed`, the dict exposed neither."""
        keys, records = signed_ledger
        verifier = ExternalVerifier(keys["ed25519_public_key_b64"])
        for r in records:
            verifier.verify_envelope(r)
        stats = verifier.stats()
        assert stats["verified"] == len(records)
        assert stats["failed"] == 0
        assert stats["signature_ok"] == len(records)


class TestCharts:
    def test_tps_history_uses_real_samples(self):
        data = tps_history(samples=[1, 2, 3], n_bars=3)
        assert data["datasets"][0]["data"] == [1, 2, 3]
        assert data["peak"] == 3

    def test_anomaly_distribution_has_curve(self):
        data = anomaly_distribution(anomaly_scores=[0.1, 0.5, 0.94], highlight_score=0.94)
        assert data["curve"] and all("x" in p and "y" in p for p in data["curve"])

    def test_graph_proximity_nodes(self):
        data = graph_proximity("TXN-1", "ab" * 32, 2, "OFAC_SANCTION_LIST")
        assert data["nodes"] and data["edges"]

    def test_benford_chart_from_counts(self):
        data = benford_chart(observed_counts={d: d * 10 for d in range(1, 10)})
        assert len(data["expected"]) == 9 and len(data["observed"]) == 9
        assert "chi2_statistic" in data

    def test_extract_benford_counts(self):
        counts = extract_benford_counts([{"binding_hash": "9abc"}, {"binding_hash": "1def"}])
        assert sum(counts.values()) >= 1

    def test_commitment_size_distribution(self):
        data = commitment_size_distribution(records=[{"size_kb": 1.5}, {"size_kb": 1.5}])
        assert data["total_records"] == 2 and data["avg_kb"] == 1.5


class TestSidebar:
    def test_system_status_all_online(self):
        data = system_status(cassandra_ok=True, kafka_ok=True, sgx_ok=True,
                             intent_engine_ok=True)
        assert all(c["status"] == "ONLINE" for c in data["components"])

    def test_system_status_degraded(self):
        data = system_status(cassandra_ok=False, kafka_ok=True, sgx_ok=True,
                             intent_engine_ok=True)
        assert any(c["status"] != "ONLINE" for c in data["components"])

    def test_alert_feed_from_real_anomalies(self):
        alerts = alert_feed(recent_anomalies=[{
            "txn_id": "TXN-1", "anomaly_score": 0.93,
            "flag_reason": "OFAC_SANCTION_LIST", "timestamp_ns": time.time_ns(),
        }], n=5)
        assert alerts and "TXN-1" in str(alerts)

    def test_alert_feed_empty_without_anomalies(self):
        """No fabricated alerts when nothing has been flagged."""
        assert alert_feed(recent_anomalies=[], n=5) == []

    def test_pipeline_nodes(self):
        nodes = pipeline_nodes(cassandra_write_rate=1.0, kafka_lag_ms=5.0,
                               sgx_load_pct=10.0, lwe_payload_avg_kb=1.5)
        assert len(nodes) >= 4
