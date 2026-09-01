# ZEROAUDIT

**Post-quantum, privacy-preserving audit pipeline for financial transactions.**

Prove compliance. Reveal nothing.

An auditor needs to establish that a bank's transaction ledger is complete, unaltered, and free of laundering typologies. The conventional way to establish that is to hand the auditor the ledger — which turns every audit into a copy of the bank's most sensitive data sitting on someone else's infrastructure.

ZEROAUDIT replaces the data with proof. Transactions are committed under a Module-LWE lattice commitment inside the prover, chained, and signed. The auditor receives commitments, signatures, and chain links — never an amount, an account number, or a counterparty. When a specific transaction comes under scrutiny, the bank discloses **that one** transaction's opening, and the auditor verifies it against the commitment it already holds. Everything else stays sealed.

---

## What it actually does

```
                    ENCLAVE BOUNDARY                    │            DMZ
                                                        │
  simulator ──▶ Kafka ──▶ prover ─────────────────▶ Kafka ──▶ verifier ──▶ dashboard
   (signs)     raw      │                          committed │  (public       :3000
                        │ 1. verify ingress sig              │   keys only)
                        │ 2. score  (FP16 ONNX autoencoder)  │
                        │ 3. commit (Module-LWE)             │  ✓ Ed25519 signature
                        │ 4. chain  (SHA3-256 links)         │  ✓ binding hash
                        │ 5. sign   (Ed25519)                │  ✓ chain continuity
                        ▼                                    │  ✓ LWE parameters
                    Cassandra                                │  ✓ zero-PII assertion
                  (durable ledger)                           │
                                                        │
        sees raw amounts                                │   never sees an amount
        holds every secret                              │   holds only public keys
```

The boundary is the design. The intent engine runs **inside** the prover because scoring needs the amount, and that is the last point at which the amount legitimately exists. Only the resulting scalar score crosses into the DMZ.

| Layer | Technology | Role |
|---|---|---|
| Commitment | Module-LWE (Kyber-512 profile) | Post-quantum binding + hiding commitment |
| Attestation | Ed25519 | Proves the enclave authored each record |
| Tamper-evidence | SHA3-256 hash chain | Detects edit, insert, delete, reorder |
| Intent engine | FP16 ONNX autoencoder + typology rules | Anomaly detection on metadata + amount |
| Ledger | Apache Cassandra 4.1 | Append-only, day-partitioned, idempotent writes |
| Transport | Apache Kafka 7.6 | Ordered, replayable, topic-isolated |
| Verifier API | FastAPI | External DMZ, zero PII |
| Terminal | Static HTML + nginx | Live audit view |

---

## Cryptography

### Commitment scheme

Over the ring `R_q = Z_q[X]/(X^N + 1)` with the Kyber-512 profile `n=256, k=2, q=3329, η=2`:

```
KeyGen:   A ← expand(seed);  s,e ← CBD(η);  t = A·s + e        public: (seed, t)
Commit:   r  = HMAC-SHA256(K_master, txn_id)
          u  = Aᵀ·r + e₁
          v  = ⟨t, r⟩ + e₂ + encode(m)
          C  = (u, v),   binding_hash = SHA3-256(C)
Open:     recompute C from (m, r) using the PUBLIC key, compare constant-time
```

- **Binding** — `encode()` spreads the full **64-bit** amount one bit per coefficient scaled by `⌊q/2⌋ = 1664`. Two distinct amounts differ in at least one coefficient by ~q/2, which no CBD(η=2) error term (max |e| = 2) can bridge.
- **Hiding** — `(u, v)` is pseudorandom under Module-LWE. The blinding vector and error terms derive from a secret master key and are discarded.
- **Post-quantum** — reduces to Module-LWE, believed hard for quantum adversaries.

### Tamper-evident chain

```
chain_hashᵢ = SHA3-256( chain_hashᵢ₋₁ ‖ binding_hashᵢ ‖ txn_idᵢ ‖ tsᵢ )
```

The Ed25519 signature covers `chain_hash`, so re-chaining a modified ledger requires the signing key. Editing, deleting, reordering, or inserting any record breaks every link after it.

### Selective disclosure

The auditor continuously verifies signatures, chain continuity, and the zero-PII assertion **without any secret**. To audit one specific transaction:

```bash
# bank discloses ONE opening
curl -X POST localhost:8000/audit/open \
  -H 'Content-Type: application/json' \
  -d '{"txn_id":"TXN-...","amount_cents":15000000}'

# auditor verifies it against the commitment already published
curl -X POST localhost:8001/verify/opening \
  -H 'Content-Type: application/json' \
  -d '{"txn_id":"TXN-...","amount_cents":15000000,"blinding_seed_hex":"..."}'
```

A bank that misstates the amount cannot produce a blinding factor that makes the recomputation match. Every other record stays sealed.

---

## Intent engine

An undercomplete autoencoder — `10 → 6 → 3 → 6 → 10`, tanh, trained on **normal traffic only** — exported as a float16 ONNX graph. Reconstruction MSE is the novelty score. Training on normals alone is deliberate: labelled fraud is scarce and only teaches the frauds someone already caught.

Feature standardisation and the error computation are both **inside the graph**, so inference is one ORT call returning a scalar and the `mu`/`sigma` can never drift out of sync with the weights.

Novelty is blended with a deterministic **typology rules** layer (`score = max(novelty, typology)`). AML regulation requires that a flag be explainable — an analyst can act on `STRUCTURING_PATTERN`, but not on "reconstruction error 1.4" — so whenever a rule matches, the reported reason names the typology.

### Measured performance

Reproduce with `python -m ml.evaluate` (40,010 events, 5% anomalous, held-out seed):

```
ROC AUC                 : 0.69
at the 0.75 quarantine line:
  recall                : 39.6%
  false-positive rate   : 1.03%

recall by typology:
  structuring                 100.0%      ← rules
  sanctions_adjacent          100.0%      ← rules
  velocity_burst               36.3%      ← autoencoder
  benford_violation             5.8%
  offhours_settlement           0.8%

incident-level recall (velocity bursts): 6 of 6 distinct bursts (100%)
macro-average recall across typologies : 48.6%
flag attribution: autoencoder 44% | typology rules 56%
```

**Reading these numbers honestly.** Structuring and sanctions proximity are caught outright. Velocity bursts are caught **as incidents** — 100% of bursts are flagged, but only 36% of individual legs, because the first transaction in a burst is not yet a burst; detection is inherently lagged.

Benford and off-hours recall are low *by design*. A single amount with a leading 9 occurs in ~4.6% of clean traffic, and ~3% of legitimate settlement happens overnight. Neither is grounds to freeze a transaction. Those are **population-level** signals, handled by a sliding-window Pearson chi-squared test instead:

```
clean stream      : chi2 =     5.95   suspicious = False
10% fabricated    : chi2 =   456.85   suspicious = True
25% fabricated    : chi2 =  2803.89   suspicious = True
                    critical value (p=0.05, 8 dof) = 15.51
```

The 1.03% false-positive rate is the number that decides whether an alert queue is staffable.

---

## Getting started

### Prerequisites
- Docker ≥ 20.10, Docker Compose ≥ 2.0

### Run

```bash
git clone https://github.com/VishnuAravind-RG/zeroaudit.git
cd zeroaudit

python -m scripts.gen_keys > .env     # deterministic key material
docker compose up --build -d
```

Cassandra and Kafka take ~60s to report healthy; the other services wait on their health checks.

> **Why `.env` matters.** Without it every container mints ephemeral keys at boot, so a restarted prover can no longer open any commitment it published earlier and the verifier rejects every prior signature. Fine for a smoke test, useless for a ledger.

### Verify it is working

```bash
curl localhost:8001/health          # consumer_connected + has_prover_key must be true
curl localhost:8001/stats           # tps, verified, signature_verified, chain_broken
curl localhost:8001/chain/verify    # {"intact": true, ...}
curl localhost:8001/keys            # public keys the verifier checks against
curl localhost:8000/stats           # prover throughput + intent engine backend
```

`/stats` after ~60 seconds:

```json
{
  "tps": 14.8,
  "total_commitments": 892,
  "verified": 892,
  "failed": 0,
  "signature_verified": 892,
  "chain_broken": 0,
  "kafka_lag_records": 3,
  "pii_bytes": 0
}
```

`chain_broken: 0` and `failed: 0` are the assertions that matter: every record the DMZ received carried a valid enclave signature and linked correctly to its predecessor.

### Dashboard

[http://localhost:3000](http://localhost:3000) — polls the verifier API every 2s. If the verifier is unreachable it says so rather than rendering a synthetic feed.

---

## Retraining the intent engine

```bash
python -m ml.train_autoencoder --samples 100000 --epochs 250
python -m ml.evaluate
docker compose restart prover
```

Training runs in ~16s on CPU. Forward/backward passes and Adam are written directly in NumPy: the network is ~350 parameters, the explicit gradients are clearer than a framework dependency, the run is deterministic under a fixed seed, and the image avoids a multi-hundred-MB torch install. Export verifies fp32 and fp16 agree (median relative error ~0.05%) before writing the file.

---

## Service endpoints

| Service | Port | Notes |
|---|---|---|
| Prover | 8000 | **Enclave side.** Holds master key, lattice secret, signing key. Sees amounts. |
| Verifier | 8001 | **DMZ.** Public keys only. Never receives an amount. |
| Dashboard | 3000 | Static HTML, polls :8001 |
| Kafka | 9092 | External listener |
| Cassandra | 9042 | CQL |

**Prover:** `/health` `/stats` `/keys` `/chain/verify` `/audit/open` `/verify` `/ledger/export`
**Verifier:** `/health` `/stats` `/keys` `/transactions` `/anomalies` `/anomaly/{id}` `/chain/verify` `/verify/opening` `/resolve/{id}` `/ledger/export` `/charts/*` `/sidebar/*` `/stream`

---

## Project layout

```
zeroaudit/
├── prover/                     ENCLAVE SIDE — every secret lives here
│   ├── crypto/lwe.py           Module-LWE commitments, NumPy ring arithmetic
│   ├── crypto/commitment.py    Ledger, hash chain, selective disclosure
│   ├── crypto/signature.py     Ed25519 attestation
│   ├── cassandra_store.py      Query-driven Cassandra data model
│   ├── consumer.py             Kafka ingest: verify → score → commit → chain → sign
│   └── main.py                 Prover API
├── verifier/                   DMZ — public keys only
│   ├── verify.py               Signature, binding, chain, params, PII checks
│   ├── anomaly_detector.py     Intent engine (executes in the prover)
│   ├── kafka_client/consumer.py
│   └── dashboard.py            Verifier API
├── ml/
│   ├── train_autoencoder.py    NumPy training → FP16 ONNX export
│   └── evaluate.py             Reproducible evaluation harness
├── models/                     Committed model artefact + calibration sidecar
├── simulator/bank_sim.py       Signed synthetic traffic with real anomalies
├── dashboard/index.html        Audit terminal (live API)
├── scripts/gen_keys.py
└── tests/                      139 tests
```

---

## Tests

```bash
pip install -r requirements.txt
pytest
```

```
tests/test_crypto.py          45 passed    LWE, binding, chain, Ed25519
tests/test_verifier.py        51 passed    verification, intent engine, charts
tests/test_intent_engine.py   23 passed    the shipped ONNX artefact
tests/test_integration.py     20 passed    full pipeline + tamper attacks
                             139 passed
```

The tamper suite asserts that edited, deleted, reordered, and re-signed records are all rejected, and that a PII field injected into an envelope fails verification.

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `ZEROAUDIT_MASTER_KEY` | — | 32-byte hex. Derives per-transaction blinding factors. |
| `LWE_SEED_HEX` | — | 32-byte hex. Seeds the lattice public matrix and secret. |
| `SGX_SIGNING_KEY_B64` | — | 32-byte base64. Ed25519 signing seed. |
| `CASSANDRA_ENABLED` | `false` | Prover only. Verifier must never hold a write handle. |
| `ONNX_MODEL_PATH` | `models/intent_autoencoder_fp16.onnx` | Intent engine artefact |
| `ANOMALY_THRESHOLD` | `0.75` | Quarantine line |
| `INGRESS_REQUIRE_SIGNATURE` | `false` | Reject unsigned inbound transactions |
| `SIM_TPS` | `15` | Simulator rate |
| `SIM_ANOMALY_RATE` | `0.05` | Injected anomaly fraction |

---

## Threat model — what this does and does not defend against

**Defended.** A dishonest prover cannot restate an amount after publication (binding), forge a record (Ed25519), or quietly rewrite history (hash chain). A compromised DMZ leaks nothing: it holds no amounts and no secrets. A compromised Kafka topic cannot inject records without the signing key.

**Not defended.**

- **SGX is simulated.** The prover is a normal container. The enclave boundary is enforced by process and network separation, not by hardware attestation. Running this under real SGX (or Gramine) would require a remote-attestation handshake before the verifier trusts the signing key. The current design is honest about where that key comes from: the verifier fetches it over HTTP at startup and trusts it on first use.
- **Sanctions graph proximity is simulated.** Prefix buckets over the account hash stand in for a real graph traversal. In production this becomes a Neo4j/TigerGraph query; the interface is isolated in `graph_hops_to_blacklist()`.
- **Key custody.** Keys come from environment variables. Production would use an HSM or KMS.
- **A prover that lies at ingest.** ZEROAUDIT proves the published ledger matches what the prover committed. It cannot prove the prover was shown every transaction — that requires attestation over the source system.

---

## License

Provided for demonstration and educational purposes. Contact the author for licensing details.

---

*ZEROAUDIT — your data doesn't need to leave your vault to prove it's clean.*
