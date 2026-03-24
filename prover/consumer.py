"""
verifier/kafka_client/consumer.py — Verifier-side Kafka Consumer
ZEROAUDIT Verifier Service

Consumes from the committed and anomalies topics.
Feeds records to the dashboard via an in-memory ring buffer
and to the ExternalVerifier for independent signature checks.
Zero PII at all times.

StubVerifierConsumer has been REMOVED.
If Kafka is unavailable, the service raises RuntimeError — no silent fallback.
"""

import json
import time
import logging
import threading
from collections import deque
from typing import Optional, Callable

from prover.config.settings import settings

logger = logging.getLogger("zeroaudit.verifier.kafka_client")

try:
    from kafka import KafkaConsumer as _KafkaConsumer
    _KAFKA_AVAILABLE = True
except ImportError:
    _KAFKA_AVAILABLE = False
    logger.error("kafka-python not installed — verifier consumer cannot start")


# ── Ring Buffer ───────────────────────────────────────────────────────────────

class RingBuffer:
    """Thread-safe fixed-size ring buffer for live dashboard feed."""

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


# ── TPS Sliding Window ────────────────────────────────────────────────────────

class TPSMeter:
    """Sliding window TPS meter — real message timestamps, no fake values."""

    def __init__(self, window_seconds: int = 30):
        self._window = window_seconds
        self._timestamps: deque = deque()
        self._lock = threading.Lock()

    def record(self):
        now = time.time()
        with self._lock:
            self._timestamps.append(now)
            cutoff = now - self._window
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()

    def tps(self) -> float:
        now = time.time()
        with self._lock:
            cutoff = now - self._window
            count = sum(1 for ts in self._timestamps if ts >= cutoff)
        return round(count / self._window, 1)

    def samples(self, n_seconds: int = 30) -> list[float]:
        """Return per-second TPS for the last n_seconds seconds."""
        now = time.time()
        buckets = [0] * n_seconds
        with self._lock:
            for ts in self._timestamps:
                age = now - ts
                if 0 <= age < n_seconds:
                    bucket_idx = n_seconds - 1 - int(age)
                    if 0 <= bucket_idx < n_seconds:
                        buckets[bucket_idx] += 1
        return [float(b) for b in buckets]


# ── Verifier Kafka Consumer ───────────────────────────────────────────────────

class VerifierKafkaConsumer:
    """
    Subscribes to:
      - zeroaudit.transactions.committed  (all verified commitments)
      - zeroaudit.anomalies               (quarantined transactions)

    Maintains:
      - committed_buffer: RingBuffer of recent verified records
      - anomaly_buffer:   RingBuffer of recent anomaly records
      - tps_meter:        Real sliding window TPS from actual message timestamps
      - Calls on_committed / on_anomaly callbacks for downstream processors
    """

    def __init__(
        self,
        on_committed: Optional[Callable] = None,
        on_anomaly: Optional[Callable] = None,
        buffer_size: int = 500,
    ):
        self.committed_buffer = RingBuffer(buffer_size)
        self.anomaly_buffer = RingBuffer(buffer_size)
        self._tps_meter = TPSMeter(window_seconds=30)
        self._on_committed = on_committed
        self._on_anomaly = on_anomaly
        self._consumer: Optional[object] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stats = {
            "committed_received": 0,
            "anomalies_received": 0,
            "errors": 0,
            "start_time": time.time(),
        }

    def _connect(self):
        if not _KAFKA_AVAILABLE:
            raise RuntimeError(
                "kafka-python not installed. Cannot start VerifierKafkaConsumer. "
                "Install with: pip install kafka-python"
            )
        try:
            self._consumer = _KafkaConsumer(
                settings.KAFKA_TOPIC_COMMITTED,
                settings.KAFKA_TOPIC_ANOMALIES,
                bootstrap_servers=settings.KAFKA_BOOTSTRAP,
                group_id="zeroaudit-verifier-dashboard",
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                auto_offset_reset="latest",
                enable_auto_commit=True,
                consumer_timeout_ms=1000,
            )
            logger.info(f"Verifier consumer connected to Kafka @ {settings.KAFKA_BOOTSTRAP}")
        except Exception as e:
            raise RuntimeError(f"Verifier Kafka connect failed: {e}") from e

    def _process(self, topic: str, record: dict):
        """Route record to correct buffer and callback."""
        try:
            # Enforce zero PII
            assert record.get("pii_bytes", 0) == 0, f"PII DETECTED on {record.get('txn_id')} — dropping"

            # Record real timestamp for TPS meter
            self._tps_meter.record()

            if topic == settings.KAFKA_TOPIC_COMMITTED:
                self.committed_buffer.push(record)
                self._stats["committed_received"] += 1
                if self._on_committed:
                    self._on_committed(record)

            elif topic == settings.KAFKA_TOPIC_ANOMALIES:
                self.anomaly_buffer.push(record)
                self._stats["anomalies_received"] += 1
                if self._on_anomaly:
                    self._on_anomaly(record)

        except AssertionError as e:
            self._stats["errors"] += 1
            logger.critical(f"PII ASSERTION FAILED: {e}")
        except Exception as e:
            self._stats["errors"] += 1
            logger.error(f"Record processing error: {e}")

    def _consume_loop(self):
        while self._running:
            if not self._consumer:
                time.sleep(1)
                continue
            try:
                records = self._consumer.poll(timeout_ms=500)
                for tp, messages in records.items():
                    for msg in messages:
                        self._process(tp.topic, msg.value)
            except Exception as e:
                self._stats["errors"] += 1
                logger.error(f"Consume loop error: {e}")
                time.sleep(1)

    def start(self):
        self._running = True
        self._connect()
        self._thread = threading.Thread(
            target=self._consume_loop,
            name="verifier-kafka-consumer",
            daemon=True,
        )
        self._thread.start()
        logger.info("VerifierKafkaConsumer started")

    def stop(self):
        self._running = False
        if self._consumer:
            self._consumer.close()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info(f"VerifierKafkaConsumer stopped. Stats: {self._stats}")

    def tps(self) -> float:
        """Real TPS from sliding window over actual message timestamps."""
        return self._tps_meter.tps()

    def tps_samples(self, n_seconds: int = 30) -> list[float]:
        """Per-second TPS samples for chart rendering."""
        return self._tps_meter.samples(n_seconds)

    def stats(self) -> dict:
        return {
            **self._stats,
            "tps": self.tps(),
            "committed_buffer_size": len(self.committed_buffer),
            "anomaly_buffer_size": len(self.anomaly_buffer),
            "pii_bytes": 0,
        }

    def recent_committed(self, n: int = 50) -> list:
        return self.committed_buffer.snapshot(n)

    def recent_anomalies(self, n: int = 20) -> list:
        return self.anomaly_buffer.snapshot(n)


# ── Factory ────────────────────────────────────────────────────────────────────

def get_verifier_consumer(
    on_committed: Optional[Callable] = None,
    on_anomaly: Optional[Callable] = None,
) -> VerifierKafkaConsumer:
    """
    Returns a real VerifierKafkaConsumer.
    Raises RuntimeError if Kafka is unavailable — no stub fallback.
    """
    if not _KAFKA_AVAILABLE:
        raise RuntimeError(
            "kafka-python is not installed. Cannot create verifier consumer. "
            "Run: pip install kafka-python"
        )
    return VerifierKafkaConsumer(on_committed, on_anomaly)