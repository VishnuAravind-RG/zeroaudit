"""
test_sanctions_graph.py - Real graph-database sanctions proximity client

No Neo4j server runs in this test sandbox, which is itself useful: every
test in TestDegradedMode exercises the client's REAL fallback path, not a
mock of it. TestQueryLogic drives the Cypher-construction and
result-parsing logic against a fake driver, since correctness there
doesn't require an actual database - only a session.run()/single() pair
shaped the way the neo4j driver shapes them.

    pytest tests/test_sanctions_graph.py -v
"""

import pytest

from verifier.sanctions_graph import SanctionsGraphClient, _PROGRAM_SEVERITY


class TestDegradedMode:
    """Genuinely exercised: there is no Neo4j to connect to here."""

    def test_construction_does_not_raise_without_a_server(self):
        client = SanctionsGraphClient(uri="bolt://localhost:1", password="x", connect_timeout=0.3)
        assert client.available is False

    def test_returns_safe_default_when_unavailable(self):
        client = SanctionsGraphClient(uri="bolt://localhost:1", password="x", connect_timeout=0.3)
        hops, flag = client.hops_to_sanctioned("ab" * 32, "cd" * 32)
        assert flag == "NONE"
        assert hops >= 4          # never claims a false sanctions hit when blind

    def test_stats_report_degraded(self):
        client = SanctionsGraphClient(uri="bolt://localhost:1", password="x", connect_timeout=0.3)
        client.hops_to_sanctioned("ab" * 32)
        stats = client.stats()
        assert stats["available"] is False
        assert stats["backend"] == "degraded"

    def test_never_raises_out_of_hops_to_sanctioned(self):
        """The ingest pipeline calls this inline; it must never throw."""
        client = SanctionsGraphClient(uri="bolt://localhost:1", password="x", connect_timeout=0.3)
        for _ in range(5):
            client.hops_to_sanctioned("00" * 32, "ff" * 32)  # should not raise


class _FakeRecord(dict):
    def __getitem__(self, key):
        return dict.get(self, key)


class _FakeResult:
    def __init__(self, record):
        self._record = record

    def single(self):
        return self._record


class _FakeSession:
    def __init__(self, record, capture):
        self._record = record
        self._capture = capture

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, query, **kwargs):
        self._capture["query"] = query
        self._capture["kwargs"] = kwargs
        return _FakeResult(self._record)


class _FakeDriver:
    def __init__(self, record=None, capture=None):
        self._record = record
        self._capture = capture if capture is not None else {}

    def verify_connectivity(self):
        return True

    def session(self):
        return _FakeSession(self._record, self._capture)

    def close(self):
        pass


def _wired_client(record, capture=None):
    """A client whose real _connect() is bypassed with a fake driver."""
    client = SanctionsGraphClient.__new__(SanctionsGraphClient)
    client._uri, client._user, client._password = "fake", "fake", "fake"
    import threading
    client._lock = threading.Lock()
    client._query_count = 0
    client._error_count = 0
    client._available = True
    client._driver = _FakeDriver(record, capture)
    return client


class TestQueryLogic:
    def test_hit_maps_hops_to_the_matching_severity_tier(self):
        for hops, expected_flag in _PROGRAM_SEVERITY.items():
            record = _FakeRecord(hops=hops, name="TEST ENTITY", program="TEST-PROGRAM")
            client = _wired_client(record)
            result_hops, flag = client.hops_to_sanctioned("ab" * 32, "cd" * 32)
            assert result_hops == hops
            assert flag == expected_flag

    def test_no_path_returns_safe_default(self):
        client = _wired_client(_FakeRecord(hops=None, name=None, program=None))
        hops, flag = client.hops_to_sanctioned("ab" * 32)
        assert flag == "NONE"

    def test_missing_record_returns_safe_default(self):
        client = _wired_client(None)
        hops, flag = client.hops_to_sanctioned("ab" * 32)
        assert flag == "NONE"

    def test_query_checks_both_account_and_counterparty(self):
        capture = {}
        client = _wired_client(_FakeRecord(hops=1, name="X", program="Y"), capture)
        client.hops_to_sanctioned("account-hash", "counterparty-hash")
        assert set(capture["kwargs"]["hashes"]) == {"account-hash", "counterparty-hash"}

    def test_query_tolerates_a_missing_counterparty(self):
        capture = {}
        client = _wired_client(_FakeRecord(hops=1, name="X", program="Y"), capture)
        client.hops_to_sanctioned("account-hash", None)
        assert capture["kwargs"]["hashes"] == ["account-hash"]

    def test_query_is_a_real_shortest_path_cypher_query(self):
        """Not a hex-prefix lookup - an actual graph traversal."""
        capture = {}
        client = _wired_client(_FakeRecord(hops=1, name="X", program="Y"), capture)
        client.hops_to_sanctioned("account-hash")
        query = capture["query"]
        assert "shortestPath" in query
        assert "SanctionedEntity" in query
        assert "TRANSACTED_WITH" in query

    def test_query_error_flips_to_degraded_and_returns_safe_default(self):
        class ExplodingSession:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def run(self, *a, **k):
                raise RuntimeError("connection reset")

        class ExplodingDriver:
            def session(self):
                return ExplodingSession()

        client = SanctionsGraphClient.__new__(SanctionsGraphClient)
        import threading
        client._lock = threading.Lock()
        client._query_count, client._error_count = 0, 0
        client._available = True
        client._driver = ExplodingDriver()

        hops, flag = client.hops_to_sanctioned("ab" * 32)
        assert flag == "NONE"
        assert client.available is False        # a failed query marks the client degraded
        assert client.stats()["errors"] == 1
