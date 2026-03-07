import json
import logging
from datetime import datetime
from kafka import KafkaConsumer, KafkaProducer
from prover.config import settings
from prover.crypto.signature import verify_bank_signature
from prover.crypto.commitment import create_pedersen_commitment

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def start_consumer():
    """Main loop: consume raw transactions, verify, commit, produce to commitments topic."""
    # Consumer for raw transactions (from Debezium)
    consumer = KafkaConsumer(
        settings.RAW_TOPIC,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        value_deserializer=lambda m: json.loads(m.decode('utf-8')),
        auto_offset_reset='earliest',
        group_id=settings.CONSUMER_GROUP,
        enable_auto_commit=True
    )

    # Producer for commitments
    producer = KafkaProducer(
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        value_serializer=lambda v: json.dumps(v, default=str).encode('utf-8')
    )

    logger.info(f"Listening to topic: {settings.RAW_TOPIC}")

    for message in consumer:
        try:
            # Debezium message structure: { "payload": { "after": {...}, "before": {...}, "op": "c" } }
            payload = message.value.get('payload', {})
            if payload.get('op') == 'c' or payload.get('op') == 'r':  # create or read (snapshot)
                after = payload.get('after')
                if after:
                    # Extract fields
                    tx_id = after.get('transaction_id')
                    amount = float(after.get('amount', 0))
                    balance = float(after.get('balance', 0))
                    signature = after.get('bank_signature')
                    pub_key = after.get('bank_public_key')
                    timestamp = after.get('timestamp', datetime.now().isoformat())

                    # Build string that was originally signed (in real system, this would be a canonical representation)
                    # For demo, we just concatenate tx_id, amount, balance
                    signed_data = f"{tx_id}{amount}{balance}"

                    # Verify signature
                    if verify_bank_signature(signed_data, signature, pub_key):
                        logger.info(f"✅ Signature valid for {tx_id}")

                        # Create commitment (using balance as the secret value)
                        commitment, blinding = create_pedersen_commitment(balance)

                        # Produce commitment to output topic
                        commitment_msg = {
                            "transaction_id": tx_id,
                            "commitment": commitment,
                            "timestamp": timestamp,
                            "verified": True
                        }
                        producer.send(settings.COMMITMENT_TOPIC, value=commitment_msg)
                        producer.flush()
                        logger.info(f"✅ Commitment published for {tx_id}")

                        # Securely erase sensitive data (in Python, just let GC handle)
                        # In a high-security system, you'd overwrite memory, but here it's fine.
                    else:
                        logger.error(f"❌ Signature invalid for {tx_id}")
            else:
                logger.debug(f"Ignored event type: {payload.get('op')}")
        except Exception as e:
            logger.exception(f"Error processing message: {e}")