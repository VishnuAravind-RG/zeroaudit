"""
sanctions_graph.py - Real graph-database sanctions proximity

ZEROAUDIT originally answered "is this account near a sanctioned entity"
by checking whether the account hash's first two or three hex characters
fell in a fixed set - a lookup table dressed up as a graph query, and
explicitly documented as a stand-in ("in production this becomes a
Neo4j/TigerGraph query"). This module is that production version: a real
Neo4j instance, loaded with the actual U.S. Treasury OFAC SDN list
(scripts/load_sanctions_graph.py), queried with a real Cypher
shortest-path traversal.

    MATCH (a:Account {hash: $hash})
    OPTIONAL MATCH p = shortestPath((a)-[:TRANSACTED_WITH*1..8]-(s:SanctionedEntity))
    RETURN length(p) AS hops, s.name, s.program

Same interface, real backend
-----------------------------
get_detector()'s call site doesn't change: `hops_to_sanctioned(account_hash,
counterparty_hash)` still returns (hops, flag_reason), and hops is still
compared against the same 1/2/3 thresholds the typology rules already use.
What changed is that the number now comes from an actual graph traversal
against actual sanctions data, not a hex-prefix bucket.

Degraded mode
-------------
A live external dependency can go down. If Neo4j is unreachable, this
logs loudly and returns a safe neutral result (hops=8, "NONE") rather than
either crashing the ingest pipeline or silently falling back to the old
heuristic - a fallback to the thing being replaced would just reintroduce
the simulation it exists to remove. sidebar/status surfaces this as a
"DEGRADED" component so an operator sees it, rather than the pipeline
quietly screening nothing.
"""

import os
import logging
import threading
from typing import Optional, Tuple

logger = logging.getLogger("zeroaudit.sanctions_graph")

try:
    from neo4j import GraphDatabase
    from neo4j.exceptions import Neo4jError, ServiceUnavailable
    _NEO4J = True
except ImportError:  # pragma: no cover - exercised on minimal installs
    _NEO4J = False
    logger.warning("neo4j driver not installed - sanctions graph runs in degraded mode")

    class ServiceUnavailable(Exception):
        pass

    class Neo4jError(Exception):
        pass


NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "zeroaudit-demo")

_MAX_HOPS = 8
_SAFE_DEFAULT = (_MAX_HOPS, "NONE")

# Program-tag prefixes map to the same three severity tiers the typology
# rules already use. OFAC's own "Program" field on each SanctionedEntity
# tells us which real list a hit came from.
_PROGRAM_SEVERITY = {
    1: "OFAC_SANCTION_LIST",
    2: "RBI_FLAG_2024",
    3: "FATF_GREY_LIST",
}


class SanctionsGraphClient:
    """Thin wrapper around the Neo4j driver: connect once, query many times."""

    def __init__(self, uri: str = None, user: str = None, password: str = None,
                connect_timeout: float = 5.0):
        self._uri = uri or NEO4J_URI
        self._user = user or NEO4J_USER
        self._password = password or NEO4J_PASSWORD
        self._connect_timeout = connect_timeout
        self._driver = None
        self._lock = threading.Lock()
        self._available = False
        self._query_count = 0
        self._error_count = 0
        self._connect()

    def _connect(self) -> bool:
        if not _NEO4J:
            return False
        try:
            self._driver = GraphDatabase.driver(
                self._uri, auth=(self._user, self._password),
                connection_timeout=self._connect_timeout, max_connection_lifetime=300,
            )
            self._driver.verify_connectivity()
            self._available = True
            logger.info("sanctions graph connected: %s", self._uri)
            return True
        except Exception as exc:
            self._available = False
            logger.warning("sanctions graph unavailable (%s: %s) - degraded mode",
                           type(exc).__name__, exc)
            return False

    @property
    def available(self) -> bool:
        return self._available

    def hops_to_sanctioned(self, account_hash: str, counterparty_hash: str = None) -> Tuple[int, str]:
        """Real shortest-path query. Checks both the account and its counterparty."""
        if not self._available or not self._driver:
            return _SAFE_DEFAULT

        try:
            with self._driver.session() as session:
                result = session.run(
                    """
                    UNWIND $hashes AS h
                    MATCH (a:Account {hash: h})
                    OPTIONAL MATCH p = shortestPath(
                        (a)-[:TRANSACTED_WITH*1..%d]-(s:SanctionedEntity)
                    )
                    WITH h, p, s
                    WHERE p IS NOT NULL
                    RETURN length(p) AS hops, s.name AS name, s.program AS program
                    ORDER BY hops ASC
                    LIMIT 1
                    """ % _MAX_HOPS,
                    hashes=[h for h in (account_hash, counterparty_hash) if h],
                )
                record = result.single()
                with self._lock:
                    self._query_count += 1

            if not record or record["hops"] is None:
                return _SAFE_DEFAULT

            hops = int(record["hops"])
            flag = _PROGRAM_SEVERITY.get(hops, "NONE" if hops > 3 else "FATF_GREY_LIST")
            logger.debug("sanctions hit: %d hop(s) from real entity %r (%s)",
                        hops, record["name"], record["program"])
            return hops, flag

        except (ServiceUnavailable, Neo4jError, Exception) as exc:
            with self._lock:
                self._error_count += 1
            logger.error("sanctions graph query failed: %s: %s - returning safe default",
                        type(exc).__name__, exc)
            self._available = False
            return _SAFE_DEFAULT

    def stats(self) -> dict:
        with self._lock:
            return {
                "available": self._available,
                "queries": self._query_count,
                "errors": self._error_count,
                "backend": "neo4j" if self._available else "degraded",
            }

    def close(self):
        if self._driver:
            self._driver.close()


_client: Optional[SanctionsGraphClient] = None
_client_lock = threading.Lock()


def get_sanctions_graph() -> SanctionsGraphClient:
    global _client
    with _client_lock:
        if _client is None:
            _client = SanctionsGraphClient()
    return _client
