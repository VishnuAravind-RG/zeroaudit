"""
verifier/kafka_client/consumer.py - Verifier-side Kafka consumer

Consumes the public committed/anomaly topics, runs external verification on
every record, and keeps bounded ring buffers for the audit API.

Metrics that mean what they say
-------------------------------
An earlier revision reported `kafka_lag_ms` as the wall time `poll()` took to
return - roughly the poll timeout when idle and near zero under load, i.e. the
inverse of lag. And `tps` was total-records-since-start divided by uptime, a
lifetime average that a Kafka replay pins high for hours. Both are now real:

  lag_records   sum over assigned partitions of (end_offset - position),
                read from the broker. Actual backlog.
  lag_ms        wall clock minus the event time of the last consumed record.
                Actual staleness.
  tps           count over a trailing 10-second window.
  tps_samples   real per-second buckets for the dashboard sparkline, not [].
"""

import json
import time
import uuid
import logging
import threading
from collections import deque, defaultdict
from typing import Optional, Callable

from prover.config.settings import settings

logger = logging.getLogger("zeroaudit.verifier.kafka")

try:
    from kafka import KafkaConsumer as _KafkaConsumer
    _KAFKA = True
except ImportError:
    _KAFKA = False
    logger.error("kafka-python not installed - the verifier cannot consume the commitment topic")


class RingBuffer:
    """Bounded, thread-safe, newest-last."""

    def __init__(self, maxlen: int = 500):
        self._buf: deque = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def push(self, item: dict):
        with self._lock:
            self._buf.append(item)

    def snapshot(self, n: int = None) -> list:
        with self._lock:
            items = list(self._buf)
        return items[-n:] if n else items

    def __len__(self):
        with self._lock:
            return len(self._buf)


class VerifierKafkaConsumer:
    """Kafka consumer for the external DMZ verifier."""

    # A fresh group per process start. Reusing a stable group lets a stale
    # member from a previous container hold the partition assignment, leaving
    # the new consumer connected but assigned nothing and reading zero messages.
    _GROUP_SUFFIX = uuid.uuid4().hex[:8]

    def __init__(self, on_committed: Callable = None, on_anomaly: Callable = None,
                 buffer_size: int = 500):
        self.committed_buffer = RingBuffer(buffer_size)
        self.anomaly_buffer = RingBuffer(buffer_size)
        self._on_committed = on_committed
        self._on_anomaly = on_anomaly
        self._consumer = None
        self._running = False
        self._thread = None
        self._lock = threading.Lock()
        self._group_id = "zeroaudit-verifier-%s" % self._GROUP_SUFFIX

        self._events: deque = deque(maxlen=20000)     # consume times, for TPS
        self._per_second = defaultdict(int)           # epoch second -> count
        self._stats = {
            "committed_received": 0,
            "anomalies_received": 0,
            "verification_failures": 0,
            "errors": 0,
            "start_time": time.time(),
            "lag_records": 0,
            "lag_ms": 0.0,
            "last_event_ns": 0,
            "connected": False,
        }

    # -- connection -----------------------------------------------------------

    def _connect(self) -> bool:
        if not _KAFKA:
            logger.error("kafka-python is not installed")
            return False
        try:
            logger.info("connecting to Kafka @ %s (group=%s)",
                        settings.KAFKA_BOOTSTRAP, self._group_id)
            self._consumer = _KafkaConsumer(
                settings.KAFKA_TOPIC_COMMITTED,
                settings.KAFKA_TOPIC_ANOMALIES,
                bootstrap_servers=settings.KAFKA_BOOTSTRAP,
                group_id=self._group_id,
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                # Read the topic from the beginning so the verifier catches up
                # on anything published before it started.
                auto_offset_reset="earliest",
                enable_auto_commit=True,
                consumer_timeout_ms=1000,
                request_timeout_ms=15000,
                session_timeout_ms=10000,
            )
            with self._lock:
                self._stats["connected"] = True
            logger.info("verifier subscribed to %s, %s",
                        settings.KAFKA_TOPIC_COMMITTED, settings.KAFKA_TOPIC_ANOMALIES)
            return True
        except Exception as exc:
            logger.error("Kafka connect failed: %s: %s", type(exc).__name__, exc)
            self._consumer = None
            with self._lock:
                self._stats["connected"] = False
            return False

    # -- record handling ------------------------------------------------------

    def _process(self, topic: str, record: dict):
        try:
            if record.get("pii_bytes", 0) != 0:
                with self._lock:
                    self._stats["errors"] += 1
                logger.critical("PII ASSERTION FAILED on %s: pii_bytes=%s",
                                record.get("txn_id"), record.get("pii_bytes"))
                return

            now = time.time()
            with self._lock:
                self._events.append(now)
                self._per_second[int(now)] += 1
                cutoff = int(now) - 120
                for sec in [s for s in self._per_second if s < cutoff]:
                    del self._per_second[sec]
                ts_ns = int(record.get("timestamp_ns", 0))
                if ts_ns:
                    self._stats["last_event_ns"] = ts_ns
                    self._stats["lag_ms"] = round(max(now * 1e9 - ts_ns, 0) / 1e6, 1)

            if topic == settings.KAFKA_TOPIC_COMMITTED:
                self.committed_buffer.push(record)
                with self._lock:
                    self._stats["committed_received"] += 1
                if self._on_committed:
                    result = self._on_committed(record)
                    if isinstance(result, dict) and not result.get("verified", True):
                        with self._lock:
                            self._stats["verification_failures"] += 1

            elif topic == settings.KAFKA_TOPIC_ANOMALIES:
                self.anomaly_buffer.push(record)
                with self._lock:
                    self._stats["anomalies_received"] += 1
                if self._on_anomaly:
                    self._on_anomaly(record)

        except Exception as exc:
            with self._lock:
                self._stats["errors"] += 1
            logger.error("record processing error: %s: %s", type(exc).__name__, exc,
                         exc_info=True)

    def _refresh_lag(self):
        """Read true backlog from the broker: end offset minus our position."""
        try:
            partitions = self._consumer.assignment()
            if not partitions:
                return
            end_offsets = self._consumer.end_offsets(list(partitions))
            lag = 0
            for tp in partitions:
                position = self._consumer.position(tp)
                lag += max(end_offsets.get(tp, position) - position, 0)
            with self._lock:
                self._stats["lag_records"] = lag
        except Exception as exc:
            logger.debug("lag probe failed: %s", exc)

    def _consume_loop(self):
        backoff = 2.0
        last_lag_probe = 0.0

        while self._running:
            if self._consumer is None:
                if not self._connect():
                    logger.warning("retrying Kafka connection in %.0fs", backoff)
                    time.sleep(backoff)
                    # Exponential backoff handles the startup race where Kafka
                    # reports healthy before it is actually serving metadata.
                    backoff = min(backoff * 2, 30.0)
                    continue
                backoff = 2.0

            try:
                for tp, messages in self._consumer.poll(timeout_ms=500).items():
                    for msg in messages:
                        self._process(tp.topic, msg.value)

                now = time.monotonic()
                if now - last_lag_probe > 5.0:
                    self._refresh_lag()
                    last_lag_probe = now

            except Exception as exc:
                with self._lock:
                    self._stats["errors"] += 1
                    self._stats["connected"] = False
                logger.error("consume loop error: %s: %s", type(exc).__name__, exc)
                try:
                    self._consumer.close()
                except Exception:
                    pass
                self._consumer = None
                time.sleep(2)

        logger.info("verifier consumer loop exiting")

    # -- lifecycle ------------------------------------------------------------

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._consume_loop, name="verifier-kafka-consumer", daemon=True)
        self._thread.start()
        logger.info("verifier consumer thread started (id=%s)", self._thread.ident)

    def stop(self):
        self._running = False
        if self._consumer:
            try:
                self._consumer.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)

    # -- metrics --------------------------------------------------------------

    def tps(self, window: int = 10) -> float:
        """Throughput over a trailing window - not a lifetime average."""
        cutoff = time.time() - window
        with self._lock:
            recent = sum(1 for t in self._events if t > cutoff)
        return round(recent / window, 2)

    def tps_samples(self, n_seconds: int = 30) -> list:
        """Real per-second counts, oldest first, zero-filled for quiet seconds."""
        now = int(time.time())
        with self._lock:
            return [self._per_second.get(sec, 0)
                    for sec in range(now - n_seconds + 1, now + 1)]

    def stats(self) -> dict:
        with self._lock:
            snapshot = dict(self._stats)
        snapshot.update({
            "tps": self.tps(),
            "group_id": self._group_id,
            "committed_buffer_size": len(self.committed_buffer),
            "anomaly_buffer_size": len(self.anomaly_buffer),
            "thread_alive": self._thread.is_alive() if self._thread else False,
            "uptime_s": round(time.time() - snapshot["start_time"], 1),
            "pii_bytes": 0,
        })
        return snapshot

    def recent_committed(self, n: int = 50) -> list:
        return self.committed_buffer.snapshot(n)

    def recent_anomalies(self, n: int = 20) -> list:
        return self.anomaly_buffer.snapshot(n)


def get_verifier_consumer(on_committed=None, on_anomaly=None):
    if not _KAFKA:
        raise RuntimeError(
            "kafka-python is not installed - the verifier cannot consume the "
            "commitment topic. Install requirements.txt."
        )
    return VerifierKafkaConsumer(on_committed, on_anomaly)
