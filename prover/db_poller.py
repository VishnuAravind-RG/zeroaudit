"""
db_poller.py — Cassandra LSM-Tree CDC Poller
ZEROAUDIT Prover Service

Polls the Cassandra commitments table for new records and publishes
them to the Kafka committed topic. Acts as the bridge between
Cassandra LSM writes and the Kafka event stream.

In production: replace polling with Cassandra CDC + Debezium connector.
This poller is the fallback / dev mode alternative.
"""

import json
import time
import logging
import threading
from typing import Optional, Callable
from datetime import datetime, timedelta, timezone

from .config.settings import settings

logger = logging.getLogger("zeroaudit.db_poller")

try:
    from cassandra.cluster import Cluster
    from cassandra.auth import PlainTextAuthProvider
    from cassandra.policies import DCAwareRoundRobinPolicy
    from cassandra import ConsistencyLevel
    from cassandra.query import SimpleStatement
    _CASSANDRA_AVAILABLE = True
except ImportError:
    _CASSANDRA_AVAILABLE = False
    logger.warning("cassandra-driver not installed — db_poller will run in stub mode")

try:
    from kafka import KafkaProducer
    _KAFKA_AVAILABLE = True
except ImportError:
    _KAFKA_AVAILABLE = False


# ── Cassandra Connection ───────────────────────────────────────────────────────

def build_cassandra_session():
    """Build and return a Cassandra session. Returns None if unavailable."""
    if not _CASSANDRA_AVAILABLE:
        return None
    try:
        auth = PlainTextAuthProvider(
            username=settings.CASSANDRA_USERNAME,
            password=settings.CASSANDRA_PASSWORD,
        )
        cluster = Cluster(
            contact_points=settings.CASSANDRA_HOSTS,
            auth_provider=auth,
            load_balancing_policy=DCAwareRoundRobinPolicy(local_dc="dc1"),
            connect_timeout=10,
        )
        session = cluster.connect(settings.CASSANDRA_KEYSPACE)
        logger.info(f"Cassandra connected: {settings.CASSANDRA_HOSTS} / {settings.CASSANDRA_KEYSPACE}")
        return session
    except Exception as e:
        logger.error(f"Cassandra connection failed: {e}")
        return None


# ── DB Poller ──────────────────────────────────────────────────────────────────

class DBPoller:
    """
    Polls Cassandra for new commitment records and:
      1. Publishes them to Kafka committed topic
      2. Calls registered on_record callbacks (for in-process consumers)

    Polling strategy:
      - Tracks last_polled_timestamp_ns in memory
      - Queries: SELECT * FROM commitments WHERE date_bucket = ? AND timestamp_ns > ?
      - Runs in a background daemon thread
    """

    def __init__(
        self,
        session=None,
        poll_interval_ms: int = None,
        on_record: Optional[Callable] = None,
    ):
        self._session = session or build_cassandra_session()
        self._poll_interval = (poll_interval_ms or settings.PROVER_POLL_INTERVAL_MS) / 1000.0
        self._on_record = on_record
        self._producer = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_ts_ns: int = 0
        self._stats = {
            "polls": 0,
            "records_found": 0,
            "published": 0,
            "errors": 0,
        }

        # Prepared statements
        self._select_stmt = None
        if self._session:
            self._prepare_statements()

        # Kafka producer
        if _KAFKA_AVAILABLE:
            try:
                self._producer = KafkaProducer(
                    bootstrap_servers=settings.KAFKA_BOOTSTRAP,
                    value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                    acks=1,
                    linger_ms=5,
                )
                logger.info("DBPoller Kafka producer connected")
            except Exception as e:
                logger.warning(f"DBPoller Kafka connect failed: {e}")

    def _prepare_statements(self):
        try:
            self._select_stmt = self._session.prepare("""
                SELECT txn_id, binding_hash, commitment_b64, size_kb,
                       lwe_params, timestamp_ns, pii_bytes,
                       account_hash, txn_type, status, anomaly_score
                FROM commitments
                WHERE date_bucket = ?
                  AND timestamp_ns > ?
                LIMIT 500
            """)
            self._select_stmt.consistency_level = ConsistencyLevel.LOCAL_ONE
            logger.debug("Cassandra prepared statements ready")
        except Exception as e:
            logger.error(f"Failed to prepare Cassandra statements: {e}")
            self._select_stmt = None

    def _poll_once(self):
        """Execute one polling cycle."""
        if not self._session or not self._select_stmt:
            return 0

        today = datetime.now(timezone.utc).date()
        records_found = 0

        try:
            rows = self._session.execute(
                self._select_stmt,
                (today, self._last_ts_ns),
                timeout=5.0,
            )

            for row in rows:
                record = {
                    "txn_id": row.txn_id,
                    "binding_hash": row.binding_hash,
                    "commitment_b64": row.commitment_b64,
                    "size_kb": row.size_kb,
                    "lwe_params": json.loads(row.lwe_params or "{}"),
                    "timestamp_ns": row.timestamp_ns,
                    "pii_bytes": row.pii_bytes,
                    "account_hash": row.account_hash,
                    "txn_type": row.txn_type,
                    "status": row.status,
                    "anomaly_score": row.anomaly_score,
                    "pipeline_stage": "CASSANDRA_POLLED",
                }

                # Update watermark
                if row.timestamp_ns > self._last_ts_ns:
                    self._last_ts_ns = row.timestamp_ns

                # Publish to Kafka
                if self._producer:
                    self._producer.send(settings.KAFKA_TOPIC_COMMITTED, value=record)
                    self._stats["published"] += 1

                # Call registered callback
                if self._on_record:
                    try:
                        self._on_record(record)
                    except Exception as cb_err:
                        logger.error(f"on_record callback error: {cb_err}")

                records_found += 1
                self._stats["records_found"] += 1

        except Exception as e:
            self._stats["errors"] += 1
            logger.error(f"Poll error: {e}", exc_info=True)

        return records_found

    def start(self):
        """Start the background polling thread."""
        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="zeroaudit-db-poller",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"DBPoller started (interval={self._poll_interval*1000:.0f}ms)")

    def stop(self):
        """Stop polling and flush Kafka producer."""
        self._running = False
        if self._producer:
            self._producer.flush(timeout=5)
            self._producer.close()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info(f"DBPoller stopped. Stats: {self._stats}")

    def _poll_loop(self):
        while self._running:
            t0 = time.monotonic()
            try:
                found = self._poll_once()
                self._stats["polls"] += 1
                if found > 0:
                    logger.debug(f"Poll found {found} new records")
            except Exception as e:
                self._stats["errors"] += 1
                logger.error(f"Poll loop error: {e}")

            # Sleep for remainder of interval
            elapsed = time.monotonic() - t0
            sleep_time = max(0.0, self._poll_interval - elapsed)
            time.sleep(sleep_time)

    def stats(self) -> dict:
        return dict(self._stats)

    def set_watermark(self, timestamp_ns: int):
        """Manually set the polling watermark (e.g., resume from last checkpoint)."""
        self._last_ts_ns = timestamp_ns
        logger.info(f"Watermark set to {timestamp_ns}")

    def is_running(self) -> bool:
        return self._running and (self._thread is not None) and self._thread.is_alive()


# ── Stub mode for dev without Cassandra ───────────────────────────────────────

class StubDBPoller:
    """
    Stub poller that generates fake records for local development.
    Activated automatically when Cassandra is unavailable.
    """

    def __init__(self, on_record: Optional[Callable] = None, tps: float = 5.0):
        self._on_record = on_record
        self._tps = tps
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._count = 0

    def start(self):
        import uuid, random
        self._running = True

        def _loop():
            interval = 1.0 / self._tps
            types = ["RTGS", "NEFT", "WIRE_TRANSFER", "TRADE_SETTLEMENT"]
            while self._running:
                self._count += 1
                fake_record = {
                    "txn_id": f"TXN-STUB-{uuid.uuid4().hex[:12].upper()}",
                    "binding_hash": uuid.uuid4().hex * 2,
                    "commitment_b64": "STUB_LWE_PAYLOAD",
                    "size_kb": round(random.uniform(6.5, 9.5), 1),
                    "lwe_params": {"n": 256, "k": 2, "q": 3329, "eta": 2},
                    "timestamp_ns": time.time_ns(),
                    "pii_bytes": 0,
                    "account_hash": uuid.uuid4().hex,
                    "txn_type": random.choice(types),
                    "status": "QUARANTINED" if random.random() < 0.07 else "VERIFIED",
                    "anomaly_score": round(random.uniform(0.0, 1.0), 4),
                    "pipeline_stage": "STUB",
                }
                if self._on_record:
                    try:
                        self._on_record(fake_record)
                    except Exception as e:
                        logger.error(f"StubPoller callback error: {e}")
                time.sleep(interval)

        self._thread = threading.Thread(target=_loop, daemon=True, name="stub-db-poller")
        self._thread.start()
        logger.info(f"StubDBPoller started at {self._tps} TPS")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    def stats(self) -> dict:
        return {"mode": "stub", "records_generated": self._count}

    def is_running(self) -> bool:
        return self._running


def get_poller(
    on_record: Optional[Callable] = None,
    session=None,
) -> "DBPoller | StubDBPoller":
    """
    Factory: returns a real DBPoller if Cassandra is available,
    otherwise a StubDBPoller for local dev.
    """
    if _CASSANDRA_AVAILABLE:
        sess = session or build_cassandra_session()
        if sess:
            return DBPoller(session=sess, on_record=on_record)

    logger.warning("Cassandra unavailable — using StubDBPoller")
    return StubDBPoller(on_record=on_record)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG)

    received = []

    def on_rec(r):
        received.append(r)
        print(f"[RECORD] {r['txn_id']} | {r['status']} | score={r['anomaly_score']}")

    poller = get_poller(on_record=on_rec)
    poller.start()

    try:
        time.sleep(10)
    except KeyboardInterrupt:
        pass
    finally:
        poller.stop()
        print(f"\nReceived {len(received)} records")
        print(f"Stats: {poller.stats()}")