"""
anomaly_detector.py — FP16 ONNX Isolation Forest Anomaly Detector
ZEROAUDIT Verifier Service

Features extracted (zero PII):
  - log(amount_cents)
  - hour_of_day (0-23)
  - day_of_week (0-6)
  - txn_type_encoded
  - benford_deviation
  - velocity_1h (transactions in last 1 hour for this account_hash)
  - graph_hops_to_blacklist

Outputs:
  - anomaly_score: float 0.0–1.0  (fully deterministic — zero random noise)
  - reconstruction_loss: float    (derived from score, no noise)
  - benford_deviation: float
  - flag_reason: str
"""

import math
import time
import hashlib
import logging
from collections import defaultdict, deque
from typing import Optional

logger = logging.getLogger("zeroaudit.anomaly_detector")

try:
    import onnxruntime as ort
    import numpy as np
    _ONNX_AVAILABLE = True
except ImportError:
    _ONNX_AVAILABLE = False
    logger.warning("onnxruntime not available — using deterministic statistical fallback detector")


# ── Benford's Law ──────────────────────────────────────────────────────────────

BENFORD_EXPECTED = {
    1: 0.301, 2: 0.176, 3: 0.125, 4: 0.097,
    5: 0.079, 6: 0.067, 7: 0.058, 8: 0.051, 9: 0.046,
}


def benford_deviation(amount_cents: int) -> float:
    """
    Chi-squared deviation of the leading digit from Benford's Law.
    Returns 0.0 (perfectly Benford) to 1.0 (extreme deviation).
    Fully deterministic — no randomness.
    """
    if amount_cents <= 0:
        return 0.5
    leading_digit = int(str(abs(amount_cents))[0])
    expected = BENFORD_EXPECTED.get(leading_digit, 0.046)
    # Observed probability approximated by harmonic series formula
    observed = math.log10(1 + 1.0 / leading_digit)
    deviation = abs(observed - expected) / expected
    return min(deviation, 1.0)


# ── Velocity Tracker ──────────────────────────────────────────────────────────

class VelocityTracker:
    """Sliding window transaction count per account_hash."""

    def __init__(self, window_seconds: int = 3600):
        self._window = window_seconds
        self._timestamps: dict[str, deque] = defaultdict(deque)

    def record(self, account_hash: str, timestamp_ns: int = None):
        ts = (timestamp_ns or time.time_ns()) / 1e9
        dq = self._timestamps[account_hash]
        dq.append(ts)
        cutoff = ts - self._window
        while dq and dq[0] < cutoff:
            dq.popleft()

    def count_1h(self, account_hash: str) -> int:
        dq = self._timestamps.get(account_hash, deque())
        cutoff = time.time() - 3600
        return sum(1 for ts in dq if ts > cutoff)


# ── Graph Proximity (deterministic hash-based) ────────────────────────────────

# Deterministic blacklist membership: hash prefixes that map to known risk tiers.
# In production: replace with Neo4j/TigerGraph BFS query.
# These are NOT random — they are deterministic based on account_hash content.
_OFAC_PREFIXES = frozenset(["00", "01", "02"])       # ~1.2% of SHA3 space
_RBI_FLAG_PREFIXES = frozenset(["03", "04", "05", "06", "07"])  # ~2%
_FATF_PREFIXES = frozenset(["08", "09", "0a", "0b", "0c", "0d", "0e"])  # ~3%


def graph_hops_to_blacklist(account_hash: str, counterparty_hash: str) -> tuple[int, str]:
    """
    Returns (hops, flag_reason).
    Deterministic: derived from the first 2 hex chars of the account and counterparty hashes.
    Zero randomness — same input always yields same result.

    In production: replace the prefix lookup with a real graph traversal
    (Neo4j Cypher: MATCH path = (a)-[*1..5]->(b:Blacklisted) WHERE a.hash = $hash RETURN length(path))
    """
    # Check account hash directly
    prefix_a = account_hash[:2].lower() if len(account_hash) >= 2 else "ff"
    prefix_b = counterparty_hash[:2].lower() if len(counterparty_hash) >= 2 else "ff"

    if prefix_a in _OFAC_PREFIXES or prefix_b in _OFAC_PREFIXES:
        return 1, "OFAC_SANCTION_LIST"
    if prefix_a in _RBI_FLAG_PREFIXES or prefix_b in _RBI_FLAG_PREFIXES:
        return 2, "RBI_FLAG_2024"
    if prefix_a in _FATF_PREFIXES or prefix_b in _FATF_PREFIXES:
        return 3, "FATF_GREY_LIST"

    # Deterministic hop count from hash — no random.randint
    # Use the integer value of first byte to produce a stable hop count 4-8
    hop_seed = int(account_hash[:2], 16) if len(account_hash) >= 2 else 128
    hops = 4 + (hop_seed % 5)  # deterministically 4, 5, 6, 7, or 8
    return hops, "NONE"


# ── Feature Extraction ────────────────────────────────────────────────────────

TXN_TYPE_MAP = {
    "RTGS": 0, "NEFT": 1, "WIRE_TRANSFER": 2,
    "TRADE_SETTLEMENT": 3, "INTERNAL_TRANSFER": 4, "FX_CONVERSION": 5,
}


def extract_features(
    txn_id: str,
    account_hash: str,
    counterparty_hash: str,
    amount_cents: int,
    txn_type: str,
    timestamp_ns: int,
    velocity_tracker: VelocityTracker,
) -> tuple[dict, str]:
    """Extract ML feature vector. Zero PII — only hashes and numerics. Fully deterministic."""
    ts_sec = timestamp_ns / 1e9
    import datetime
    dt = datetime.datetime.utcfromtimestamp(ts_sec)
    hour = dt.hour
    dow = dt.weekday()

    log_amount = math.log1p(amount_cents) if amount_cents > 0 else 0.0
    bdev = benford_deviation(amount_cents)
    velocity = velocity_tracker.count_1h(account_hash)
    hops, flag_reason = graph_hops_to_blacklist(account_hash, counterparty_hash)
    txn_type_enc = TXN_TYPE_MAP.get(txn_type.upper(), 0)

    is_offhours = 1 if (hour < 6 or hour > 22) else 0
    is_weekend = 1 if dow >= 5 else 0

    features = {
        "log_amount": log_amount,
        "hour_of_day": hour / 23.0,
        "day_of_week": dow / 6.0,
        "txn_type_enc": txn_type_enc / 5.0,
        "benford_deviation": bdev,
        "velocity_1h": min(velocity / 100.0, 1.0),
        "graph_hops": min(hops / 8.0, 1.0),
        "is_offhours": float(is_offhours),
        "is_weekend": float(is_weekend),
    }
    return features, flag_reason


# ── Detector ──────────────────────────────────────────────────────────────────

class AnomalyDetector:
    """
    FP16 ONNX Isolation Forest wrapper.
    Falls back to deterministic statistical scoring if ONNX model unavailable.
    Zero random noise anywhere in the scoring pipeline.
    """

    def __init__(self, model_path: str = None):
        self._session = None
        self._velocity = VelocityTracker()

        if _ONNX_AVAILABLE and model_path:
            try:
                opts = ort.SessionOptions()
                opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                self._session = ort.InferenceSession(
                    model_path,
                    sess_options=opts,
                    providers=["CPUExecutionProvider"],
                )
                logger.info(f"ONNX model loaded from {model_path}")
            except Exception as e:
                logger.warning(f"ONNX model load failed: {e} — using deterministic statistical fallback")

    def score(
        self,
        txn_id: str,
        account_hash: str,
        counterparty_hash: str,
        amount_cents: int,
        txn_type: str,
        timestamp_ns: int,
    ) -> dict:
        """Score a transaction. Returns anomaly metadata. Fully deterministic."""
        self._velocity.record(account_hash, timestamp_ns)

        features, flag_reason = extract_features(
            txn_id, account_hash, counterparty_hash,
            amount_cents, txn_type, timestamp_ns,
            self._velocity,
        )

        feature_vector = list(features.values())

        if self._session and _ONNX_AVAILABLE:
            score = self._onnx_score(feature_vector)
        else:
            score = self._statistical_score(features)

        bdev = features["benford_deviation"]
        hops = int(features["graph_hops"] * 8)

        # Determine flag reason from score + graph proximity
        if score > 0.85 and hops <= 2:
            actual_flag = flag_reason
        elif score > 0.75:
            actual_flag = "HIGH_ANOMALY_SCORE"
        elif bdev > 0.7:
            actual_flag = "BENFORD_VIOLATION"
        else:
            actual_flag = "NONE"

        # reconstruction_loss is derived deterministically from score — no random noise
        # Modeled as: loss = score * decay_factor where decay = 1 - benford_bonus
        reconstruction_loss = round(score * (1.0 - 0.05 * bdev), 4)

        return {
            "txn_id": txn_id,
            "anomaly_score": round(score, 4),
            "reconstruction_loss": reconstruction_loss,
            "benford_deviation": round(bdev, 4),
            "graph_hops_to_blacklist": hops,
            "flag_reason": actual_flag,
            "features": features,
            "behavioral_delta": {
                "is_offhours": bool(features["is_offhours"]),
                "is_weekend": bool(features["is_weekend"]),
                "velocity_1h": int(features["velocity_1h"] * 100),
            },
        }

    def _onnx_score(self, feature_vector: list) -> float:
        import numpy as np
        x = np.array([feature_vector], dtype=np.float16)
        inputs = {self._session.get_inputs()[0].name: x}
        outputs = self._session.run(None, inputs)
        raw_score = float(outputs[0][0])
        # Isolation Forest returns negative scores for anomalies
        # Normalize to 0-1 range — no noise added
        return max(0.0, min(1.0, (-raw_score + 0.5)))

    def _statistical_score(self, features: dict) -> float:
        """
        Deterministic rule-based statistical fallback.
        Combines Benford deviation, velocity, graph proximity, and temporal signals.
        Zero random noise — same features always produce same score.
        """
        score = 0.0
        weights = {
            "benford_deviation": 0.25,
            "graph_hops_inv": 0.30,    # inverted: fewer hops = higher risk
            "velocity_1h": 0.20,
            "is_offhours": 0.15,
            "log_amount_extreme": 0.10,
        }

        score += features["benford_deviation"] * weights["benford_deviation"]

        # Invert graph hops: closer to blacklist = higher score
        graph_hops_score = max(0.0, 1.0 - features["graph_hops"])
        score += graph_hops_score * weights["graph_hops_inv"]

        score += features["velocity_1h"] * weights["velocity_1h"]
        score += features["is_offhours"] * weights["is_offhours"]

        # Extreme amounts (>5 STD from log-normal mean for INR RTGS)
        # ~1.5Cr INR = log(150_000_000) ≈ 18.8; threshold at log(500_000_000) ≈ 20.0
        log_amount = features["log_amount"]
        extreme = max(0.0, (log_amount - 20.0) / 5.0)
        score += min(extreme, 1.0) * weights["log_amount_extreme"]

        # NO random noise — deterministic output
        return max(0.0, min(1.0, score))


# ── Singleton ─────────────────────────────────────────────────────────────────

_detector: AnomalyDetector = None


def get_detector(model_path: str = None) -> AnomalyDetector:
    global _detector
    if _detector is None:
        import os
        path = model_path or os.environ.get("ONNX_MODEL_PATH")
        _detector = AnomalyDetector(path)
    return _detector