# ZEROAUDIT

**Post-quantum zero-knowledge financial audit system for Tier-1 banks.**

Prove compliance. Reveal nothing.

ZEROAUDIT eliminates the single largest attack surface in enterprise finance — the transfer of raw financial data to auditors — by replacing data with cryptographic proof. Transactions are committed inside an Intel SGX enclave using a Lattice-based (LWE) scheme. Auditors receive only the commitment ledger and verify integrity without ever seeing amounts, accounts, or counterparties.

---

## The problem it solves

Traditional audit flow:

```
Bank data → Export → Transfer → Auditor sees everything → Breach risk
```

ZEROAUDIT flow:

```
Bank data → LWE commitment (SGX) → Public Kafka topic → Auditor verifies proof
```

The auditor trusts the mathematics and the bank's public key — no person, network, or system.

---

## Architecture

```

┌─────────────────────────────────────────────────────────────┐
│  Bank Perimeter (Internal)                                  │
│                                                             │
│  [Simulator] ──► (zeroaudit.transactions.raw)               │
│                        │                                    │
│                        ▼                                    │
│                 [Prover (SGX Enclave)]                      │
│                 - Verifies Signatures                       │
│                 - AI Intent Engine (Metadata)               │
│                 - LWE Lattice Commitment                    │
│                 - Memory Burn (memset)                      │
│                        │                                    │
│                 [Cassandra] (Append-only Ledger)            │
└────────────────────────┼────────────────────────────────────┘
                         │ 
                         │ LWE Commitments & Anomaly Scores
                         │ (Zero PII)
                         ▼
             (zeroaudit.transactions.committed)
                         │
┌────────────────────────┼────────────────────────────────────┐
│  External DMZ (Public) │                                    │
│                        ▼                                    │
│                 [Verifier API]                              │
│                 - Validates LWE Params                      │
│                 - Asserts PII = 0                           │
│                 - Exposes REST Endpoints                    │
│                        │                                    │
│                        ▼                                    │
│                 [Auditor Dashboard]                         │
│                 - Live Telemetry & Ledger                   │
└─────────────────────────────────────────────────────────────┘
```

| Layer | Technology | Role |
|---|---|---|
| Write storage | Apache Cassandra 4.1 | Append-only LSM-tree ledger |
| Message bus | Apache Kafka (Confluent 7.6) | Ordered, persistent, isolated topics |
| Secure compute | Intel SGX (simulated) | Hardware-encrypted prover enclave |
| Cryptography | LWE / Kyber-1024 | Post-quantum commitment scheme |
| Anomaly detection | FP16 ONNX autoencoder | Metadata-only, privacy-preserving |
| Verifier API | FastAPI + uvicorn | External DMZ — zero PII |
| Dashboard | Static HTML + nginx | Real-time audit terminal |

**Data flow:**

1. The simulator produces ~15 TPS of synthetic bank transactions to the `zeroaudit.transactions.raw` Kafka topic (5% anomaly rate).
2. The prover consumes raw transactions from inside the SGX enclave, verifies Ed25519 signatures, generates an LWE commitment, and publishes the commitment to `zeroaudit.transactions.committed`. Raw amounts never leave the enclave.
3. The verifier (external DMZ) consumes the committed topic, validates LWE parameters and binding hash format, and populates in-memory ring buffers.
4. The dashboard polls the verifier's FastAPI endpoints and renders live metrics, the commitment ledger, and the quarantine queue.

---

## Cryptographic guarantees

- **Binding** — once commitment `C` is published, the prover cannot change the underlying value without detection.
- **Hiding** — no one can recover the transaction amount or identity from `C`. The blinding factor and error vector are discarded inside the enclave.
- **Post-quantum** — security rests on Learning With Errors (LWE), hard for both classical and quantum adversaries.
- **Zero-knowledge** — the auditor receives proof of validity without ever seeing raw data.

LWE parameters used: `n=256, k=2, q=3329, η=2` (Kyber-1024 profile).

---

## AI anomaly detection

The intent engine runs entirely on transaction metadata — timestamps, account hashes, transaction types, velocity — never on raw amounts.

- FP16 ONNX autoencoder; reconstruction loss is the anomaly score.
- Score ≥ 0.75 → quarantine. Score ≥ 0.90 → critical flag.
- Benford's Law analysis on binding hash leading digits.
- Graph proximity to OFAC/RBI blacklists (hop count).
- Behavioral biometrics on account velocity patterns.

---

## Getting started

### Prerequisites

- Docker ≥ 20.10
- Docker Compose ≥ 2.0

### Run

```bash
git clone https://github.com/VishnuAravind-RG/zeroaudit.git
cd zeroaudit
docker compose up --build -d
```

Cassandra and Kafka take ~30 seconds to become healthy. Monitor with:

```bash
docker compose ps
docker compose logs -f
```

### Verify the system is working

```bash
# Verifier health
curl http://localhost:8001/health

# Live stats — tps and kafka_lag_ms should be non-zero
curl http://localhost:8001/stats

# Recent committed transactions — should return a populated list
curl http://localhost:8001/transactions

# Quarantine queue
curl http://localhost:8001/anomalies
```

Expected output after ~60 seconds:

```json
{
  "tps": 92.8,
  "total_commitments": 0,
  "kafka_lag_ms": 139.5,
  "pii_bytes": 0
}
```

`total_commitments` reflects the Cassandra persistent store; `tps` and the `/transactions` list reflect the verifier's in-memory ring buffer, which fills immediately.

### Dashboard

Open [http://localhost:3000](http://localhost:3000) in your browser.

---

## Service endpoints

| Service | Port | Description |
|---|---|---|
| Prover API | 8000 | Internal — commitment pipeline metrics |
| Verifier API | 8001 | External DMZ — audit endpoints |
| Dashboard | 3000 | Static HTML served by nginx |
| Kafka | 9092 | External listener (host) |
| Cassandra | 9042 | CQL interface |

---

## Project structure

```
zeroaudit/
├── prover/
│   ├── crypto/              # LWE commitment scheme, Ed25519 stubs
│   ├── config/settings.py   # Shared settings (KAFKA_BOOTSTRAP, topic names)
│   └── main.py              # FastAPI prover API
├── verifier/
│   ├── __main__.py          # Entry point for `python -m verifier.dashboard`
│   ├── dashboard.py         # FastAPI verifier API (14 endpoints)
│   ├── verify.py            # ExternalVerifier — LWE + PII checks
│   ├── anomaly_detector.py  # ONNX autoencoder scoring
│   ├── kafka_client/
│   │   └── consumer.py      # VerifierKafkaConsumer with backoff reconnect
│   └── components/          # Chart and sidebar data helpers
├── simulator/               # Synthetic bank transaction generator
├── dashboard/               # Static HTML + nginx Dockerfile
├── services/
│   ├── cassandra/init.cql   # Keyspace and table definitions
│   └── kafka/create_topics.sh
├── Dockerfile.prover
├── Dockerfile.verifier
├── Dockerfile.simulator
└── docker-compose.yml
```

---

## Configuration

All runtime configuration is passed via environment variables (see `docker-compose.yml`).

| Variable | Default | Description |
|---|---|---|
| `KAFKA_BOOTSTRAP` | `kafka:29092` | Kafka broker address (internal network) |
| `CASSANDRA_HOSTS` | `cassandra` | Cassandra contact point |
| `CASSANDRA_KEYSPACE` | `zeroaudit` | Keyspace name |
| `LOG_LEVEL` | `INFO` | Python log level |
| `SIM_TPS` | `15` | Simulator transaction rate |
| `SIM_ANOMALY_RATE` | `0.05` | Fraction of transactions flagged as anomalies |
| `ANOMALY_THRESHOLD` | `0.75` | Score threshold for quarantine |

---

## Implementation notes

**`verifier/__main__.py`** — required because the container runs `python -m verifier.dashboard`. Without this file, Python sets `__name__ = "verifier.dashboard"` and the `if __name__ == "__main__"` block in `dashboard.py` never executes. `__main__.py` calls `uvicorn.run()` unconditionally and configures the root logger so application logs appear in `docker logs`.

**Consumer group isolation** — the verifier generates a unique consumer group ID (`zeroaudit-verifier-{8 hex chars}`) on each process start. This prevents stale members from previous container runs stealing partition assignments, which would leave the new consumer with an empty assignment and zero messages despite being connected.

**Backoff reconnect** — `VerifierKafkaConsumer._consume_loop()` retries the Kafka connection with exponential backoff (2 → 4 → 8 → … → 30 s). This handles the startup race between the verifier container and Kafka becoming ready, even when `depends_on: condition: service_healthy` is set.

**`auto_offset_reset="earliest"`** — the verifier reads from the beginning of the topic on first connection so it catches up on messages produced before it started.

---

## Regulatory context

Designed with Indian financial regulation in mind (RBI guidelines) and OFAC sanctions list integration. The 5% simulator anomaly rate reflects realistic financial fraud rates. The zero-PII guarantee is enforced at every layer — the `pii_bytes: 0` assertion is checked on every message that crosses the enclave boundary.

---

## Contributing

1. Fork the repository.
2. Create a feature branch.
3. Run `docker compose up --build` and verify all endpoints return data.
4. Submit a pull request with a description of what was changed and why.

Keep the zero-PII invariant intact — no raw amounts, account numbers, or counterparty identities should appear in any log, metric, or API response.

---

## License

Provided for demonstration and educational purposes. Contact the author for licensing details.

---

*ZEROAUDIT — your data doesn't need to leave your vault to prove it's clean.*
