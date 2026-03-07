#!/bin/bash

# =====================================================
# Create Kafka topics for ZEROAUDIT
# Run this after Kafka is up and healthy.
# =====================================================

echo "Waiting for Kafka to be ready..."
cub kafka-ready -b kafka:9092 1 20   # part of the Kafka image, waits for broker

# Topics
# - raw-transactions: Debezium will publish here (actual name may vary based on connector config)
# - commitments: our prover will publish commitments here

# Debezium, when configured with topic.prefix="zeroaudit", will publish to:
#   zeroaudit.public.transactions
# So we don't need to create it manually; Kafka auto-creates topics with default settings.
# But we'll create the commitments topic explicitly.

echo "Creating 'commitments' topic..."
kafka-topics --bootstrap-server kafka:9092 \
             --create \
             --if-not-exists \
             --topic commitments \
             --partitions 3 \
             --replication-factor 1

echo "Topics created successfully!"