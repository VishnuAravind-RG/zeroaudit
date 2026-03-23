"""
consumer.py — Kafka Consumer for ZEROAUDIT Prover
Reads raw transactions from Kafka, runs them through:
  1. Anomaly detection (score injection)
  2. LWE commitment generation
  3. Ed25519 signing
  4. Cassandra write + Kafka publish to committed topic
"""

import json
import time
import logging
import uuid
from typing import Optional

from .config.settings import settings
from .crypto.commitment import get_store
from .crypto.signature import get_signing_key, sign_commitment
from .models.transaction import RawTransaction, AnomalyFlag

logger = logging.getLogger("zeroaudit.consumer")

try:
    from kafka import KafkaConsumer, KafkaProducer
    _KAFKA_AVAILABLE = True
except ImportError:
    _KAFKA_AVAILABLE = False
    logger.warning("kafka-python not installed — consumer will run in stub mode")


class ProverConsumer:
    """
    Kafka consumer that drives the full prover pipeline:
    RAW TXN → ANOMALY SCORE → LWE COMMIT → SIGN → PUBLISH
    """

    def __init__(self, cassandra_session=None):
        self._store = get_store(cassandra_session)
        self._signing_key = get_signing_key()
        self._consumer: Optional[object] = None
        self._producer: Optional[object] = None
        self._running = False
        self._stats = {
            "processed": 0,
            "committed": 0,
            "quarantined": 0,
            "errors": 0,
            "start_time": time.time(),
        }

    def _connect(self):
        if not _KAFKA_AVAILABLE:
            logger.warning("Kafka not available — skipping connect")
            return

        self._consumer = KafkaConsumer(
            settings.KAFKA_TOPIC_INGEST,
            bootstrap_servers=settings.KAFKA_BOOTSTRAP,
            group_id=settings.KAFKA_CONSUMER_GROUP,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            max_poll_records=settings.KAFKA_MAX_POLL_RECORDS,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )
        self._producer = KafkaProducer(
            bootstrap_servers=settings.KAFKA_BOOTSTRAP,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            acks="all",
            retries=3,
        )
        logger.info(f"Connected to Kafka @ {settings.KAFKA_BOOTSTRAP}")

    def _process_message(self, raw_msg: dict):
        """Full pipeline for a single transaction message."""
        try:
            # 1. Parse raw transaction
            raw_txn = RawTransaction.from_kafka_msg(raw_msg)

            # 2. Get anomaly score (injected from Kafka msg or default 0)
            anomaly_score = float(raw_msg.get("anomaly_score", 0.0))

            # 3. Generate LWE commitment
            record = self._store.add(
                txn_id=raw_txn.txn_id,
                amount_cents=raw_txn.amount_cents,
                account_id=raw_txn.account_id,
                txn_type=raw_txn.txn_type,
                anomaly_score=anomaly_score,
            )

            # 4. Sign the commitment
            envelope = sign_commitment(
                signing_key=self._signing_key,
                txn_id=record.txn_id,
                binding_hash=record.binding_hash,
                timestamp_ns=record.timestamp_ns,
            )

            # 5. Build output payload (zero PII)
            output = {
                **record.to_export_dict(),
                "signature": envelope,
                "pipeline_stage": "SGX_COMMITTED",
            }

            # 6. Publish to committed topic
            if self._producer:
                self._producer.send(settings.KAFKA_TOPIC_COMMITTED, value=output)

                # Also publish to anomalies topic if quarantined
                if record.status == "QUARANTINED":
                    self._producer.send(settings.KAFKA_TOPIC_ANOMALIES, value=output)

            # 7. Update stats
            self._stats["processed"] += 1
            if record.status == "QUARANTINED":
                self._stats["quarantined"] += 1
            else:
                self._stats["committed"] += 1

            logger.debug(f"Committed {raw_txn.txn_id} [{record.status}]")

        except Exception as e:
            self._stats["errors"] += 1
            logger.error(f"Pipeline error for {raw_msg.get('txn_id', '?')}: {e}", exc_info=True)

    def run(self):
        """Start the consumer loop."""
        self._running = True
        self._connect()

        if not _KAFKA_AVAILABLE or not self._consumer:
            logger.info("Running in stub mode — no Kafka messages to consume")
            return

        logger.info("Prover consumer started. Listening for transactions...")
        try:
            for message in self._consumer:
                if not self._running:
                    break
                self._process_message(message.value)
                self._consumer.commit()

                # Log TPS every 1000 messages
                if self._stats["processed"] % 1000 == 0:
                    elapsed = time.time() - self._stats["start_time"]
                    tps = self._stats["processed"] / max(elapsed, 1)
                    logger.info(f"TPS={tps:.1f} | committed={self._stats['committed']} | quarantined={self._stats['quarantined']}")

        except KeyboardInterrupt:
            logger.info("Consumer shutdown requested")
        finally:
            self.stop()

    def stop(self):
        self._running = False
        if self._consumer:
            self._consumer.close()
        if self._producer:
            self._producer.flush()
            self._producer.close()
        logger.info(f"Consumer stopped. Final stats: {self._stats}")

    def tps(self) -> float:
        elapsed = time.time() - self._stats["start_time"]
        return round(self._stats["processed"] / max(elapsed, 1), 1)