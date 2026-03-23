"""
bank_sim.py — ZEROAUDIT Bank Transaction Simulator
Generates realistic high-frequency banking transactions and publishes to Kafka.
Injects anomalous transactions at a configurable rate for testing.
"""

import json
import random
import time
import uuid
import math
import logging
import argparse
from datetime import datetime

logger = logging.getLogger("zeroaudit.simulator")

try:
    from kafka import KafkaProducer
    _KAFKA_AVAILABLE = True
except ImportError:
    _KAFKA_AVAILABLE = False
    logger.warning("kafka-python not installed — outputting to stdout")

# ── Transaction Templates ──────────────────────────────────────────────────────

TXN_TYPES = ["RTGS", "NEFT", "WIRE_TRANSFER", "TRADE_SETTLEMENT", "INTERNAL_TRANSFER", "FX_CONVERSION"]
TXN_TYPE_WEIGHTS = [0.35, 0.30, 0.10, 0.10, 0.10, 0.05]

# Realistic INR amount ranges per type (in paise / cents)
AMOUNT_RANGES = {
    "RTGS":               (200_000_00, 50_000_000_00),   # 2L – 50Cr
    "NEFT":               (1_00,       200_000_00),       # ₹1 – 2L
    "WIRE_TRANSFER":      (1_000_00,   10_000_000_00),    # 1K – 10Cr
    "TRADE_SETTLEMENT":   (10_000_00,  100_000_000_00),   # 10K – 100Cr
    "INTERNAL_TRANSFER":  (1_00,       500_000_00),       # ₹1 – 5L
    "FX_CONVERSION":      (50_000_00,  1_000_000_000_00), # 50K – 1000Cr
}

ACCOUNT_POOL = [f"ACC-{random.randint(1000, 9999)}" for _ in range(200)]
CURRENCIES = ["INR"] * 8 + ["USD", "EUR", "GBP", "JPY", "AED", "SGD"]


def _log_normal_amount(low: int, high: int) -> int:
    """Sample from log-normal to mimic real transaction distributions."""
    log_low = math.log(max(low, 1))
    log_high = math.log(high)
    mu = (log_low + log_high) / 2
    sigma = (log_high - log_low) / 6
    val = int(math.exp(random.gauss(mu, sigma)))
    return max(low, min(high, val))


def generate_normal_transaction() -> dict:
    txn_type = random.choices(TXN_TYPES, weights=TXN_TYPE_WEIGHTS)[0]
    low, high = AMOUNT_RANGES[txn_type]
    amount = _log_normal_amount(low, high)

    account = random.choice(ACCOUNT_POOL)
    counterparty = random.choice([a for a in ACCOUNT_POOL if a != account])

    return {
        "txn_id": f"TXN-{uuid.uuid4().hex[:16].upper()}",
        "account_id": account,
        "counterparty_id": counterparty,
        "amount_cents": amount,
        "currency": random.choice(CURRENCIES),
        "txn_type": txn_type,
        "timestamp_ns": time.time_ns(),
        "anomaly_score": round(random.uniform(0.0, 0.3), 4),
        "metadata": {
            "channel": random.choice(["mobile", "web", "branch", "api"]),
            "device_os": random.choice(["iOS", "Android", "macOS", "Windows"]),
            "city": random.choice(["Mumbai", "Delhi", "Bangalore", "Chennai", "Hyderabad"]),
        }
    }


def generate_anomalous_transaction(anomaly_type: str = None) -> dict:
    """Generate a transaction designed to trigger the anomaly detector."""
    types = ["round_number", "offhours_cayman", "high_velocity", "ofac_adjacent", "benford_violation"]
    atype = anomaly_type or random.choice(types)

    base = generate_normal_transaction()

    if atype == "round_number":
        # Structuring: suspiciously round numbers just below reporting thresholds
        thresholds = [1_000_000_00, 5_000_000_00, 10_000_000_00]
        base["amount_cents"] = random.choice(thresholds) - 1_00
        base["anomaly_score"] = round(random.uniform(0.80, 0.95), 4)

    elif atype == "offhours_cayman":
        # Login from Cayman Islands at 3AM
        base["anomaly_score"] = round(random.uniform(0.85, 0.98), 4)
        base["metadata"].update({
            "city": "Cayman Islands",
            "device_os": "Windows",
            "hour": 3,
        })

    elif atype == "high_velocity":
        # Flooding transactions from same account
        base["anomaly_score"] = round(random.uniform(0.78, 0.92), 4)
        base["account_id"] = "ACC-VELOCITY-TEST"

    elif atype == "ofac_adjacent":
        # Transaction to entity 1 hop from OFAC list
        base["anomaly_score"] = round(random.uniform(0.88, 0.99), 4)
        base["metadata"]["flag_hint"] = "OFAC_SANCTION_LIST"

    elif atype == "benford_violation":
        # Amount starting with 9 (low Benford probability for large txns)
        base["amount_cents"] = int(f"9{random.randint(10000000, 99999999)}")
        base["anomaly_score"] = round(random.uniform(0.76, 0.88), 4)

    base["txn_id"] = "TXN-ANOM-" + uuid.uuid4().hex[:12].upper()
    return base


# ── Main Simulator ─────────────────────────────────────────────────────────────

class BankSimulator:
    def __init__(
        self,
        target_tps: float = 10.0,
        anomaly_rate: float = 0.05,
        kafka_bootstrap: str = "localhost:9092",
        topic: str = "zeroaudit.transactions.raw",
    ):
        self.target_tps = target_tps
        self.anomaly_rate = anomaly_rate
        self.topic = topic
        self._producer = None
        self._count = 0
        self._start = None

        if _KAFKA_AVAILABLE:
            self._producer = KafkaProducer(
                bootstrap_servers=kafka_bootstrap,
                value_serializer=lambda v: json.dumps(v).encode(),
                acks=1,
                linger_ms=10,
                batch_size=32768,
            )

    def _emit(self, txn: dict):
        if self._producer:
            self._producer.send(self.topic, value=txn)
        else:
            print(json.dumps(txn))

    def run(self, total: int = None):
        self._start = time.time()
        interval = 1.0 / self.target_tps
        logger.info(f"Simulator started: {self.target_tps} TPS, {self.anomaly_rate*100:.0f}% anomaly rate")

        try:
            while total is None or self._count < total:
                t0 = time.time()

                if random.random() < self.anomaly_rate:
                    txn = generate_anomalous_transaction()
                else:
                    txn = generate_normal_transaction()

                self._emit(txn)
                self._count += 1

                if self._count % 100 == 0:
                    elapsed = time.time() - self._start
                    actual_tps = self._count / elapsed
                    logger.info(f"Emitted {self._count} txns | actual TPS={actual_tps:.1f}")

                # Rate limiting
                elapsed = time.time() - t0
                sleep_time = interval - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

        except KeyboardInterrupt:
            logger.info(f"Simulator stopped after {self._count} transactions")
        finally:
            if self._producer:
                self._producer.flush()
                self._producer.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ZEROAUDIT Bank Simulator")
    parser.add_argument("--tps", type=float, default=10.0, help="Target TPS")
    parser.add_argument("--anomaly-rate", type=float, default=0.05)
    parser.add_argument("--kafka", default="localhost:9092")
    parser.add_argument("--topic", default="zeroaudit.transactions.raw")
    parser.add_argument("--total", type=int, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    sim = BankSimulator(args.tps, args.anomaly_rate, args.kafka, args.topic)
    sim.run(args.total)