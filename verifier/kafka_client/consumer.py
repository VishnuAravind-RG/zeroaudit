"""
verifier/kafka_client/consumer.py — Verifier-side Kafka Consumer
ZEROAUDIT Verifier Service
"""

import json
import time
import uuid
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
    logger.warning("kafka-python not installed — verifier consumer running in stub mode")


class RingBuffer:
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
    _GROUP_SUFFIX = uuid.uuid4().hex[:8]

    def __init__(self, on_committed=None, on_anomaly=None, buffer_size=500):
        self.committed_buffer = RingBuffer(buffer_size)
        self.anomaly_buffer = RingBuffer(buffer_size)
        self._on_committed = on_committed
        self._on_anomaly = on_anomaly
        self._consumer = None
        self._running = False
        self._thread = None
        self._stats = {
            "committed_received": 0,
            "anomalies_received": 0,
            "errors": 0,
            "start_time": time.time(),
            "kafka_lag_ms": 0.0,
        }
        self._group_id = f"zeroaudit-verifier-{self._GROUP_SUFFIX}"

    def _connect(self) -> bool:
        if not _KAFKA_AVAILABLE:
            logger.error("kafka-python is not installed")
            return False
        try:
            logger.info(f"Connecting to Kafka @ {settings.KAFKA_BOOTSTRAP} (group={self._group_id})")
            self._consumer = _KafkaConsumer(
                settings.KAFKA_TOPIC_COMMITTED,
                settings.KAFKA_TOPIC_ANOMALIES,
                bootstrap_servers=settings.KAFKA_BOOTSTRAP,
                group_id=self._group_id,
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                auto_offset_reset="earliest",
                enable_auto_commit=True,
                consumer_timeout_ms=1000,
                request_timeout_ms=15000,
                session_timeout_ms=10000,
            )
            logger.info(f"Verifier consumer connected — topics: {settings.KAFKA_TOPIC_COMMITTED}, {settings.KAFKA_TOPIC_ANOMALIES}")
            return True
        except Exception as e:
            logger.error(f"Kafka connect failed: {type(e).__name__}: {e}")
            self._consumer = None
            return False

    def _process(self, topic: str, record: dict):
        try:
            assert record.get("pii_bytes", 0) == 0, "PII DETECTED"
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
            logger.critical(f"PII ASSERTION FAILED on {record.get('txn_id')}: {e}")
        except Exception as e:
            self._stats["errors"] += 1
            logger.error(f"Record processing error: {e}", exc_info=True)

    def _consume_loop(self):
        backoff = 2.0
        while self._running:
            if self._consumer is None:
                if not self._connect():
                    logger.warning(f"Retrying in {backoff:.0f}s ...")
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)
                    continue
                backoff = 2.0
            try:
                t0 = time.monotonic()
                records = self._consumer.poll(timeout_ms=500)
                self._stats["kafka_lag_ms"] = round((time.monotonic() - t0) * 1000, 1)
                for tp, messages in records.items():
                    for msg in messages:
                        self._process(tp.topic, msg.value)
            except Exception as e:
                self._stats["errors"] += 1
                logger.error(f"Consume loop error: {type(e).__name__}: {e}", exc_info=True)
                try:
                    self._consumer.close()
                except Exception:
                    pass
                self._consumer = None
                time.sleep(2)
        logger.info("VerifierKafkaConsumer loop exiting")

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._consume_loop, name="verifier-kafka-consumer", daemon=True)
        self._thread.start()
        logger.info(f"VerifierKafkaConsumer thread started (id={self._thread.ident})")

    def stop(self):
        self._running = False
        if self._consumer:
            try:
                self._consumer.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)

    def tps(self) -> float:
        elapsed = time.time() - self._stats["start_time"]
        total = self._stats["committed_received"] + self._stats["anomalies_received"]
        return round(total / max(elapsed, 1), 1)

    def tps_samples(self, n_seconds: int = 30) -> list:
        return []

    def stats(self) -> dict:
        return {**self._stats, "tps": self.tps(),
                "committed_buffer_size": len(self.committed_buffer),
                "anomaly_buffer_size": len(self.anomaly_buffer),
                "thread_alive": self._thread.is_alive() if self._thread else False,
                "pii_bytes": 0}

    def recent_committed(self, n: int = 50) -> list:
        return self.committed_buffer.snapshot(n)

    def recent_anomalies(self, n: int = 20) -> list:
        return self.anomaly_buffer.snapshot(n)


class StubVerifierConsumer:
    def __init__(self, on_committed=None, on_anomaly=None, tps=8.0, anomaly_rate=0.07, buffer_size=500):
        self.committed_buffer = RingBuffer(buffer_size)
        self.anomaly_buffer = RingBuffer(buffer_size)
        self._on_committed = on_committed
        self._on_anomaly = on_anomaly
        self._tps = tps
        self._anomaly_rate = anomaly_rate
        self._running = False
        self._thread = None
        self._count = 0
        self._stats = {"committed_received": 0, "anomalies_received": 0, "errors": 0,
                       "start_time": time.time(), "kafka_lag_ms": 0.0}

    def start(self):
        import random, uuid as _uuid
        self._running = True
        def _loop():
            interval = 1.0 / self._tps
            types = ["RTGS", "NEFT", "WIRE_TRANSFER", "TRADE_SETTLEMENT", "FX_CONVERSION"]
            while self._running:
                self._count += 1
                is_anomaly = random.random() < self._anomaly_rate
                score = round(random.uniform(0.82, 0.99) if is_anomaly else random.uniform(0.0, 0.35), 4)
                record = {
                    "txn_id": f"TXN-{'ANOM' if is_anomaly else _uuid.uuid4().hex[:8].upper()}",
                    "binding_hash": _uuid.uuid4().hex * 2,
                    "size_kb": round(random.uniform(6.5, 9.2), 1),
                    "lwe_params": {"n": 256, "k": 2, "q": 3329, "eta": 2},
                    "timestamp_ns": time.time_ns(), "pii_bytes": 0,
                    "account_hash": _uuid.uuid4().hex, "txn_type": random.choice(types),
                    "status": "QUARANTINED" if is_anomaly else "VERIFIED",
                    "anomaly_score": score,
                    "flag_reason": random.choice(["OFAC_SANCTION_LIST", "RBI_FLAG_2024", "BENFORD_VIOLATION"]) if is_anomaly else "NONE",
                    "pipeline_stage": "STUB",
                }
                self.committed_buffer.push(record)
                self._stats["committed_received"] += 1
                if self._on_committed:
                    self._on_committed(record)
                if is_anomaly:
                    self.anomaly_buffer.push(record)
                    self._stats["anomalies_received"] += 1
                    if self._on_anomaly:
                        self._on_anomaly(record)
                time.sleep(interval)
        self._thread = threading.Thread(target=_loop, daemon=True, name="stub-verifier-consumer")
        self._thread.start()
        logger.info(f"StubVerifierConsumer started at {self._tps} TPS")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    def tps(self) -> float:
        return self._tps

    def tps_samples(self, n_seconds: int = 30) -> list:
        return []

    def stats(self) -> dict:
        return {**self._stats, "mode": "stub", "tps": self._tps,
                "committed_buffer_size": len(self.committed_buffer),
                "anomaly_buffer_size": len(self.anomaly_buffer),
                "thread_alive": self._thread.is_alive() if self._thread else False,
                "pii_bytes": 0}

    def recent_committed(self, n: int = 50) -> list:
        return self.committed_buffer.snapshot(n)

    def recent_anomalies(self, n: int = 20) -> list:
        return self.anomaly_buffer.snapshot(n)


def get_verifier_consumer(on_committed=None, on_anomaly=None):
    if _KAFKA_AVAILABLE:
        return VerifierKafkaConsumer(on_committed, on_anomaly)
    return StubVerifierConsumer(on_committed, on_anomaly)


ledger = []
anomalies = []
