"""
bank_sim.py - ZEROAUDIT bank transaction simulator

Generates realistic high-frequency banking traffic, signs each transaction
with a per-run bank key, and publishes to Kafka.

Injected anomalies are structurally real
----------------------------------------
An earlier revision "injected" anomalies by writing strings into a metadata
dict the model never reads - city="Cayman Islands", flag_hint="OFAC" - while
leaving the timestamp, amount, and account hash statistically identical to
normal traffic. Those records were labelled anomalous but were not anomalous,
which caps any detector at chance on three of the five types.

Every anomaly below now perturbs a field the feature extractor actually
consumes:

  offhours_settlement  moves timestamp_ns into the 01:00-04:00 UTC window
  sanctions_adjacent   uses a demo account Neo4j has a real path from, to a
                       real OFAC-listed entity (scripts/load_sanctions_graph.py)
  velocity_burst       emits a real burst from one account inside a few seconds
  structuring          amounts just under regulatory reporting thresholds
  benford_violation    leading digit 9, which Benford makes improbable

Ground truth vs. detection
--------------------------
The simulator emits `ground_truth_anomaly` (and `ground_truth_type`) for
evaluation only. It does NOT emit an anomaly score: scoring is the intent
engine's job inside the prover. A pipeline where the data generator hands the
detector the answer measures nothing.
"""

import os
import json
import math
import time
import uuid
import random
import hashlib
import logging
import argparse
from typing import Optional

logger = logging.getLogger("zeroaudit.simulator")

try:
    from kafka import KafkaProducer
    _KAFKA = True
except ImportError:
    _KAFKA = False
    logger.warning("kafka-python not installed - emitting to stdout")

try:
    from prover.crypto.signature import SigningKey
    _SIGNING = True
except Exception:  # pragma: no cover
    _SIGNING = False


TXN_TYPES = ["RTGS", "NEFT", "WIRE_TRANSFER", "TRADE_SETTLEMENT",
             "INTERNAL_TRANSFER", "FX_CONVERSION"]
TXN_TYPE_WEIGHTS = [0.35, 0.30, 0.10, 0.10, 0.10, 0.05]

# Realistic INR ranges per instrument, in paise
AMOUNT_RANGES = {
    "RTGS":              (200_000_00, 50_000_000_00),      # 2L - 50Cr
    "NEFT":              (1_00, 200_000_00),               # Rs 1 - 2L
    "WIRE_TRANSFER":     (1_000_00, 10_000_000_00),        # 1K - 10Cr
    "TRADE_SETTLEMENT":  (10_000_00, 100_000_000_00),      # 10K - 100Cr
    "INTERNAL_TRANSFER": (1_00, 500_000_00),               # Rs 1 - 5L
    "FX_CONVERSION":     (50_000_00, 1_000_000_000_00),    # 50K - 1000Cr
}

CURRENCIES = ["INR"] * 8 + ["USD", "EUR", "GBP", "JPY", "AED", "SGD"]

ACCOUNT_POOL = ["ACC-%04d" % n for n in range(1000, 1200)]

def _account_hash(account_id: str) -> str:
    return hashlib.sha3_256(account_id.encode()).hexdigest()


# Deterministic demo account IDs. scripts/load_sanctions_graph.py seeds these
# exact same IDs into Neo4j with real graph edges to real OFAC-listed
# entities at controlled hop distances (1/2/3), replacing the earlier
# approach of searching for account IDs whose hash happened to fall in a
# fixed hex-prefix bucket. Both sides now agree on "which accounts are
# sanctions-adjacent" by referencing the same literal IDs, not by
# recomputing a heuristic independently.
SANCTIONED_ACCOUNTS = ["ACC-SANC-OFAC-%02d" % i for i in range(1, 5)]     # 1 hop
RBI_FLAGGED_ACCOUNTS = ["ACC-SANC-RBI-%02d" % i for i in range(1, 5)]     # 2 hops
FATF_FLAGGED_ACCOUNTS = ["ACC-SANC-FATF-%02d" % i for i in range(1, 5)]   # 3 hops


def _log_normal_amount(low: int, high: int) -> int:
    """Sample from a log-normal, which is how real transaction values sit."""
    log_low, log_high = math.log(max(low, 1)), math.log(high)
    mu = (log_low + log_high) / 2
    sigma = (log_high - log_low) / 6
    return max(low, min(high, int(math.exp(random.gauss(mu, sigma)))))


# Realistic diurnal profile: settlement traffic peaks mid-morning and
# mid-afternoon, thins overnight, but never stops. An earlier revision drew
# every normal transaction from a hard 09:00-18:00 block, which made "hour"
# a giveaway - any off-hours record was trivially separable and the detector
# scored 100% on that one axis while learning nothing. A genuine overnight
# tail forces the model to combine signals instead.
_HOUR_WEIGHTS = [
    0.5, 0.4, 0.3, 0.3, 0.4, 0.8,      # 00-05 overnight trough
    1.5, 3.0, 6.0, 9.0, 10.0, 10.0,    # 06-11 morning ramp and peak
    8.0, 9.0, 10.0, 10.0, 9.0, 7.0,    # 12-17 afternoon peak
    4.0, 3.0, 2.0, 1.5, 1.0, 0.7,      # 18-23 evening decay
]
_HOURS = list(range(24))


def _diurnal_timestamp() -> int:
    """Timestamp drawn from the diurnal profile above, on a recent day."""
    hour = random.choices(_HOURS, weights=_HOUR_WEIGHTS)[0]
    now = time.time()
    day_start = now - (now % 86400) - random.randint(0, 4) * 86400
    return int((day_start + hour * 3600 + random.uniform(0, 3600)) * 1e9)


# Retained under the old name for callers that imported it.
_business_hours_timestamp = _diurnal_timestamp


def generate_normal_transaction() -> dict:
    txn_type = random.choices(TXN_TYPES, weights=TXN_TYPE_WEIGHTS)[0]
    low, high = AMOUNT_RANGES[txn_type]
    account = random.choice(ACCOUNT_POOL)
    counterparty = random.choice([a for a in ACCOUNT_POOL if a != account])

    return {
        "txn_id": "TXN-%s" % uuid.uuid4().hex[:16].upper(),
        "account_id": account,
        "counterparty_id": counterparty,
        "amount_cents": _log_normal_amount(low, high),
        "currency": random.choice(CURRENCIES),
        "txn_type": txn_type,
        "timestamp_ns": _diurnal_timestamp(),
        "ground_truth_anomaly": False,
        "ground_truth_type": "NONE",
        "metadata": {
            "channel": random.choice(["mobile", "web", "branch", "api"]),
            "device_os": random.choice(["iOS", "Android", "macOS", "Windows"]),
            "city": random.choice(["Mumbai", "Delhi", "Bangalore", "Chennai", "Hyderabad"]),
        },
    }


ANOMALY_TYPES = ["structuring", "offhours_settlement", "velocity_burst",
                 "sanctions_adjacent", "benford_violation"]


def generate_anomalous_transaction(anomaly_type: str = None) -> dict:
    """Produce a transaction that is genuinely anomalous in feature space."""
    atype = anomaly_type or random.choice(ANOMALY_TYPES)
    txn = generate_normal_transaction()
    txn["ground_truth_anomaly"] = True
    txn["ground_truth_type"] = atype
    txn["txn_id"] = "TXN-ANOM-%s" % uuid.uuid4().hex[:12].upper()

    if atype == "structuring":
        # Amounts parked just below regulatory reporting thresholds.
        threshold = random.choice([1_000_000_00, 5_000_000_00, 10_000_000_00])
        txn["amount_cents"] = threshold - random.choice([1_00, 5_00, 10_00])
        txn["txn_type"] = "RTGS"

    elif atype == "offhours_settlement":
        # A real 01:00-04:00 UTC timestamp, not a metadata string.
        now = time.time()
        day_start = now - (now % 86400)
        txn["timestamp_ns"] = int((day_start + random.uniform(3600, 4 * 3600)) * 1e9)
        txn["txn_type"] = random.choice(["WIRE_TRANSFER", "FX_CONVERSION"])
        txn["amount_cents"] = _log_normal_amount(50_000_00, 10_000_000_00)
        txn["metadata"]["city"] = "George Town"

    elif atype == "velocity_burst":
        # Marked here; the emitter turns it into an actual burst.
        txn["account_id"] = "ACC-VELOCITY-%03d" % random.randint(0, 5)
        txn["metadata"]["burst"] = True

    elif atype == "sanctions_adjacent":
        # Use a demo account Neo4j has a real graph edge from, at a random
        # tier - 1 hop (direct OFAC counterparty), 2 hops (one shell), or
        # 3 hops (two shells) - so the intent engine's live Cypher query
        # against the real sanctioned-entity graph has something genuine
        # to find, rather than a hash landing in an arbitrary bucket.
        pool = random.choice([SANCTIONED_ACCOUNTS, RBI_FLAGGED_ACCOUNTS, FATF_FLAGGED_ACCOUNTS])
        if random.random() < 0.5:
            txn["counterparty_id"] = random.choice(pool)
        else:
            txn["account_id"] = random.choice(pool)
        txn["amount_cents"] = _log_normal_amount(1_000_00, 50_000_000_00)

    elif atype == "benford_violation":
        # Leading digit 9 - Benford gives it p=0.046.
        digits = random.randint(7, 11)
        txn["amount_cents"] = int("9" + "".join(
            str(random.randint(0, 9)) for _ in range(digits)))

    return txn


# -- Ingress signing -----------------------------------------------------------

def payload_hash(txn: dict) -> str:
    """Hash of the canonical transaction body, which the signature covers."""
    body = json.dumps({
        "txn_id": txn["txn_id"],
        "account_id": txn["account_id"],
        "counterparty_id": txn["counterparty_id"],
        "amount_cents": txn["amount_cents"],
        "currency": txn["currency"],
        "txn_type": txn["txn_type"],
        "timestamp_ns": txn["timestamp_ns"],
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha3_256(body.encode()).hexdigest()


class BankSimulator:
    def __init__(
        self,
        target_tps: float = 15.0,
        anomaly_rate: float = 0.05,
        kafka_bootstrap: str = "kafka:29092",
        topic: str = "zeroaudit.transactions.raw",
        sign: bool = True,
        seed: Optional[int] = None,
    ):
        self.target_tps = max(target_tps, 0.1)
        self.anomaly_rate = anomaly_rate
        self.topic = topic
        self._count = 0
        self._anomalies = 0
        self._start = None
        self._producer = None

        if seed is not None:
            random.seed(seed)

        self._key = None
        self._pub = None
        if sign and _SIGNING:
            try:
                self._key = SigningKey()
                self._pub = self._key.public_key_b64()
                logger.info("simulator signing key ready - key_id=%s", self._key.key_id())
            except Exception as exc:
                logger.warning("could not create a signing key (%s) - emitting unsigned", exc)

        if _KAFKA:
            self._producer = KafkaProducer(
                bootstrap_servers=kafka_bootstrap,
                value_serializer=lambda v: json.dumps(v).encode(),
                acks=1,
                linger_ms=10,
                batch_size=32768,
                retries=5,
            )
            logger.info("connected to Kafka @ %s topic=%s", kafka_bootstrap, topic)

    def _sign(self, txn: dict) -> dict:
        if not self._key:
            return txn
        ph = payload_hash(txn)
        txn["payload_hash"] = ph
        txn["signature_b64"] = self._key.sign(
            b"\x1f".join([
                txn["txn_id"].encode(), ph.encode(),
                str(txn["timestamp_ns"]).encode(), b"",
            ])
        )
        txn["bank_pubkey_b64"] = self._pub
        return txn

    def _emit(self, txn: dict):
        txn = self._sign(txn)
        if self._producer:
            self._producer.send(self.topic, value=txn)
        else:
            print(json.dumps(txn))
        self._count += 1
        if txn.get("ground_truth_anomaly"):
            self._anomalies += 1

    def _emit_burst(self, txn: dict):
        """A velocity anomaly is a burst, not one row claiming to be a burst."""
        account = txn["account_id"]
        self._emit(txn)
        for _ in range(random.randint(8, 15)):
            extra = generate_normal_transaction()
            extra["account_id"] = account
            extra["txn_id"] = "TXN-ANOM-%s" % uuid.uuid4().hex[:12].upper()
            extra["ground_truth_anomaly"] = True
            extra["ground_truth_type"] = "velocity_burst"
            extra["timestamp_ns"] = time.time_ns()
            self._emit(extra)

    def run(self, total: int = None):
        self._start = time.time()
        interval = 1.0 / self.target_tps
        logger.info("simulator started: %.1f TPS, %.0f%% anomaly rate, topic=%s",
                    self.target_tps, self.anomaly_rate * 100, self.topic)

        try:
            while total is None or self._count < total:
                t0 = time.time()

                if random.random() < self.anomaly_rate:
                    txn = generate_anomalous_transaction()
                    if txn["ground_truth_type"] == "velocity_burst":
                        self._emit_burst(txn)
                    else:
                        self._emit(txn)
                else:
                    self._emit(generate_normal_transaction())

                if self._count % 200 == 0:
                    elapsed = time.time() - self._start
                    logger.info("emitted %d txns | actual TPS=%.1f | anomalies=%d (%.1f%%)",
                                self._count, self._count / max(elapsed, 1e-9),
                                self._anomalies, 100 * self._anomalies / max(self._count, 1))

                sleep_for = interval - (time.time() - t0)
                if sleep_for > 0:
                    time.sleep(sleep_for)

        except KeyboardInterrupt:
            logger.info("simulator stopped after %d transactions", self._count)
        finally:
            if self._producer:
                self._producer.flush()
                self._producer.close()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        logger.warning("%s is not numeric - using %s", name, default)
        return default


def main():
    # Environment first (docker-compose sets these), CLI flags override.
    # The previous revision read argparse defaults only, so SIM_TPS and
    # SIM_ANOMALY_RATE from docker-compose were silently ignored.
    parser = argparse.ArgumentParser(description="ZEROAUDIT bank simulator")
    parser.add_argument("--tps", type=float, default=_env_float("SIM_TPS", 15.0))
    parser.add_argument("--anomaly-rate", type=float,
                        default=_env_float("SIM_ANOMALY_RATE", 0.05))
    parser.add_argument("--kafka", default=os.environ.get("KAFKA_BOOTSTRAP", "kafka:29092"))
    parser.add_argument("--topic", default=os.environ.get(
        "KAFKA_TOPIC_INGEST", "zeroaudit.transactions.raw"))
    parser.add_argument("--total", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--no-sign", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    BankSimulator(
        target_tps=args.tps,
        anomaly_rate=args.anomaly_rate,
        kafka_bootstrap=args.kafka,
        topic=args.topic,
        sign=not args.no_sign,
        seed=args.seed,
    ).run(args.total)


if __name__ == "__main__":
    main()
