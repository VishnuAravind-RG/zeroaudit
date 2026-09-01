"""
anomaly_detector.py - FP16 ONNX autoencoder intent engine
ZEROAUDIT

An undercomplete autoencoder (10 -> 6 -> 3 -> 6 -> 10) trained only on
normal traffic. It learns to reconstruct ordinary transactions accurately;
anything structurally unlike its training distribution reconstructs badly,
and that reconstruction error is the anomaly score. Training on normals
alone is deliberate - fraud labels are scarce and the fraud that matters
is the kind nobody has labelled yet.

Where this runs
---------------
Inside the prover, on the trusted side of the enclave boundary. The engine
sees the amount; the DMZ never does. Only the scalar score and a flag reason
cross into the verifier, which is why the published ledger stays zero-PII
while still carrying a usable risk signal.

Feature vector (10 dims, fixed order - the ONNX graph depends on it)
--------------------------------------------------------------------
    0 log_amount          log1p(amount_cents), scaled
    1 hour_of_day         0-23 -> [0,1]
    2 day_of_week         0-6  -> [0,1]
    3 txn_type_enc        categorical -> [0,1]
    4 benford_surprisal   leading-digit improbability under Benford
    5 velocity_1h         sliding-window txn count for this account
    6 graph_hops          proximity to a sanctioned entity
    7 is_offhours         binary
    8 is_weekend          binary
    9 threshold_proximity nearness to a regulatory reporting threshold

Scoring
-------
Reconstruction MSE is mapped to [0,1] against percentiles measured on a
held-out normal set at training time and baked into the model sidecar, so
the 0.75 quarantine threshold means the same thing across runs.

Determinism: no randomness anywhere in the scoring path. Identical input
always produces an identical score - an audit system whose verdicts drift
between runs is not auditable.
"""

import os
import json
import math
import time
import logging
import threading
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("zeroaudit.intent_engine")

try:
    import numpy as np
    _NUMPY = True
except ImportError:  # pragma: no cover
    _NUMPY = False

try:
    import onnxruntime as ort
    _ONNX_RUNTIME = True
except ImportError:  # pragma: no cover
    _ONNX_RUNTIME = False
    logger.warning("onnxruntime unavailable - intent engine uses the statistical fallback")


FEATURE_NAMES = [
    "log_amount", "hour_of_day", "day_of_week", "txn_type_enc",
    "benford_surprisal", "velocity_1h", "graph_hops", "is_offhours", "is_weekend",
    "threshold_proximity",
]
N_FEATURES = len(FEATURE_NAMES)

# log1p(1e14 paise) is about 32.2; 35 keeps the largest realistic amount inside [0,1]
LOG_AMOUNT_SCALE = 35.0

DEFAULT_MODEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "models", "intent_autoencoder_fp16.onnx",
)


# -- Benford's Law -------------------------------------------------------------
#
# Benford's law states P(leading digit = d) = log10(1 + 1/d). Two distinct
# tests are useful and they are not interchangeable:
#
#   per-transaction  how surprising is THIS leading digit? Quantified as
#                    normalised surprisal, -log2(p_d) / -log2(p_9).
#
#   population       does a STREAM of amounts follow the distribution?
#                    Quantified as a chi-squared statistic over a sliding
#                    window. A single value has no chi-squared - comparing
#                    log10(1+1/d) against a table of the same quantity, as
#                    an earlier revision did, is identically zero by
#                    construction and can never fire.

BENFORD_P = {d: math.log10(1 + 1.0 / d) for d in range(1, 10)}
_MAX_SURPRISAL = -math.log2(BENFORD_P[9])


def leading_digit(amount_cents: int) -> int:
    if amount_cents <= 0:
        return 0
    return int(str(abs(int(amount_cents)))[0])


def benford_surprisal(amount_cents: int) -> float:
    """Per-transaction improbability of the leading digit, normalised to [0,1].

    A leading 1 (p=0.301) scores 0.30; a leading 9 (p=0.046) scores 1.0.
    """
    d = leading_digit(amount_cents)
    if d == 0:
        return 0.5
    return round(min(-math.log2(BENFORD_P[d]) / _MAX_SURPRISAL, 1.0), 6)


# Backwards-compatible alias. The old name promised a deviation it never
# computed; callers wanting the population test should use BenfordMonitor.
benford_deviation = benford_surprisal


class BenfordMonitor:
    """Sliding-window chi-squared test of leading digits against Benford."""

    def __init__(self, window: int = 2000):
        self._window = window
        self._digits: deque = deque(maxlen=window)
        self._lock = threading.Lock()

    def record(self, amount_cents: int):
        d = leading_digit(amount_cents)
        if d:
            with self._lock:
                self._digits.append(d)

    def counts(self) -> dict:
        with self._lock:
            snapshot = list(self._digits)
        counts = {d: 0 for d in range(1, 10)}
        for d in snapshot:
            counts[d] += 1
        return counts

    def chi_squared(self) -> dict:
        """Pearson chi-squared with 8 degrees of freedom.

        Critical value at p=0.05 is 15.51; above that the stream's leading
        digits are inconsistent with Benford, which is the classic signature
        of fabricated or structured amounts.
        """
        counts = self.counts()
        n = sum(counts.values())
        if n < 100:
            return {"chi2": 0.0, "n": n, "suspicious": False,
                    "detail": "insufficient sample (need 100+)"}

        chi2 = 0.0
        for d in range(1, 10):
            expected = BENFORD_P[d] * n
            chi2 += (counts[d] - expected) ** 2 / expected

        return {
            "chi2": round(chi2, 3),
            "n": n,
            "critical_value_p05": 15.507,
            "suspicious": chi2 > 15.507,
            "counts": counts,
        }


# -- Regulatory reporting thresholds -------------------------------------------
#
# Structuring ("smurfing") splits a transfer so each leg lands just under a
# reporting threshold. In feature space the tell is not the amount itself -
# it is the amount's NEARNESS to a threshold from below. FIU-IND requires
# Cash Transaction Reports at INR 10 lakh; 50 lakh and 1 crore are common
# internal escalation lines. Values in paise.

REPORTING_THRESHOLDS = [1_000_000_00, 5_000_000_00, 10_000_000_00, 100_000_000_00]
# The band must be TIGHT. At 5% roughly 0.9% of ordinary log-normal amounts
# land inside it by chance, so an autoencoder trained on normal traffic learns
# the pattern as ordinary and stops flagging it. Deliberate structuring sits
# within a fraction of a percent of the line, so 0.5% keeps the recall while
# cutting incidental normal traffic in the band by an order of magnitude.
_STRUCTURING_BAND = 0.005


def threshold_proximity(amount_cents: int) -> float:
    """1.0 when an amount sits just under a reporting threshold, 0.0 when far.

    Deliberately one-sided: sitting just ABOVE a threshold is unremarkable
    (the report simply gets filed). Sitting just below it, repeatedly, is the
    signature of deliberate structuring.
    """
    if amount_cents <= 0:
        return 0.0
    best = 0.0
    for t in REPORTING_THRESHOLDS:
        band = t * _STRUCTURING_BAND
        if t - band <= amount_cents < t:
            best = max(best, 1.0 - (t - amount_cents) / band)
    return round(best, 6)


# -- Velocity ------------------------------------------------------------------

class VelocityTracker:
    """Sliding-window transaction count per account hash."""

    def __init__(self, window_seconds: int = 3600, max_accounts: int = 50_000):
        self._window = window_seconds
        self._max_accounts = max_accounts
        self._ts: dict = defaultdict(deque)
        self._lock = threading.Lock()

    def record(self, account_hash: str, timestamp_ns: int = None):
        ts = (timestamp_ns or time.time_ns()) / 1e9
        with self._lock:
            dq = self._ts[account_hash]
            dq.append(ts)
            cutoff = ts - self._window
            while dq and dq[0] < cutoff:
                dq.popleft()
            # Bound memory: drop the coldest accounts once the map gets large.
            if len(self._ts) > self._max_accounts:
                for key in [k for k, v in list(self._ts.items())[:1000] if not v]:
                    self._ts.pop(key, None)

    def count_1h(self, account_hash: str, as_of_ns: int = None) -> int:
        """Transactions in the trailing window, measured from EVENT time.

        Measuring from wall-clock instead silently returns 0 for any record
        whose event time is older than the window - which is every record
        during a Kafka replay, a backfill, or a catch-up after downtime.
        """
        now = (as_of_ns / 1e9) if as_of_ns else time.time()
        cutoff = now - self._window
        with self._lock:
            dq = self._ts.get(account_hash)
            return sum(1 for ts in dq if ts > cutoff) if dq else 0


# -- Sanctions graph proximity -------------------------------------------------
#
# Deterministic prefix buckets stand in for a real graph traversal. In
# production this becomes a Neo4j/TigerGraph query:
#   MATCH p = (a {hash:$h})-[*1..5]->(b:Sanctioned) RETURN min(length(p))

# Prefix width sets what fraction of the account universe is treated as
# sanctioned, and it has to be realistic. Two-hex-char buckets marked ~6% of
# all accounts as sanctions-adjacent, so the sanctions rule alone produced an
# 8.7% false-positive rate on clean traffic - an alert queue nobody can staff.
# The real OFAC SDN list is a vanishing fraction of global accounts, so these
# use three hex characters: 1/4096 per bucket.
_PREFIX_LEN = 3
_OFAC_PREFIXES = frozenset(["000"])                                  # ~0.024%
_RBI_FLAG_PREFIXES = frozenset(["001", "002"])                       # ~0.049%
_FATF_PREFIXES = frozenset(["003", "004", "005", "006"])             # ~0.098%


def graph_hops_to_blacklist(account_hash: str, counterparty_hash: str):
    """Return (hops, flag_reason). Deterministic in the input hashes."""
    pa = account_hash[:_PREFIX_LEN].lower() if len(account_hash) >= _PREFIX_LEN else "fff"
    pb = counterparty_hash[:_PREFIX_LEN].lower() if len(counterparty_hash) >= _PREFIX_LEN else "fff"

    if pa in _OFAC_PREFIXES or pb in _OFAC_PREFIXES:
        return 1, "OFAC_SANCTION_LIST"
    if pa in _RBI_FLAG_PREFIXES or pb in _RBI_FLAG_PREFIXES:
        return 2, "RBI_FLAG_2024"
    if pa in _FATF_PREFIXES or pb in _FATF_PREFIXES:
        return 3, "FATF_GREY_LIST"

    try:
        seed = int(account_hash[:2], 16)
    except (ValueError, IndexError):
        seed = 128
    return 4 + (seed % 5), "NONE"


# -- Feature extraction --------------------------------------------------------

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
    velocity_tracker: "VelocityTracker",
):
    """Build the 9-dim feature vector. Deterministic; returns (dict, flag_reason)."""
    dt = datetime.fromtimestamp(timestamp_ns / 1e9, tz=timezone.utc)
    hour, dow = dt.hour, dt.weekday()

    hops, flag_reason = graph_hops_to_blacklist(account_hash, counterparty_hash)
    velocity = velocity_tracker.count_1h(account_hash, timestamp_ns) if velocity_tracker else 0

    features = {
        "log_amount": min(math.log1p(max(amount_cents, 0)) / LOG_AMOUNT_SCALE, 1.0),
        "hour_of_day": hour / 23.0,
        "day_of_week": dow / 6.0,
        "txn_type_enc": TXN_TYPE_MAP.get(str(txn_type).upper(), 0) / 5.0,
        "benford_surprisal": benford_surprisal(amount_cents),
        "velocity_1h": min(velocity / 100.0, 1.0),
        "graph_hops": min(hops / 8.0, 1.0),
        "is_offhours": 1.0 if (hour < 6 or hour > 22) else 0.0,
        "is_weekend": 1.0 if dow >= 5 else 0.0,
        "threshold_proximity": threshold_proximity(amount_cents),
    }
    return features, flag_reason


def to_vector(features: dict) -> list:
    """Dict -> ordered list. The ONNX graph depends on FEATURE_NAMES order."""
    return [float(features[name]) for name in FEATURE_NAMES]


# -- Detector ------------------------------------------------------------------

class AnomalyDetector:
    """FP16 ONNX autoencoder with a deterministic statistical fallback."""

    def __init__(self, model_path: str = None, threshold: float = 0.75):
        self._session = None
        self._input_name = None
        self._calibration = None
        self._velocity = VelocityTracker()
        self._benford = BenfordMonitor()
        self._threshold = threshold
        self._scored = 0
        self._lock = threading.Lock()

        path = model_path or os.environ.get("ONNX_MODEL_PATH") or DEFAULT_MODEL_PATH
        self._load(path)

    @property
    def backend(self) -> str:
        return "onnx-fp16" if self._session else "statistical-fallback"

    def _load(self, path: str):
        if not (_ONNX_RUNTIME and _NUMPY):
            return
        if not path or not os.path.exists(path):
            logger.warning("ONNX model not found at %s - using statistical fallback "
                           "(build it with: python -m ml.train_autoencoder)", path)
            return
        try:
            opts = ort.SessionOptions()
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            opts.intra_op_num_threads = 1        # tiny graph; threading costs more than it saves
            self._session = ort.InferenceSession(
                path, sess_options=opts, providers=["CPUExecutionProvider"]
            )
            self._input_name = self._session.get_inputs()[0].name

            sidecar = os.path.splitext(path)[0] + ".json"
            if os.path.exists(sidecar):
                with open(sidecar, "r", encoding="utf-8") as fh:
                    self._calibration = json.load(fh)
                logger.info("intent engine loaded: %s (fp16, calibrated p50=%.6f p99=%.6f)",
                            os.path.basename(path),
                            self._calibration.get("p50", 0.0),
                            self._calibration.get("p99", 0.0))
            else:
                logger.warning("calibration sidecar missing at %s - scores will be uncalibrated",
                               sidecar)
        except Exception as exc:
            logger.error("ONNX model load failed (%s: %s) - using statistical fallback",
                         type(exc).__name__, exc)
            self._session = None

    # -- scoring -------------------------------------------------------------

    def score(
        self,
        txn_id: str,
        account_hash: str,
        counterparty_hash: str,
        amount_cents: int,
        txn_type: str,
        timestamp_ns: int,
    ) -> dict:
        """Score one transaction. Returns anomaly metadata, no raw amount."""
        self._velocity.record(account_hash, timestamp_ns)
        self._benford.record(amount_cents)

        features, graph_flag = extract_features(
            txn_id, account_hash, counterparty_hash,
            amount_cents, txn_type, timestamp_ns, self._velocity,
        )

        if self._session is not None:
            score, recon_error = self._onnx_score(features)
        else:
            score = self._statistical_score(features)
            recon_error = round(score * 0.1, 6)

        hops = int(round(features["graph_hops"] * 8))

        # Final risk = the stronger of learned novelty and a matched typology.
        # Taking the max rather than a weighted sum means a confident rule is
        # never diluted by a calm autoencoder, and vice versa.
        typology_score, typology_reason = self._typology(features, hops, graph_flag)
        novelty_score = score
        score = max(novelty_score, typology_score)

        # The SCORE takes the maximum, but the REASON prefers a matched
        # typology whenever one fired, even if the autoencoder scored higher.
        # An analyst can act on "STRUCTURING_PATTERN"; "CRITICAL_NOVELTY" only
        # tells them the model was surprised, which is not a basis for filing
        # a suspicious transaction report.
        flag = typology_reason if typology_score > 0.0 else self._novelty_reason(novelty_score)

        with self._lock:
            self._scored += 1

        return {
            "txn_id": txn_id,
            "anomaly_score": round(float(score), 4),
            "novelty_score": round(float(novelty_score), 4),
            "typology_score": round(float(typology_score), 4),
            "reconstruction_loss": round(float(recon_error), 6),
            "benford_surprisal": features["benford_surprisal"],
            "threshold_proximity": features["threshold_proximity"],
            "graph_hops_to_blacklist": hops,
            "flag_reason": flag,
            "quarantine": bool(score >= self._threshold),
            "backend": self.backend,
            "features": features,
            "behavioral_delta": {
                "is_offhours": bool(features["is_offhours"]),
                "is_weekend": bool(features["is_weekend"]),
                "velocity_1h": int(round(features["velocity_1h"] * 100)),
            },
        }

    def _typology(self, features: dict, hops: int, graph_flag: str):
        """Deterministic rules for KNOWN laundering typologies.

        The autoencoder is a novelty detector: it is good at "this does not
        look like anything I was trained on" and structurally weak at patterns
        that overlap ordinary traffic on most axes. Known typologies are better
        served by explicit rules, and AML regulation independently requires
        that a flag be explainable - "reconstruction error 1.4" is not an answer
        a compliance officer can act on, whereas "counterparty is 1 hop from an
        OFAC-listed entity" is.

        Returns (score, reason). Score 0.0 means no typology matched.
        """
        rules = []

        if hops <= 1:
            rules.append((0.95, graph_flag or "OFAC_SANCTION_LIST"))
        elif hops == 2:
            rules.append((0.85, graph_flag or "RBI_FLAG_2024"))
        elif hops == 3:
            rules.append((0.70, graph_flag or "FATF_GREY_LIST"))

        if features["threshold_proximity"] > 0.90:
            rules.append((0.90, "STRUCTURING_PATTERN"))

        velocity_per_hour = features["velocity_1h"] * 100
        # Normal per-account velocity sits at p50=4/hr, p99=9/hr, so 12 is
        # comfortably outside ordinary behaviour without eating the FPR budget.
        if velocity_per_hour >= 20:
            rules.append((0.92, "VELOCITY_SPIKE"))
        elif velocity_per_hour >= 12:
            rules.append((0.80, "VELOCITY_SPIKE"))

        # Weak individually; contributory when they co-occur with a large value.
        if features["benford_surprisal"] >= 0.99 and features["log_amount"] > 0.55:
            rules.append((0.62, "BENFORD_VIOLATION"))
        if features["is_offhours"] and features["log_amount"] > 0.60:
            rules.append((0.60, "OFFHOURS_HIGH_VALUE"))

        if not rules:
            return 0.0, "NONE"
        return max(rules, key=lambda r: r[0])

    def _novelty_reason(self, score: float) -> str:
        if score >= 0.90:
            return "CRITICAL_NOVELTY"
        if score >= self._threshold:
            return "HIGH_NOVELTY_SCORE"
        return "NONE"

    def _onnx_score(self, features: dict):
        """Run the FP16 autoencoder and map reconstruction MSE onto [0,1]."""
        x = np.array([to_vector(features)], dtype=np.float16)
        recon_error = float(self._session.run(None, {self._input_name: x})[0].reshape(-1)[0])

        if not self._calibration:
            return max(0.0, min(1.0, recon_error * 50.0)), recon_error

        p50 = self._calibration.get("p50", 0.0)
        p99 = self._calibration.get("p99", p50 + 1e-6)
        span = max(p99 - p50, 1e-9)
        # p50 of normal traffic -> 0.0, p99 -> 0.75 (the quarantine line),
        # tapering asymptotically to 1.0 beyond that.
        norm = (recon_error - p50) / span
        score = 0.75 * norm if norm <= 1.0 else 0.75 + 0.25 * (1 - math.exp(-(norm - 1.0)))
        return max(0.0, min(1.0, score)), recon_error

    def _statistical_score(self, features: dict) -> float:
        """Deterministic rule-based fallback when no ONNX model is present."""
        weights = {
            "graph_hops_inv": 0.30,
            "benford_surprisal": 0.20,
            "velocity_1h": 0.20,
            "is_offhours": 0.10,
            "amount_extreme": 0.10,
            "threshold_proximity": 0.10,
        }
        score = 0.0
        score += max(0.0, 1.0 - features["graph_hops"]) * weights["graph_hops_inv"]
        score += features["benford_surprisal"] * weights["benford_surprisal"]
        score += features["velocity_1h"] * weights["velocity_1h"]
        score += features["is_offhours"] * weights["is_offhours"]
        score += max(0.0, (features["log_amount"] - 0.55) / 0.45) * weights["amount_extreme"]
        score += features["threshold_proximity"] * weights["threshold_proximity"]
        return max(0.0, min(1.0, score))

    # -- introspection --------------------------------------------------------

    def benford_report(self) -> dict:
        return self._benford.chi_squared()

    def stats(self) -> dict:
        with self._lock:
            scored = self._scored
        return {
            "backend": self.backend,
            "scored": scored,
            "threshold": self._threshold,
            "calibrated": bool(self._calibration),
            "features": N_FEATURES,
        }


_detector: Optional[AnomalyDetector] = None
_detector_lock = threading.Lock()


def get_detector(model_path: str = None) -> AnomalyDetector:
    global _detector
    with _detector_lock:
        if _detector is None:
            threshold = float(os.environ.get("ANOMALY_THRESHOLD", "0.75"))
            _detector = AnomalyDetector(model_path, threshold=threshold)
            logger.info("intent engine ready - backend=%s", _detector.backend)
    return _detector
