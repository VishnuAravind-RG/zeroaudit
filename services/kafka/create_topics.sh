#!/bin/bash
# ZEROAUDIT Kafka Topic Initialization
set -e

KAFKA_BROKER="kafka:29092"
REPLICATION=1
PARTITIONS=6

echo "Waiting for Kafka to be ready..."
sleep 10

create_topic() {
  local TOPIC=$1
  local RETENTION_MS=$2
  echo "Creating topic: $TOPIC"
  kafka-topics --bootstrap-server $KAFKA_BROKER \
    --create --if-not-exists \
    --topic $TOPIC \
    --partitions $PARTITIONS \
    --replication-factor $REPLICATION \
    --config retention.ms=$RETENTION_MS \
    --config cleanup.policy=delete \
    --config compression.type=lz4
}

# Raw transactions from Cassandra CDC
create_topic "zeroaudit.transactions.raw"       604800000   # 7 days

# LWE-committed, signed records
create_topic "zeroaudit.transactions.committed" 2592000000  # 30 days

# Anomaly flags (quarantined transactions)
create_topic "zeroaudit.anomalies"              2592000000  # 30 days

# Dead letter queue
create_topic "zeroaudit.dlq"                    604800000   # 7 days

echo "All Kafka topics created successfully."
kafka-topics --bootstrap-server $KAFKA_BROKER --list