"""
test_verifier.py — Verifier & Anomaly Detector Unit Tests
Run: pytest tests/test_verifier.py -v
"""

import pytest
import time
from verifier.anomaly_detector import (
    AnomalyDetector, VelocityTracker, benford_deviation,
    graph_hops_to_blacklist, extract_features,
)
from verifier.components.charts import (
    tps_history, anomaly_distribution, graph_proximity,
    benford_chart, commitment_size_distribution,
)
from verifier.components.sidebar import system_status, alert_feed, pipeline_nodes


# ── Benford's Law ──────────────────────────────────────────────────────────────

class TestBenford:
    def test_leading_9_high_deviation(self):
        # Amount starting with 9 should have high deviation
        score = benford_deviation(900000)
        assert score > 0.03

    def test_leading_1_low_deviation(self):
        # Amount starting with 1 should have low deviation
        score = benford_deviation(100000)
        assert score < 0.5

    def test_zero_amount(self):
        score = benford_deviation(0)
        assert 0.0 <= score <= 1.0

    def test_range(self):
        for amount in [1, 100, 50000, 999999, 10000000]:
            score = benford_deviation(amount)
            assert 0.0 <= score <= 1.0, f"Out of range for amount={amount}"


# ── Velocity Tracker ──────────────────────────────────────────────────────────

class TestVelocityTracker:
    def test_empty(self):
        vt = VelocityTracker()
        assert vt.count_1h("ACC-001") == 0

    def test_count_increases(self):
        vt = VelocityTracker()
        for _ in range(10):
            vt.record("ACC-002")
        assert vt.count_1h("ACC-002") == 10

    def test_old_entries_evicted(self):
        vt = VelocityTracker(window_seconds=1)
        vt.record("ACC-003", timestamp_ns=int((time.time() - 2) * 1e9))
        vt.record("ACC-003", timestamp_ns=time.time_ns())
        assert vt.count_1h("ACC-003") == 1

    def test_separate_accounts(self):
        vt = VelocityTracker()
        for _ in range(5):
            vt.record("ACC-A")
        for _ in range(3):
            vt.record("ACC-B")
        assert vt.count_1h("ACC-A") == 5
        assert vt.count_1h("ACC-B") == 3


# ── Anomaly Detector ──────────────────────────────────────────────────────────

class TestAnomalyDetector:
    def setup_method(self):
        self.detector = AnomalyDetector(model_path=None)  # statistical mode

    def test_score_returns_dict(self):
        result = self.detector.score(
            txn_id="TXN-001",
            account_hash="a" * 64,
            counterparty_hash="b" * 64,
            amount_cents=500000,
            txn_type="RTGS",
            timestamp_ns=time.time_ns(),
        )
        assert "anomaly_score" in result
        assert "reconstruction_loss" in result
        assert "benford_deviation" in result
        assert "graph_hops_to_blacklist" in result
        assert "flag_reason" in result

    def test_score_range(self):
        result = self.detector.score(
            txn_id="TXN-002",
            account_hash="c" * 64,
            counterparty_hash="d" * 64,
            amount_cents=1000000,
            txn_type="NEFT",
            timestamp_ns=time.time_ns(),
        )
        assert 0.0 <= result["anomaly_score"] <= 1.0
        assert 0.0 <= result["reconstruction_loss"] <= 1.1

    def test_no_pii_in_result(self):
        result = self.detector.score(
            txn_id="TXN-003",
            account_hash="e" * 64,
            counterparty_hash="f" * 64,
            amount_cents=200000,
            txn_type="WIRE_TRANSFER",
            timestamp_ns=time.time_ns(),
        )
        result_str = str(result)
        assert "200000" not in result_str


# ── Charts ────────────────────────────────────────────────────────────────────

class TestCharts:
    def test_tps_history_length(self):
        data = tps_history(n_bars=20)
        assert len(data["datasets"][0]["data"]) == 20

    def test_tps_history_custom_samples(self):
        samples = [5.0, 6.0, 7.0, 8.0, 9.0]
        data = tps_history(samples=samples)
        assert data["datasets"][0]["data"] == samples
        assert data["peak"] == 9.0

    def test_anomaly_distribution_has_curve(self):
        data = anomaly_distribution(outlier_score=0.94)
        assert len(data["curve"]) == 200
        assert "outlier" in data
        assert "percentile" in data
        assert float(data["percentile"].rstrip("th")) > 90

    def test_graph_proximity_nodes(self):
        data = graph_proximity("TXN-TEST-001", "a" * 64, hops=2, flag_reason="OFAC_SANCTION_LIST")
        node_types = [n["type"] for n in data["nodes"]]
        assert "transaction" in node_types
        assert "blacklisted" in node_types

    def test_benford_chart_keys(self):
        data = benford_chart()
        assert "labels" in data
        assert "expected" in data
        assert "observed" in data
        assert "chi2_statistic" in data
        assert len(data["labels"]) == 9

    def test_commitment_size_distribution(self):
        records = [{"size_kb": 8.0} for _ in range(20)]
        data = commitment_size_distribution(records)
        assert data["total_records"] == 20
        assert data["avg_kb"] == 8.0


# ── Sidebar ───────────────────────────────────────────────────────────────────

class TestSidebar:
    def test_system_status_all_online(self):
        status = system_status(True, True, True, True)
        assert status["overall"] == "NOMINAL"
        assert all(c["status"] == "ONLINE" for c in status["components"])
        assert status["pii_bytes"] == 0

    def test_system_status_degraded(self):
        status = system_status(cassandra_ok=False)
        assert status["overall"] == "DEGRADED"

    def test_alert_feed_from_anomalies(self):
        anomalies = [
            {"txn_id": "TXN-A001", "anomaly_score": 0.95, "flag_reason": "OFAC_SANCTION_LIST", "timestamp_ns": 1},
            {"txn_id": "TXN-A002", "anomaly_score": 0.80, "flag_reason": "RBI_FLAG_2024", "timestamp_ns": 2},
        ]
        alerts = alert_feed(recent_anomalies=anomalies)
        assert len(alerts) == 2
        assert alerts[0]["severity"] == "CRITICAL"
        assert alerts[1]["severity"] == "HIGH"

    def test_pipeline_nodes_count(self):
        nodes = pipeline_nodes()
        assert len(nodes) == 7
        ids = [n["id"] for n in nodes]
        assert "sgx" in ids
        assert "zkp" in ids
        assert "cassandra" not in ids  # should be "db"
        assert "db" in ids