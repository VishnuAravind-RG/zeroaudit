"""
settings.py — ZEROAUDIT Prover Configuration
All secrets via environment variables. Never hardcode.
"""

import os
from dataclasses import dataclass


@dataclass
class Settings:
    # ── Kafka ──────────────────────────────────────────────────────────────────
    KAFKA_BOOTSTRAP: str = os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")
    KAFKA_TOPIC_INGEST: str = os.environ.get("KAFKA_TOPIC_INGEST", "zeroaudit.transactions.raw")
    KAFKA_TOPIC_COMMITTED: str = os.environ.get("KAFKA_TOPIC_COMMITTED", "zeroaudit.transactions.committed")
    KAFKA_TOPIC_ANOMALIES: str = os.environ.get("KAFKA_TOPIC_ANOMALIES", "zeroaudit.anomalies")
    KAFKA_CONSUMER_GROUP: str = os.environ.get("KAFKA_CONSUMER_GROUP", "zeroaudit-prover")
    KAFKA_MAX_POLL_RECORDS: int = int(os.environ.get("KAFKA_MAX_POLL_RECORDS", "500"))

    # ── Cassandra ──────────────────────────────────────────────────────────────
    CASSANDRA_HOSTS: list = None  # set in __post_init__
    CASSANDRA_KEYSPACE: str = os.environ.get("CASSANDRA_KEYSPACE", "zeroaudit")
    CASSANDRA_USERNAME: str = os.environ.get("CASSANDRA_USERNAME", "cassandra")
    CASSANDRA_PASSWORD: str = os.environ.get("CASSANDRA_PASSWORD", "cassandra")

    # ── Cryptography ──────────────────────────────────────────────────────────
    ZEROAUDIT_MASTER_KEY: str = os.environ.get("ZEROAUDIT_MASTER_KEY", "")
    LWE_SEED_HEX: str = os.environ.get("LWE_SEED_HEX", "")
    SGX_SIGNING_KEY_B64: str = os.environ.get("SGX_SIGNING_KEY_B64", "")

    # ── Prover ─────────────────────────────────────────────────────────────────
    PROVER_BATCH_SIZE: int = int(os.environ.get("PROVER_BATCH_SIZE", "100"))
    PROVER_POLL_INTERVAL_MS: int = int(os.environ.get("PROVER_POLL_INTERVAL_MS", "500"))
    ANOMALY_THRESHOLD: float = float(os.environ.get("ANOMALY_THRESHOLD", "0.75"))

    # ── API ────────────────────────────────────────────────────────────────────
    API_HOST: str = os.environ.get("API_HOST", "0.0.0.0")
    API_PORT: int = int(os.environ.get("API_PORT", "8000"))
    API_WORKERS: int = int(os.environ.get("API_WORKERS", "4"))
    CORS_ORIGINS: list = None  # set in __post_init__

    # ── S3 / Parquet Vault ─────────────────────────────────────────────────────
    S3_BUCKET: str = os.environ.get("S3_BUCKET", "zeroaudit-vault")
    S3_PREFIX: str = os.environ.get("S3_PREFIX", "commitments/")
    AWS_REGION: str = os.environ.get("AWS_REGION", "ap-south-1")

    # ── Logging ────────────────────────────────────────────────────────────────
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")
    LOG_FORMAT: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    def __post_init__(self):
        raw_hosts = os.environ.get("CASSANDRA_HOSTS", "localhost")
        self.CASSANDRA_HOSTS = [h.strip() for h in raw_hosts.split(",")]

        raw_cors = os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000")
        self.CORS_ORIGINS = [o.strip() for o in raw_cors.split(",")]


# Module-level singleton
settings = Settings()