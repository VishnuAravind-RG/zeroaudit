"""
evaluate.py - Measure the ZEROAUDIT intent engine end to end

    python -m ml.evaluate

Runs the SHIPPED artefact - the fp16 ONNX graph loaded through the same
AnomalyDetector the prover uses - over a held-out simulated stream, and
reports what it actually catches.

Reported metrics
----------------
  ROC AUC              rank-ordering quality over the whole score range.
  Detection @ 1% FPR   the operational number. An audit queue has finite
                       analyst capacity, so the honest question is "how much
                       fraud do we catch if we accept 1 false positive in
                       100 clean transactions", not "how good is the AUC".
  Per-typology recall  broken out by anomaly type, because an aggregate hides
                       a detector that aces one pattern and misses four.
  Novelty vs typology  attribution between the autoencoder and the rules
                       layer, so neither gets credit for the other's work.

The stream is generated chronologically and replayed through one shared
VelocityTracker, matching how the prover consumes Kafka - shuffling it
would make every velocity feature meaningless.
"""

import os
import sys
import json
import random
import hashlib
import logging
import argparse
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verifier.anomaly_detector import AnomalyDetector, DEFAULT_MODEL_PATH  # noqa: E402
from simulator.bank_sim import (  # noqa: E402
    generate_normal_transaction, generate_anomalous_transaction, ANOMALY_TYPES,
)

logger = logging.getLogger("zeroaudit.ml.evaluate")


def build_stream(n_samples: int, anomaly_rate: float, seed: int) -> list:
    """Chronologically ordered evaluation stream with materialised bursts."""
    import uuid

    random.seed(seed)
    events = []
    n_anom = int(n_samples * anomaly_rate)

    for _ in range(n_samples - n_anom):
        events.append(generate_normal_transaction())

    emitted = 0
    while emitted < n_anom:
        atype = random.choice(ANOMALY_TYPES)
        txn = generate_anomalous_transaction(atype)
        if atype == "velocity_burst":
            base_ts, account = txn["timestamp_ns"], txn["account_id"]
            for i in range(random.randint(8, 15)):
                leg = generate_normal_transaction()
                leg.update({
                    "txn_id": "TXN-ANOM-%s" % uuid.uuid4().hex[:12].upper(),
                    "account_id": account,
                    "timestamp_ns": base_ts + i * random.randint(20, 90) * 1_000_000_000,
                    "ground_truth_anomaly": True,
                    "ground_truth_type": "velocity_burst",
                })
                events.append(leg)
                emitted += 1
        else:
            events.append(txn)
            emitted += 1

    events.sort(key=lambda e: e["timestamp_ns"])
    return events


def roc_auc(neg: np.ndarray, pos: np.ndarray) -> float:
    """AUC via Mann-Whitney U, ties at half weight."""
    if len(neg) == 0 or len(pos) == 0:
        return float("nan")
    y = np.concatenate([neg, pos])
    order = y.argsort()
    ranks = np.empty(len(y), dtype=np.float64)
    ranks[order] = np.arange(1, len(y) + 1)

    sorted_y = y[order]
    i = 0
    while i < len(sorted_y):
        j = i
        while j + 1 < len(sorted_y) and sorted_y[j + 1] == sorted_y[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + j + 2) / 2.0
        i = j + 1

    n_pos, n_neg = len(pos), len(neg)
    return float((ranks[n_neg:].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def main():
    parser = argparse.ArgumentParser(description="Evaluate the ZEROAUDIT intent engine")
    parser.add_argument("--samples", type=int, default=40000)
    parser.add_argument("--anomaly-rate", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=2024)
    parser.add_argument("--fpr", type=float, default=0.01)
    parser.add_argument("--model", default=DEFAULT_MODEL_PATH)
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    detector = AnomalyDetector(args.model)
    logger.info("intent engine backend: %s", detector.backend)
    if detector.backend != "onnx-fp16":
        logger.warning("ONNX model not loaded - evaluating the statistical fallback. "
                       "Build the model with: python -m ml.train_autoencoder")

    logger.info("building evaluation stream (%d events, %.0f%% anomalous) ...",
                args.samples, args.anomaly_rate * 100)
    stream = build_stream(args.samples, args.anomaly_rate, args.seed)

    normal, anomalous = [], []
    by_type = defaultdict(list)
    attribution = {"novelty": 0, "typology": 0}

    for txn in stream:
        acct = hashlib.sha3_256(txn["account_id"].encode()).hexdigest()
        cpty = hashlib.sha3_256(txn["counterparty_id"].encode()).hexdigest()
        result = detector.score(
            txn_id=txn["txn_id"], account_hash=acct, counterparty_hash=cpty,
            amount_cents=txn["amount_cents"], txn_type=txn["txn_type"],
            timestamp_ns=txn["timestamp_ns"],
        )
        score = result["anomaly_score"]
        if txn.get("ground_truth_anomaly"):
            anomalous.append(score)
            result["_account"] = txn["account_id"]
            by_type[txn["ground_truth_type"]].append(result)
            if result.get("typology_score", 0) >= result.get("novelty_score", 0):
                attribution["typology"] += 1
            else:
                attribution["novelty"] += 1
        else:
            normal.append(score)

    normal_a = np.asarray(normal)
    anom_a = np.asarray(anomalous)

    threshold = float(np.percentile(normal_a, 100 * (1 - args.fpr)))
    detection = float((anom_a > threshold).mean())
    auc = roc_auc(normal_a, anom_a)
    at_default = float((anom_a >= 0.75).mean())
    fpr_default = float((normal_a >= 0.75).mean())

    print()
    print("=" * 66)
    print("ZEROAUDIT INTENT ENGINE - EVALUATION")
    print("=" * 66)
    print("backend                 : %s" % detector.backend)
    print("stream                  : %d events (%d normal / %d anomalous)"
          % (len(stream), len(normal), len(anomalous)))
    print()
    print("ROC AUC                 : %.4f" % auc)
    print("threshold @ %.0f%% FPR     : %.4f" % (args.fpr * 100, threshold))
    print("detection @ %.0f%% FPR     : %.1f%%" % (args.fpr * 100, 100 * detection))
    print()
    print("at the shipped 0.75 quarantine line:")
    print("  recall                : %.1f%%" % (100 * at_default))
    print("  false-positive rate   : %.2f%%" % (100 * fpr_default))
    print()
    print("recall by typology (at 0.75):")
    print("  %-22s %8s %10s %12s" % ("type", "n", "recall", "mean score"))
    for atype in ANOMALY_TYPES:
        results = by_type.get(atype, [])
        if not results:
            continue
        scores = np.array([r["anomaly_score"] for r in results])
        print("  %-22s %8d %9.1f%% %12.3f"
              % (atype, len(results), 100 * (scores >= 0.75).mean(), scores.mean()))
    # Incident-level recall. A velocity burst is ONE laundering incident spread
    # over 8-15 transactions; flagging its 9th leg is a catch, not a miss. Only
    # transaction-level recall would score that as 8 failures and 1 success.
    bursts = defaultdict(list)
    for r in by_type.get("velocity_burst", []):
        bursts[r["_account"]].append(r["anomaly_score"])
    if bursts:
        caught = sum(1 for scores in bursts.values() if max(scores) >= 0.75)
        print("incident-level recall (velocity bursts):")
        print("  %d of %d distinct bursts flagged  (%.1f%%)"
              % (caught, len(bursts), 100 * caught / len(bursts)))
        print()

    # Macro average weights each typology equally. The transaction-weighted
    # figure is dominated by bursts, which emit 8-15 rows per incident while
    # every other typology emits one.
    per_type_recall = []
    for atype in ANOMALY_TYPES:
        results = by_type.get(atype, [])
        if results:
            scores = np.array([r["anomaly_score"] for r in results])
            per_type_recall.append(float((scores >= 0.75).mean()))
    if per_type_recall:
        print("macro-average recall across typologies: %.1f%%"
              % (100 * float(np.mean(per_type_recall))))
    print()
    total_attr = max(attribution["novelty"] + attribution["typology"], 1)
    print("flag attribution        : autoencoder %.0f%%  |  typology rules %.0f%%"
          % (100 * attribution["novelty"] / total_attr,
             100 * attribution["typology"] / total_attr))
    print("=" * 66)

    if args.json_out:
        payload = {
            "backend": detector.backend,
            "roc_auc": round(auc, 4),
            "detection_at_fpr": {"fpr": args.fpr, "recall": round(detection, 4)},
            "at_threshold_075": {"recall": round(at_default, 4), "fpr": round(fpr_default, 4)},
            "by_typology": {
                t: {
                    "n": len(v),
                    "recall": round(float((np.array([r["anomaly_score"] for r in v]) >= 0.75).mean()), 4),
                }
                for t, v in by_type.items()
            },
            "attribution": attribution,
        }
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        logger.info("wrote %s", args.json_out)


if __name__ == "__main__":
    main()
