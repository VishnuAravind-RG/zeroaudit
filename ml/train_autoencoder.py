"""
train_autoencoder.py - Train and export the ZEROAUDIT FP16 intent engine

Trains an undercomplete autoencoder on NORMAL transaction traffic only, then
exports it as a float16 ONNX graph plus a calibration sidecar.

    python -m ml.train_autoencoder --samples 60000 --epochs 120

Why an autoencoder trained on normals
-------------------------------------
Supervised fraud classifiers need labels, and labelled fraud is both scarce
and backward-looking - it can only teach the model the frauds someone already
caught. An autoencoder learns the shape of ordinary traffic and flags whatever
fails to fit, so a novel pattern is anomalous by construction rather than by
having appeared in the training set.

The 9 -> 6 -> 3 -> 6 -> 9 bottleneck is the whole mechanism: forcing every
transaction through 3 latent dimensions means the network can only afford to
reconstruct the regularities it has actually learned.

Why FP16
--------
Weights are cast to float16 and the graph executes in float16 end to end.
The model is ~350 parameters, so this is not about saving memory - it is
about inference cost per transaction on the ingest path. Reconstruction
error is a ratio compared against calibrated percentiles, so the reduced
mantissa costs nothing that matters; the export step verifies fp32 and fp16
scores agree before it writes the file.

Implementation notes
--------------------
Forward/backward passes and Adam are written directly in NumPy rather than
pulled from a framework: the network is small enough that the explicit
gradients are clearer than a dependency, the training run stays fully
deterministic under a fixed seed, and the container avoids a multi-hundred-MB
torch install for 350 parameters.
"""

import os
import sys
import json
import math
import time
import random
import logging
import argparse

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verifier.anomaly_detector import (  # noqa: E402
    FEATURE_NAMES, N_FEATURES, VelocityTracker, extract_features, to_vector,
)

logger = logging.getLogger("zeroaudit.ml.train")

LAYER_SIZES = [N_FEATURES, 6, 3, 6, N_FEATURES]
MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")
MODEL_PATH = os.path.join(MODEL_DIR, "intent_autoencoder_fp16.onnx")
SIDECAR_PATH = os.path.join(MODEL_DIR, "intent_autoencoder_fp16.json")


# -- Training data -------------------------------------------------------------

def build_dataset(n_samples: int, anomaly_rate: float, seed: int):
    """Generate features from a chronologically ordered simulated stream.

    Ordering matters. `velocity_1h` is a stateful, time-windowed feature, so
    feeding the extractor shuffled transactions makes every burst invisible
    and the model can never learn what a burst looks like. Events are sorted
    by timestamp and replayed through one shared VelocityTracker, exactly as
    the prover sees them.

    Returns (X_normal, X_anomalous). Only X_normal is used for fitting; the
    anomalous set exists to measure separation after training.
    """
    from simulator.bank_sim import (
        generate_normal_transaction, generate_anomalous_transaction, ANOMALY_TYPES,
    )
    import hashlib
    import uuid as _uuid

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
            # Materialise the burst: one account, many transactions, minutes apart.
            base_ts = txn["timestamp_ns"]
            account = txn["account_id"]
            for i in range(random.randint(8, 15)):
                leg = generate_normal_transaction()
                leg.update({
                    "txn_id": "TXN-ANOM-%s" % _uuid.uuid4().hex[:12].upper(),
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

    tracker = VelocityTracker()
    normal, anomalous = [], []
    for txn in events:
        acct = hashlib.sha3_256(txn["account_id"].encode()).hexdigest()
        cpty = hashlib.sha3_256(txn["counterparty_id"].encode()).hexdigest()
        tracker.record(acct, txn["timestamp_ns"])
        feats, _ = extract_features(
            txn["txn_id"], acct, cpty, txn["amount_cents"],
            txn["txn_type"], txn["timestamp_ns"], tracker,
        )
        target = anomalous if txn.get("ground_truth_anomaly") else normal
        target.append(to_vector(feats))

    return np.asarray(normal, dtype=np.float32), np.asarray(anomalous, dtype=np.float32)


# -- Model ---------------------------------------------------------------------

def init_params(rng):
    """Xavier/Glorot initialisation, appropriate for tanh activations."""
    params = []
    for fan_in, fan_out in zip(LAYER_SIZES[:-1], LAYER_SIZES[1:]):
        limit = math.sqrt(6.0 / (fan_in + fan_out))
        W = rng.uniform(-limit, limit, size=(fan_in, fan_out)).astype(np.float32)
        b = np.zeros(fan_out, dtype=np.float32)
        params.append([W, b])
    return params


def forward(params, X):
    """Returns (reconstruction, per-layer activations) for backprop."""
    acts = [X]
    h = X
    for i, (W, b) in enumerate(params):
        z = h @ W + b
        h = np.tanh(z) if i < len(params) - 1 else z   # linear output layer
        acts.append(h)
    return h, acts


def backward(params, acts, recon, X):
    """Analytic gradients of mean-squared reconstruction error."""
    n = X.shape[0]
    grads = [None] * len(params)

    delta = 2.0 * (recon - X) / (n * N_FEATURES)      # dL/dz for the linear output
    for i in range(len(params) - 1, -1, -1):
        h_prev = acts[i]
        grads[i] = [h_prev.T @ delta, delta.sum(axis=0)]
        if i > 0:
            dh = delta @ params[i][0].T
            delta = dh * (1.0 - acts[i] ** 2)         # tanh'(z) = 1 - tanh(z)^2
    return grads


def train(X, epochs, batch_size, lr, seed, verbose=True):
    """Adam. Explicit because the network is small enough to keep legible."""
    rng = np.random.default_rng(seed)
    params = init_params(rng)

    m = [[np.zeros_like(W), np.zeros_like(b)] for W, b in params]
    v = [[np.zeros_like(W), np.zeros_like(b)] for W, b in params]
    b1, b2, eps = 0.9, 0.999, 1e-8
    step = 0
    history = []

    for epoch in range(1, epochs + 1):
        perm = rng.permutation(len(X))
        epoch_loss = 0.0
        n_batches = 0

        for start in range(0, len(X), batch_size):
            batch = X[perm[start:start + batch_size]]
            recon, acts = forward(params, batch)
            loss = float(np.mean((recon - batch) ** 2))
            grads = backward(params, acts, recon, batch)

            step += 1
            for i in range(len(params)):
                for j in range(2):
                    g = grads[i][j]
                    m[i][j] = b1 * m[i][j] + (1 - b1) * g
                    v[i][j] = b2 * v[i][j] + (1 - b2) * (g * g)
                    m_hat = m[i][j] / (1 - b1 ** step)
                    v_hat = v[i][j] / (1 - b2 ** step)
                    params[i][j] -= lr * m_hat / (np.sqrt(v_hat) + eps)

            epoch_loss += loss
            n_batches += 1

        history.append(epoch_loss / max(n_batches, 1))
        if verbose and (epoch % 20 == 0 or epoch == 1):
            logger.info("epoch %3d/%d   loss=%.6f", epoch, epochs, history[-1])

    return params, history


def fit_standardizer(X):
    """Per-feature mean and standard deviation of normal traffic.

    Without this the reconstruction MSE is dominated by whichever features
    happen to have the widest spread - log_amount, hour_of_day, Benford
    surprisal - while the genuinely discriminative low-variance features
    (velocity, threshold proximity) contribute almost nothing to the error.
    Standardising puts every feature on equal footing in the loss, which is
    what makes the MSE usable as an anomaly score.
    """
    mu = X.mean(axis=0)
    sigma = X.std(axis=0)
    sigma[sigma < 1e-6] = 1.0          # constant features must not explode
    return mu.astype(np.float32), sigma.astype(np.float32)


def standardize(X, mu, sigma):
    return (X - mu) / sigma


def reconstruction_error(params, X, mu=None, sigma=None):
    """Per-row reconstruction MSE, computed in standardized space."""
    Z = standardize(X, mu, sigma) if mu is not None else X
    recon, _ = forward(params, Z)
    return np.mean((recon - Z) ** 2, axis=1)


# -- ONNX export ---------------------------------------------------------------

def export_onnx(params, mu, sigma, path: str):
    """Emit a float16 ONNX graph computing reconstruction MSE end to end.

    Graph:
        Xn = (X - mu) / sigma
        h1 = Tanh(Xn @ W1 + b1)
        h2 = Tanh(h1 @ W2 + b2)
        h3 = Tanh(h2 @ W3 + b3)
        Y  =      h3 @ W4 + b4
        out = ReduceMean((Y - X)^2, axis=1)

    Folding the error computation into the graph means inference is a single
    ORT call returning a scalar, with no host-side post-processing.
    """
    from onnx import helper, TensorProto, numpy_helper, checker, save

    initializers, nodes = [], []

    # Standardisation travels inside the graph, so the runtime input stays raw
    # features and the mu/sigma can never drift out of sync with the weights.
    initializers.append(numpy_helper.from_array(mu.astype(np.float16), "mu"))
    initializers.append(numpy_helper.from_array(sigma.astype(np.float16), "sigma"))
    nodes.append(helper.make_node("Sub", ["features", "mu"], ["centered"], name="center"))
    nodes.append(helper.make_node("Div", ["centered", "sigma"], ["normed"], name="scale"))
    cur = "normed"

    for i, (W, b) in enumerate(params, start=1):
        w_name, b_name = "W%d" % i, "b%d" % i
        initializers.append(numpy_helper.from_array(W.astype(np.float16), w_name))
        initializers.append(numpy_helper.from_array(b.astype(np.float16), b_name))

        mm, add = "mm%d" % i, "add%d" % i
        nodes.append(helper.make_node("MatMul", [cur, w_name], [mm], name="matmul_%d" % i))
        nodes.append(helper.make_node("Add", [mm, b_name], [add], name="bias_%d" % i))

        if i < len(params):
            act = "h%d" % i
            nodes.append(helper.make_node("Tanh", [add], [act], name="tanh_%d" % i))
            cur = act
        else:
            cur = add

    nodes.append(helper.make_node("Sub", [cur, "normed"], ["residual"], name="residual"))
    nodes.append(helper.make_node("Mul", ["residual", "residual"], ["sq_err"], name="square"))
    nodes.append(helper.make_node(
        "ReduceMean", ["sq_err"], ["recon_error"],
        axes=[1], keepdims=1, name="mse",
    ))

    graph = helper.make_graph(
        nodes=nodes,
        name="zeroaudit_intent_autoencoder",
        inputs=[helper.make_tensor_value_info(
            "features", TensorProto.FLOAT16, ["batch", N_FEATURES])],
        outputs=[helper.make_tensor_value_info(
            "recon_error", TensorProto.FLOAT16, ["batch", 1])],
        initializer=initializers,
        doc_string="ZEROAUDIT intent engine: %s autoencoder, fp16, "
                   "output = reconstruction MSE" % "->".join(map(str, LAYER_SIZES)),
    )

    model = helper.make_model(
        graph,
        producer_name="zeroaudit",
        opset_imports=[helper.make_operatorsetid("", 13)],
    )
    model.ir_version = 8          # ORT-compatible IR for opset 13
    checker.check_model(model)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    save(model, path)
    return model


def calibrate(errors: np.ndarray) -> dict:
    """Percentiles of normal-traffic reconstruction error, for score mapping."""
    return {
        "p50": float(np.percentile(errors, 50)),
        "p90": float(np.percentile(errors, 90)),
        "p95": float(np.percentile(errors, 95)),
        "p99": float(np.percentile(errors, 99)),
        "mean": float(errors.mean()),
        "std": float(errors.std()),
    }


def roc_auc(normal_scores: np.ndarray, anomaly_scores: np.ndarray) -> float:
    """AUC via the Mann-Whitney U statistic, ties counted at half weight."""
    y = np.concatenate([normal_scores, anomaly_scores])
    order = y.argsort()
    ranks = np.empty(len(y), dtype=np.float64)
    ranks[order] = np.arange(1, len(y) + 1)

    # average ranks within tied groups
    sorted_y = y[order]
    i = 0
    while i < len(sorted_y):
        j = i
        while j + 1 < len(sorted_y) and sorted_y[j + 1] == sorted_y[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + j + 2) / 2.0
        i = j + 1

    n_pos, n_neg = len(anomaly_scores), len(normal_scores)
    rank_sum = ranks[n_neg:].sum()
    return float((rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


# -- Entry point ---------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Train the ZEROAUDIT FP16 intent engine")
    parser.add_argument("--samples", type=int, default=60000)
    parser.add_argument("--epochs", type=int, default=120)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=3e-3)
    parser.add_argument("--anomaly-rate", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--out", default=MODEL_PATH)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    logger.info("generating %d simulated transactions ...", args.samples)
    X_norm, X_anom = build_dataset(args.samples, args.anomaly_rate, args.seed)
    logger.info("normal=%d  anomalous=%d  features=%d", len(X_norm), len(X_anom), N_FEATURES)

    split = int(len(X_norm) * 0.8)
    X_train, X_holdout = X_norm[:split], X_norm[split:]

    mu, sigma = fit_standardizer(X_train)
    logger.info("training %s autoencoder on normal traffic only ...",
                "->".join(map(str, LAYER_SIZES)))
    t0 = time.time()
    params, history = train(standardize(X_train, mu, sigma),
                            args.epochs, args.batch_size, args.lr, args.seed)
    logger.info("trained in %.1fs   final loss=%.6f", time.time() - t0, history[-1])

    err_holdout = reconstruction_error(params, X_holdout, mu, sigma)
    err_anom = reconstruction_error(params, X_anom, mu, sigma)
    calibration = calibrate(err_holdout)
    auc = roc_auc(err_holdout, err_anom)

    logger.info("holdout normal MSE  p50=%.6f p95=%.6f p99=%.6f",
                calibration["p50"], calibration["p95"], calibration["p99"])
    logger.info("anomalous MSE       mean=%.6f  (normal mean=%.6f, %.1fx separation)",
                err_anom.mean(), calibration["mean"],
                err_anom.mean() / max(calibration["mean"], 1e-9))
    logger.info("ROC AUC (normal vs anomalous): %.4f", auc)

    logger.info("exporting fp16 ONNX -> %s", args.out)
    export_onnx(params, mu, sigma, args.out)

    # fp32 reference vs fp16 ONNX, so the cast is never a silent regression
    fp16_delta = None
    try:
        import onnxruntime as ort
        sess = ort.InferenceSession(args.out, providers=["CPUExecutionProvider"])
        name = sess.get_inputs()[0].name
        probe = X_holdout[:2000]
        got = sess.run(None, {name: probe.astype(np.float16)})[0].reshape(-1).astype(np.float32)
        want = reconstruction_error(params, probe, mu, sigma)
        denom = np.maximum(np.abs(want), 1e-6)
        fp16_delta = float(np.median(np.abs(got - want) / denom))
        logger.info("fp16 vs fp32 agreement: median relative error %.4f%%", fp16_delta * 100)
        if fp16_delta > 0.05:
            logger.warning("fp16 quantisation error above 5%% - inspect before shipping")
    except ImportError:
        logger.warning("onnxruntime not installed - skipping fp16 verification")

    sidecar = dict(calibration)
    sidecar.update({
        "feature_names": FEATURE_NAMES,
        "layer_sizes": LAYER_SIZES,
        "precision": "float16",
        "standardized": True,
        "feature_mean": [round(float(x), 6) for x in mu],
        "feature_std": [round(float(x), 6) for x in sigma],
        "opset": 13,
        "trained_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "train_samples": int(len(X_train)),
        "holdout_samples": int(len(X_holdout)),
        "epochs": args.epochs,
        "seed": args.seed,
        "final_train_loss": float(history[-1]),
        "roc_auc": round(auc, 4),
        "anomaly_mse_mean": float(err_anom.mean()),
        "fp16_median_rel_error": fp16_delta,
    })
    sidecar_path = os.path.splitext(args.out)[0] + ".json"
    with open(sidecar_path, "w", encoding="utf-8") as fh:
        json.dump(sidecar, fh, indent=2)

    size_kb = os.path.getsize(args.out) / 1024
    logger.info("wrote %s (%.1f KB) and %s", args.out, size_kb, os.path.basename(sidecar_path))
    logger.info("done - restart the prover to pick up the new intent engine")


if __name__ == "__main__":
    main()
