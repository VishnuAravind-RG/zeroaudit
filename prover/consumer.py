"""
prover/consumer.py - ZEROAUDIT prover ingest loop

Consumes raw transactions, verifies their ingress signature, scores them with
the intent engine, commits them under Module-LWE, and republishes a zero-PII
envelope to the public topic.

Ordering matters:

    1. verify ingress signature   reject anything the bank did not sign
    2. score with the intent engine  runs HERE, inside the prover, because
                                     this is the last point at which the raw
                                     amount legitimately exists
    3. LWE commit + chain + sign
    4. publish the zero-PII envelope

Step 2 has to precede step 4 and cannot move to the verifier: the DMZ never
sees an amount, so it could not compute these features. An earlier revision
took `anomaly_score` straight off the inbound message, which meant the data
producer was grading its own homework and the model never ran at all.
"""

import json
import time
import logging
import threading
from collections import deque
from typing import Optional

from .config.settings import settings
from .crypto.commitment import get_store
from .crypto.signature import verify_transaction_signature

logger = logging.getLogger("zeroaudit.prover.consumer")

try:
    from kafka import KafkaConsumer, KafkaProducer
    _KAFKA = True
except ImportError:
    _KAFKA = False
    logger.error("kafka-python not installed - prover cannot consume")

try:
    from verifier.anomaly_detector import get_detector
    _DETECTOR = True
except Exception as exc:  # pragma: no cover
    _DETECTOR = False
    logger.error("intent engine unavailable (%s) - transactions will not be scored", exc)


class ProverConsumer:
    """Kafka ingest loop for the prover enclave."""

    def __init__(self):
        self._running = False
        self._consumer = None
        self._producer = None
        self._store = get_store()
        self._detector = get_detector() if _DETECTOR else None
        self._lock = threading.Lock()
        self._stats = {
            "processed": 0,
            "errors": 0,
            "signature_failures": 0,
            "quarantined": 0,
            "publish_errors": 0,
            "start_time": time.time(),
        }
        self._timestamps: deque = deque(maxlen=5000)
        self._chain_topics = frozenset({
            settings.KAFKA_TOPIC_COMMITTED, settings.KAFKA_TOPIC_ANOMALIES,
        })

        if self._detector:
            logger.info("intent engine backend: %s", self._detector.backend)

    # -- connection -----------------------------------------------------------

    def _connect(self):
        if not _KAFKA:
            raise RuntimeError("kafka-python not installed")

        backoff = 2.0
        for attempt in range(1, 13):
            try:
                self._consumer = KafkaConsumer(
                    settings.KAFKA_TOPIC_INGEST,
                    bootstrap_servers=settings.KAFKA_BOOTSTRAP,
                    group_id=settings.KAFKA_CONSUMER_GROUP,
                    value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                    auto_offset_reset="earliest",
                    enable_auto_commit=True,
                    max_poll_records=settings.KAFKA_MAX_POLL_RECORDS,
                    consumer_timeout_ms=1000,
                )
                self._producer = KafkaProducer(
                    bootstrap_servers=settings.KAFKA_BOOTSTRAP,
                    value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                    acks=1,
                    linger_ms=10,
                    retries=5,
                )
                logger.info("prover connected to Kafka @ %s (group=%s)",
                            settings.KAFKA_BOOTSTRAP, settings.KAFKA_CONSUMER_GROUP)
                return
            except Exception as exc:
                logger.warning("Kafka connect attempt %d/12 failed: %s: %s",
                               attempt, type(exc).__name__, exc)
                time.sleep(backoff)
                backoff = min(backoff * 1.5, 30.0)

        raise RuntimeError("prover could not reach Kafka after 12 attempts")

    # -- processing -----------------------------------------------------------

    def _process(self, record: dict):
        txn_id = record.get("txn_id") or record.get("transaction_id", "unknown")
        try:
            # 1. Ingress firewall
            if not verify_transaction_signature(record):
                with self._lock:
                    self._stats["signature_failures"] += 1
                logger.error("signature verification FAILED for %s - dropped", txn_id)
                return

            # 2. Extract. The amount exists only inside this boundary.
            amount_cents = int(record.get("amount_cents",
                                          float(record.get("amount", 0)) * 100))
            account_id = record.get("account_id", "unknown")
            counterparty_id = record.get("counterparty_id", account_id)
            txn_type = record.get("txn_type", record.get("type", "UNKNOWN"))
            timestamp_ns = int(record.get("timestamp_ns", time.time_ns()))

            # 3. Score with the intent engine, in-enclave.
            anomaly_score, flag_reason = 0.0, "NONE"
            novelty_score, typology_score = 0.0, 0.0
            if self._detector:
                import hashlib
                verdict = self._detector.score(
                    txn_id=txn_id,
                    account_hash=hashlib.sha3_256(account_id.encode()).hexdigest(),
                    counterparty_hash=hashlib.sha3_256(counterparty_id.encode()).hexdigest(),
                    amount_cents=amount_cents,
                    txn_type=txn_type,
                    timestamp_ns=timestamp_ns,
                )
                anomaly_score = verdict["anomaly_score"]
                flag_reason = verdict["flag_reason"]
                # Carried through to the export so the DMZ can show the
                # autoencoder/typology split without re-running the detector
                # on an amount it will never have.
                novelty_score = verdict.get("novelty_score", 0.0)
                typology_score = verdict.get("typology_score", 0.0)

            # 4. Commit, chain, sign.
            committed = self._store.add(
                txn_id=txn_id,
                amount_cents=amount_cents,
                account_id=account_id,
                txn_type=txn_type,
                anomaly_score=anomaly_score,
                flag_reason=flag_reason,
                threshold=settings.ANOMALY_THRESHOLD,
                novelty_score=novelty_score,
                typology_score=typology_score,
            )

            # 5. Publish the zero-PII envelope.
            envelope = committed.to_export_dict()
            self._publish(settings.KAFKA_TOPIC_COMMITTED, envelope)
            if anomaly_score >= settings.ANOMALY_THRESHOLD:
                self._publish(settings.KAFKA_TOPIC_ANOMALIES, envelope)
                with self._lock:
                    self._stats["quarantined"] += 1

            with self._lock:
                self._stats["processed"] += 1
                self._timestamps.append(time.time())

        except Exception as exc:
            with self._lock:
                self._stats["errors"] += 1
            logger.error("processing error on %s: %s: %s",
                         txn_id, type(exc).__name__, exc, exc_info=True)

    def _publish(self, topic: str, payload: dict):
        try:
            # committed/anomalies carry a hash chain: record N's prev_chain_hash
            # must equal record N-1's chain_hash. Kafka only guarantees order
            # WITHIN a partition, and these topics have 6. Publishing with no
            # key spreads records across all of them, so a single-consumer
            # verifier polling all 6 partitions receives them interleaved
            # rather than in production order - its online chain check then
            # flags false breaks on nearly every record, even though the
            # records are perfectly valid (confirmed: the offline
            # /chain/verify, which sorts by seq before checking, sees no
            # breaks in the same data). A constant key pins every record on a
            # chain-linked topic to one partition, which costs nothing here
            # because CommitmentStore.add() already serializes the chain
            # under one lock - there was never real cross-partition
            # parallelism to preserve.
            key = b"zeroaudit-chain" if topic in self._chain_topics else None
            self._producer.send(topic, key=key, value=payload)
        except Exception as exc:
            with self._lock:
                self._stats["publish_errors"] += 1
            logger.error("publish to %s failed: %s", topic, exc)

    # -- lifecycle ------------------------------------------------------------

    def run(self):
        self._running = True
        try:
            self._connect()
        except RuntimeError as exc:
            logger.error("prover failed to start: %s", exc)
            return

        logger.info("prover ingest loop started")
        while self._running:
            try:
                for _tp, messages in self._consumer.poll(timeout_ms=500).items():
                    for msg in messages:
                        self._process(msg.value)
            except Exception as exc:
                with self._lock:
                    self._stats["errors"] += 1
                logger.error("poll error: %s: %s", type(exc).__name__, exc)
                time.sleep(1)

        logger.info("prover ingest loop exiting")

    def stop(self):
        self._running = False
        for handle in (self._consumer, self._producer):
            try:
                if handle:
                    handle.close()
            except Exception:
                pass
        logger.info("prover stopped. stats=%s", self.stats())

    # -- metrics --------------------------------------------------------------

    def tps(self, window: int = 10) -> float:
        """Throughput over a trailing window, in transactions per second."""
        cutoff = time.time() - window
        with self._lock:
            recent = sum(1 for t in self._timestamps if t > cutoff)
        return round(recent / window, 2)

    def stats(self) -> dict:
        with self._lock:
            snapshot = dict(self._stats)
        snapshot["tps"] = self.tps()
        snapshot["uptime_s"] = round(time.time() - snapshot.pop("start_time"), 1)
        if self._detector:
            snapshot["intent_engine"] = self._detector.stats()
        return snapshot
