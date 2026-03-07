import os
from dotenv import load_dotenv

load_dotenv()  # Load variables from .env file

# Kafka settings
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
RAW_TOPIC = "zeroaudit.public.transactions"   # Debezium output topic (matches connector prefix + schema.table)
COMMITMENT_TOPIC = "commitments"

# Consumer group
CONSUMER_GROUP = "prover-group"

# Security (optional, but we keep placeholders)
# No secrets here – just config