"""
cassandra_store.py - Cassandra-backed commitment ledger

Apache Cassandra is the write-absorbing tier of ZEROAUDIT: an append-only
LSM-tree store that takes the commitment stream at ingest rate and serves
audit queries afterwards.

Data model
----------
Cassandra has no joins and no ad-hoc queries, so tables are shaped by the
access pattern rather than by the entity. Three tables, one per query:

  commitments_by_bucket   partition = UTC day, clustered by (timestamp_ns DESC,
                          txn_id). Serves "show me the ledger", newest first,
                          without ever scanning the whole keyspace. Bucketing
                          by day bounds partition growth - a single unbounded
                          partition is the classic Cassandra footgun.

  commitments_by_txn      partition = txn_id. Serves the point lookup an
                          auditor performs when opening one commitment.

  ledger_head             single-row-per-shard pointer holding the tip of the
                          hash chain, so a restarted prover resumes the chain
                          instead of forking it.

Every write is idempotent: re-delivering the same Kafka message rewrites
identical column values rather than duplicating a row. That is what makes the
consumer safe to restart at an earlier offset.

Degraded mode
-------------
If the driver is missing or the cluster is unreachable, the ledger logs the
failure and reports `available == False`. The pipeline keeps running against
its in-memory store: losing durability must not take the audit path offline.
"""

import os
import json
import time
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("zeroaudit.cassandra")

try:
    from cassandra.cluster import Cluster, NoHostAvailable
    from cassandra.auth import PlainTextAuthProvider
    from cassandra.policies import DCAwareRoundRobinPolicy, ExponentialReconnectionPolicy
    from cassandra import ConsistencyLevel
    _DRIVER = True
except ImportError:  # pragma: no cover - exercised on minimal installs
    _DRIVER = False

    class NoHostAvailable(Exception):
        pass

    logger.warning("cassandra-driver not installed - ledger runs in memory-only mode")


KEYSPACE = os.environ.get("CASSANDRA_KEYSPACE", "zeroaudit")

_DDL_KEYSPACE = """
CREATE KEYSPACE IF NOT EXISTS %s
WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 1}
""" % KEYSPACE

_DDL_BY_BUCKET = """
CREATE TABLE IF NOT EXISTS %s.commitments_by_bucket (
    bucket             text,
    timestamp_ns       bigint,
    txn_id             text,
    seq                bigint,
    binding_hash       text,
    chain_hash         text,
    prev_chain_hash    text,
    commitment_b64     text,
    signature_b64      text,
    signing_key_id     text,
    pubkey_fingerprint text,
    size_kb            double,
    lwe_params         text,
    account_hash       text,
    txn_type           text,
    status             text,
    anomaly_score      double,
    flag_reason        text,
    novelty_score      double,
    typology_score     double,
    pii_bytes          int,
    PRIMARY KEY ((bucket), timestamp_ns, txn_id)
) WITH CLUSTERING ORDER BY (timestamp_ns DESC, txn_id ASC)
""" % KEYSPACE

_DDL_BY_TXN = """
CREATE TABLE IF NOT EXISTS %s.commitments_by_txn (
    txn_id             text PRIMARY KEY,
    bucket             text,
    timestamp_ns       bigint,
    seq                bigint,
    binding_hash       text,
    chain_hash         text,
    prev_chain_hash    text,
    commitment_b64     text,
    signature_b64      text,
    signing_key_id     text,
    pubkey_fingerprint text,
    size_kb            double,
    lwe_params         text,
    account_hash       text,
    txn_type           text,
    status             text,
    anomaly_score      double,
    flag_reason        text,
    novelty_score      double,
    typology_score     double,
    pii_bytes          int
)
""" % KEYSPACE

_DDL_HEAD = """
CREATE TABLE IF NOT EXISTS %s.ledger_head (
    shard      text PRIMARY KEY,
    seq        bigint,
    chain_hash text,
    updated_ns bigint
)
""" % KEYSPACE

_COLUMNS = [
    "txn_id", "bucket", "timestamp_ns", "seq", "binding_hash", "chain_hash",
    "prev_chain_hash", "commitment_b64", "signature_b64", "signing_key_id",
    "pubkey_fingerprint", "size_kb", "lwe_params", "account_hash", "txn_type",
    "status", "anomaly_score", "flag_reason", "novelty_score", "typology_score",
    "pii_bytes",
]


def bucket_for(timestamp_ns: int) -> str:
    """UTC day partition key. Bounds partition size at roughly one day of writes."""
    return datetime.fromtimestamp(timestamp_ns / 1e9, tz=timezone.utc).strftime("%Y-%m-%d")


class CassandraLedger:
    """Durable append-only commitment ledger."""

    def __init__(self, hosts=None, keyspace: str = KEYSPACE, connect: bool = True):
        self._hosts = hosts or [
            h.strip() for h in os.environ.get("CASSANDRA_HOSTS", "cassandra").split(",")
        ]
        self._keyspace = keyspace
        self._cluster = None
        self._session = None
        self._stmts = {}
        self._lock = threading.Lock()
        self.available = False
        self._write_errors = 0
        self._writes = 0
        if connect:
            self.connect()

    # -- lifecycle ------------------------------------------------------------

    def connect(self, retries: int = 12, backoff: float = 5.0) -> bool:
        """Connect and apply DDL. Cassandra takes ~30s to open its CQL port."""
        if not _DRIVER:
            return False

        auth = None
        user = os.environ.get("CASSANDRA_USERNAME")
        pwd = os.environ.get("CASSANDRA_PASSWORD")
        if user and pwd:
            auth = PlainTextAuthProvider(username=user, password=pwd)

        for attempt in range(1, retries + 1):
            try:
                self._cluster = Cluster(
                    contact_points=self._hosts,
                    auth_provider=auth,
                    load_balancing_policy=DCAwareRoundRobinPolicy(local_dc="dc1"),
                    reconnection_policy=ExponentialReconnectionPolicy(1.0, 60.0),
                    protocol_version=4,
                    connect_timeout=10,
                )
                self._session = self._cluster.connect()
                self._apply_ddl()
                self._prepare()
                self.available = True
                logger.info("Cassandra ledger ready - hosts=%s keyspace=%s",
                            self._hosts, self._keyspace)
                return True
            except (NoHostAvailable, Exception) as exc:
                logger.warning("Cassandra connect attempt %d/%d failed: %s: %s",
                               attempt, retries, type(exc).__name__, exc)
                self._teardown()
                if attempt < retries:
                    time.sleep(backoff)

        logger.error("Cassandra unreachable after %d attempts - memory-only mode", retries)
        return False

    def _teardown(self):
        try:
            if self._cluster:
                self._cluster.shutdown()
        except Exception:
            pass
        self._cluster = None
        self._session = None
        self.available = False

    def _apply_ddl(self):
        for ddl in (_DDL_KEYSPACE, _DDL_BY_BUCKET, _DDL_BY_TXN, _DDL_HEAD):
            self._session.execute(ddl)
        self._session.set_keyspace(self._keyspace)
        logger.info("Cassandra schema applied (keyspace=%s)", self._keyspace)

    def _prepare(self):
        cols = ", ".join(_COLUMNS)
        marks = ", ".join(["?"] * len(_COLUMNS))
        for table in ("commitments_by_bucket", "commitments_by_txn"):
            stmt = self._session.prepare(
                "INSERT INTO %s.%s (%s) VALUES (%s)" % (self._keyspace, table, cols, marks)
            )
            stmt.consistency_level = ConsistencyLevel.ONE
            self._stmts[table] = stmt

        self._stmts["by_txn"] = self._session.prepare(
            "SELECT * FROM %s.commitments_by_txn WHERE txn_id = ?" % self._keyspace
        )
        self._stmts["by_bucket"] = self._session.prepare(
            "SELECT * FROM %s.commitments_by_bucket WHERE bucket = ? LIMIT ?" % self._keyspace
        )
        self._stmts["status"] = self._session.prepare(
            "UPDATE %s.commitments_by_txn SET status = ? WHERE txn_id = ?" % self._keyspace
        )
        self._stmts["head_get"] = self._session.prepare(
            "SELECT seq, chain_hash FROM %s.ledger_head WHERE shard = ?" % self._keyspace
        )
        self._stmts["head_set"] = self._session.prepare(
            "UPDATE %s.ledger_head SET seq = ?, chain_hash = ?, updated_ns = ? "
            "WHERE shard = ?" % self._keyspace
        )

    def close(self):
        self._teardown()

    # -- writes ---------------------------------------------------------------

    def _row(self, rec: dict) -> tuple:
        ts = int(rec.get("timestamp_ns", 0))
        values = {
            "txn_id": rec.get("txn_id", ""),
            "bucket": bucket_for(ts),
            "timestamp_ns": ts,
            "seq": int(rec.get("seq", 0)),
            "binding_hash": rec.get("binding_hash", ""),
            "chain_hash": rec.get("chain_hash", ""),
            "prev_chain_hash": rec.get("prev_chain_hash", ""),
            "commitment_b64": rec.get("commitment_b64", ""),
            "signature_b64": rec.get("signature_b64", ""),
            "signing_key_id": rec.get("signing_key_id", ""),
            "pubkey_fingerprint": rec.get("pubkey_fingerprint", ""),
            "size_kb": float(rec.get("size_kb", 0.0)),
            "lwe_params": json.dumps(rec.get("lwe_params", {})),
            "account_hash": rec.get("account_hash", ""),
            "txn_type": rec.get("txn_type", ""),
            "status": rec.get("status", ""),
            "anomaly_score": float(rec.get("anomaly_score", 0.0)),
            "flag_reason": rec.get("flag_reason", "NONE"),
            "novelty_score": float(rec.get("novelty_score", 0.0)),
            "typology_score": float(rec.get("typology_score", 0.0)),
            "pii_bytes": int(rec.get("pii_bytes", 0)),
        }
        return tuple(values[c] for c in _COLUMNS)

    def write(self, rec: dict) -> bool:
        """Write one commitment to both query tables. Idempotent on txn_id."""
        if not self.available:
            return False
        row = self._row(rec)
        try:
            for table in ("commitments_by_bucket", "commitments_by_txn"):
                self._session.execute(self._stmts[table], row)
            with self._lock:
                self._writes += 1
            return True
        except Exception as exc:
            with self._lock:
                self._write_errors += 1
            if self._write_errors <= 5 or self._write_errors % 100 == 0:
                logger.error("Cassandra write failed for %s: %s: %s",
                             rec.get("txn_id"), type(exc).__name__, exc)
            return False

    def set_status(self, txn_id: str, status: str) -> bool:
        if not self.available:
            return False
        try:
            self._session.execute(self._stmts["status"], (status, txn_id))
            return True
        except Exception as exc:
            logger.error("Cassandra status update failed for %s: %s", txn_id, exc)
            return False

    # -- hash-chain head ------------------------------------------------------

    def load_head(self, shard: str = "default"):
        """Return (seq, chain_hash) so a restarted prover extends the chain."""
        if not self.available:
            return None
        try:
            rows = list(self._session.execute(self._stmts["head_get"], (shard,)))
            if rows:
                return int(rows[0].seq), rows[0].chain_hash
        except Exception as exc:
            logger.error("Cassandra head read failed: %s", exc)
        return None

    def save_head(self, seq: int, chain_hash: str, shard: str = "default") -> bool:
        if not self.available:
            return False
        try:
            self._session.execute(
                self._stmts["head_set"], (int(seq), chain_hash, time.time_ns(), shard)
            )
            return True
        except Exception as exc:
            logger.error("Cassandra head write failed: %s", exc)
            return False

    # -- reads ----------------------------------------------------------------

    @staticmethod
    def _to_dict(row) -> dict:
        d = dict(row._asdict()) if hasattr(row, "_asdict") else dict(row)
        raw = d.get("lwe_params") or "{}"
        try:
            d["lwe_params"] = json.loads(raw)
        except (ValueError, TypeError):
            d["lwe_params"] = {}
        return d

    def get(self, txn_id: str) -> Optional[dict]:
        if not self.available:
            return None
        try:
            rows = list(self._session.execute(self._stmts["by_txn"], (txn_id,)))
            return self._to_dict(rows[0]) if rows else None
        except Exception as exc:
            logger.error("Cassandra point read failed for %s: %s", txn_id, exc)
            return None

    def recent(self, limit: int = 100, days: int = 2) -> list:
        """Newest-first ledger scan across the last `days` day-partitions."""
        if not self.available:
            return []
        out = []
        now_ns = time.time_ns()
        try:
            for d in range(days):
                bucket = bucket_for(now_ns - d * 86_400_000_000_000)
                rows = self._session.execute(self._stmts["by_bucket"], (bucket, limit))
                out.extend(self._to_dict(r) for r in rows)
                if len(out) >= limit:
                    break
        except Exception as exc:
            logger.error("Cassandra range read failed: %s", exc)
        out.sort(key=lambda r: r.get("timestamp_ns", 0), reverse=True)
        return out[:limit]

    def count(self, days: int = 2) -> int:
        """Row count across recent partitions. Bounded scan, never COUNT(*) unfiltered."""
        if not self.available:
            return 0
        total = 0
        now_ns = time.time_ns()
        try:
            for d in range(days):
                bucket = bucket_for(now_ns - d * 86_400_000_000_000)
                rows = self._session.execute(
                    "SELECT COUNT(*) AS c FROM %s.commitments_by_bucket WHERE bucket = %%s"
                    % self._keyspace, (bucket,)
                )
                total += int(list(rows)[0].c)
        except Exception as exc:
            logger.error("Cassandra count failed: %s", exc)
        return total

    def stats(self) -> dict:
        with self._lock:
            return {
                "available": self.available,
                "hosts": self._hosts,
                "keyspace": self._keyspace,
                "writes": self._writes,
                "write_errors": self._write_errors,
            }


_ledger: Optional[CassandraLedger] = None
_ledger_lock = threading.Lock()


def get_ledger(connect: bool = True) -> CassandraLedger:
    global _ledger
    with _ledger_lock:
        if _ledger is None:
            _ledger = CassandraLedger(connect=connect)
    return _ledger
