"""
prover/consumer.py - ZEROAUDIT Prover Kafka Consumer
Reads raw transactions from zeroaudit.transactions.raw,
generates LWE commitments, publishes to zeroaudit.transactions.committed.
"""

import json
import time
import logging
import threading
from typing import Optional

from .config.settings import settings
from .crypto.commitment import get_store

logger = logging.getLogger("zeroaudit.prover.consumer")

try:
    from kafka import KafkaConsumer, KafkaProducer
    _KAFKA_AVAILABLE = True
except ImportError:
    _KAFKA_AVAILABLE = False
    logger.error("kafka-python not installed")


class ProverConsumer:
    """
    Consumes raw transactions from Kafka, generates LWE commitments,
    publishes commitments to the public topic. Zero raw data ever
    leaves this class into the public topic.
    """

    def __init__(self):
        self._running = False
        self._consumer: Optional[object] = None
        self._producer: Optional[object] = None
        self._store = get_store()
        self._lock = threading.Lock()
        self._stats = {
            "processed": 0,
            "errors": 0,
            "start_time": time.time(),
        }
        self._timestamps = []

    def _connect(self):
        if not _KAFKA_AVAILABLE:
            raise RuntimeError("kafka-python not installed")

        retries = 0
        while retries < 10:
            try:
                self._consumer = KafkaConsumer(
                    settings.KAFKA_TOPIC_INGEST,
                    bootstrap_servers=settings.KAFKA_BOOTSTRAP,
                    group_id=settings.KAFKA_CONSUMER_GROUP,
                    value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                    auto_offset_reset="earliest",
                    enable_auto_commit=True,
                    consumer_timeout_ms=1000,
                )
                self._producer = KafkaProducer(
                    bootstrap_servers=settings.KAFKA_BOOTSTRAP,
                    value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                )
                logger.info(f"ProverConsumer connected to Kafka @ {settings.KAFKA_BOOTSTRAP}")
                return
            except Exception as e:
                retries += 1
                logger.warning(f"Kafka connect attempt {retries}/10 failed: {e}")
                time.sleep(3)

        raise RuntimeError("ProverConsumer could not connect to Kafka after 10 retries")

    def _process(self, record: dict):
        """Generate LWE commitment and publish to committed topic."""
        try:
            txn_id = record.get("txn_id") or record.get("transaction_id", "unknown")
            amount_cents = int(record.get("amount_cents", record.get("amount", 0) * 100))
            account_id = record.get("account_id", "unknown")
            txn_type = record.get("txn_type", record.get("type", "UNKNOWN"))
            anomaly_score = float(record.get("anomaly_score", 0.0))

            # Generate LWE commitment (store handles crypto)
            committed = self._store.add(
                txn_id=txn_id,
                amount_cents=amount_cents,
                account_id=account_id,
                txn_type=txn_type,
                anomaly_score=anomaly_score,
            )

            # Publish zero-PII commitment to public topic
            public_record = committed.to_export_dict()
            self._producer.send(settings.KAFKA_TOPIC_COMMITTED, value=public_record)

            # If anomaly, also publish to anomalies topic
            if anomaly_score >= settings.ANOMALY_THRESHOLD:
                self._producer.send(settings.KAFKA_TOPIC_ANOMALIES, value=public_record)

            with self._lock:
                self._stats["processed"] += 1
                self._timestamps.append(time.time())
                # Keep only last 60 seconds of timestamps
                cutoff = time.time() - 60
                self._timestamps = [t for t in self._timestamps if t > cutoff]

        except Exception as e:
            with self._lock:
                self._stats["errors"] += 1
            logger.error(f"ProverConsumer process error on {record}: {e}")

    def run(self):
        """Main loop - called in a daemon thread by main.py."""
        self._running = True
        try:
            self._connect()
        except RuntimeError as e:
            logger.error(f"ProverConsumer failed to start: {e}")
            return

        logger.info("ProverConsumer run loop started")
        while self._running:
            try:
                records = self._consumer.poll(timeout_ms=500)
                for tp, messages in records.items():
                    for msg in messages:
                        self._process(msg.value)
            except Exception as e:
                self._stats["errors"] += 1
                logger.error(f"ProverConsumer poll error: {e}")
                time.sleep(1)

    def stop(self):
        self._running = False
        if self._consumer:
            self._consumer.close()
        if self._producer:
            self._producer.close()
        logger.info(f"ProverConsumer stopped. Stats: {self._stats}")

    def tps(self) -> float:
        """Real TPS from sliding 30s window."""
        with self._lock:
            cutoff = time.time() - 30
            recent = [t for t in self._timestamps if t > cutoff]
        return round(len(recent) / 30, 1)
