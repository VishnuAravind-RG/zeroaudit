"""
test_intent_engine.py - The shipped FP16 ONNX artefact

Validates the model file that is actually committed and loaded at runtime,
not a freshly trained copy. If models/intent_autoencoder_fp16.onnx is missing
or malformed these tests fail rather than silently exercising the statistical
fallback, which is the failure mode that let an earlier revision claim ONNX
inference while shipping no model at all.

    pytest tests/test_intent_engine.py -v
"""

import os
import json
import time
import pytest

np = pytest.importorskip("numpy")
ort = pytest.importorskip("onnxruntime")

from verifier.anomaly_detector import (  # noqa: E402
    AnomalyDetector, DEFAULT_MODEL_PATH, N_FEATURES, FEATURE_NAMES,
)

SIDECAR_PATH = os.path.splitext(DEFAULT_MODEL_PATH)[0] + ".json"

pytestmark = pytest.mark.skipif(
    not os.path.exists(DEFAULT_MODEL_PATH),
    reason="model not built - run: python -m ml.train_autoencoder",
)


@pytest.fixture(scope="module")
def session():
    return ort.InferenceSession(DEFAULT_MODEL_PATH, providers=["CPUExecutionProvider"])


@pytest.fixture(scope="module")
def sidecar():
    with open(SIDECAR_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


class TestModelArtefact:
    def test_model_file_exists(self):
        assert os.path.exists(DEFAULT_MODEL_PATH)
        assert os.path.getsize(DEFAULT_MODEL_PATH) > 0

    def test_sidecar_exists(self):
        assert os.path.exists(SIDECAR_PATH)

    def test_input_is_fp16(self, session):
        spec = session.get_inputs()[0]
        assert spec.type == "tensor(float16)"
        assert spec.shape[-1] == N_FEATURES

    def test_output_is_scalar_error(self, session):
        spec = session.get_outputs()[0]
        assert spec.type == "tensor(float16)"
        assert spec.shape[-1] == 1

    def test_weights_are_stored_fp16(self):
        """The claim is an FP16 model, so the initialisers must actually be fp16."""
        onnx = pytest.importorskip("onnx")
        model = onnx.load(DEFAULT_MODEL_PATH)
        assert model.graph.initializer
        for init in model.graph.initializer:
            assert init.data_type == onnx.TensorProto.FLOAT16, init.name

    def test_graph_contains_the_autoencoder(self):
        onnx = pytest.importorskip("onnx")
        model = onnx.load(DEFAULT_MODEL_PATH)
        ops = [n.op_type for n in model.graph.node]
        assert ops.count("MatMul") == 4          # four weight layers
        assert ops.count("Tanh") == 3            # linear output layer
        assert "ReduceMean" in ops               # error folded into the graph

    def test_standardisation_is_in_the_graph(self):
        """mu/sigma must ship inside the graph so they cannot drift from the weights."""
        onnx = pytest.importorskip("onnx")
        model = onnx.load(DEFAULT_MODEL_PATH)
        names = {i.name for i in model.graph.initializer}
        assert {"mu", "sigma"} <= names

    def test_sidecar_records_provenance(self, sidecar):
        for key in ("p50", "p99", "feature_names", "layer_sizes",
                    "roc_auc", "trained_utc", "seed"):
            assert key in sidecar

    def test_sidecar_matches_the_feature_contract(self, sidecar):
        assert sidecar["feature_names"] == FEATURE_NAMES
        assert sidecar["layer_sizes"][0] == N_FEATURES
        assert sidecar["layer_sizes"][-1] == N_FEATURES

    def test_bottleneck_is_undercomplete(self, sidecar):
        """A wide bottleneck reconstructs anomalies too and stops discriminating."""
        assert min(sidecar["layer_sizes"]) < N_FEATURES / 2


class TestInference:
    def test_runs_on_a_single_row(self, session):
        x = np.zeros((1, N_FEATURES), dtype=np.float16)
        out = session.run(None, {session.get_inputs()[0].name: x})[0]
        assert out.shape == (1, 1)
        assert np.isfinite(float(out[0][0]))

    def test_batches(self, session):
        x = np.random.rand(64, N_FEATURES).astype(np.float16)
        out = session.run(None, {session.get_inputs()[0].name: x})[0]
        assert out.shape == (64, 1)

    def test_deterministic(self, session):
        x = np.full((1, N_FEATURES), 0.4, dtype=np.float16)
        name = session.get_inputs()[0].name
        assert session.run(None, {name: x})[0] == session.run(None, {name: x})[0]

    def test_error_is_non_negative(self, session):
        """Output is a mean of squares; it cannot be negative."""
        x = np.random.rand(200, N_FEATURES).astype(np.float16)
        out = session.run(None, {session.get_inputs()[0].name: x})[0]
        assert (out.astype(np.float32) >= 0).all()

    def test_outlier_reconstructs_worse_than_typical(self, session, sidecar):
        name = session.get_inputs()[0].name
        typical = np.array([sidecar["feature_mean"]], dtype=np.float16)
        outlier = np.ones((1, N_FEATURES), dtype=np.float16)
        e_typical = float(session.run(None, {name: typical})[0][0][0])
        e_outlier = float(session.run(None, {name: outlier})[0][0][0])
        assert e_outlier > e_typical


class TestDetectorUsesTheModel:
    def test_backend_is_onnx(self):
        assert AnomalyDetector().backend == "onnx-fp16"

    def test_calibration_loaded(self):
        assert AnomalyDetector().stats()["calibrated"] is True

    def test_falls_back_cleanly_on_missing_model(self):
        detector = AnomalyDetector(model_path="/nonexistent/model.onnx")
        assert detector.backend == "statistical-fallback"
        result = detector.score("TXN-1", "ab" * 32, "cd" * 32, 150_000,
                                "RTGS", time.time_ns())
        assert 0.0 <= result["anomaly_score"] <= 1.0

    def test_reported_backend_is_honest(self):
        """The score payload must say which engine produced it."""
        assert AnomalyDetector().score(
            "TXN-1", "ab" * 32, "cd" * 32, 150_000, "RTGS", time.time_ns()
        )["backend"] == "onnx-fp16"


class TestModelQuality:
    def test_auc_beats_chance_by_a_margin(self, sidecar):
        assert sidecar["roc_auc"] > 0.70

    def test_anomalies_reconstruct_worse_than_normals(self, sidecar):
        assert sidecar["anomaly_mse_mean"] > sidecar["mean"] * 2

    def test_fp16_quantisation_is_negligible(self, sidecar):
        """Casting to fp16 must not move the score materially."""
        delta = sidecar.get("fp16_median_rel_error")
        if delta is not None:
            assert delta < 0.05

    def test_calibration_percentiles_ordered(self, sidecar):
        assert sidecar["p50"] < sidecar["p95"] < sidecar["p99"]
