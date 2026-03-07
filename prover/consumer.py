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
    """Main loop: consume raw transactions (after Debezium transform), verify, produce commitments."""
    consumer = KafkaConsumer(
        settings.RAW_TOPIC,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        value_deserializer=lambda m: json.loads(m.decode('utf-8')),
        auto_offset_reset='earliest',
        group_id=settings.CONSUMER_GROUP,
        enable_auto_commit=True
    )

    producer = KafkaProducer(
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        value_serializer=lambda v: json.dumps(v, default=str).encode('utf-8')
    )

    logger.info(f"Listening to topic: {settings.RAW_TOPIC}")

    for message in consumer:
        try:
            # After ExtractNewRecordState transform, the message is directly the 'after' data.
            # It may contain fields like id, transaction_id, account_id, amount, balance, etc.
            record = message.value

            # Basic validation: must have transaction_id
            tx_id = record.get('transaction_id')
            if not tx_id:
                logger.debug("Skipping message without transaction_id")
                continue

            # Extract fields
            account_id = record.get('account_id')
            amount = float(record.get('amount', 0))
            balance = float(record.get('balance', 0))
            signature = record.get('bank_signature')
            pub_key = record.get('bank_public_key')
            timestamp = record.get('timestamp', datetime.now().isoformat())
            metadata_raw = record.get('metadata')

            # Parse metadata if it's a JSON string (as seen in the console output)
            metadata = {}
            if metadata_raw:
                if isinstance(metadata_raw, str):
                    try:
                        metadata = json.loads(metadata_raw)
                    except:
                        metadata = {'raw': metadata_raw}
                else:
                    metadata = metadata_raw

            transaction_type = metadata.get('type') if metadata else None

            # Build the signed data (same as bank_sim used)
            signed_data = f"{tx_id}{amount}{balance}"

            # Verify signature
            if verify_bank_signature(signed_data, signature, pub_key):
                logger.info(f"✅ Signature valid for {tx_id}")

                # Create commitment (using balance as the secret value)
                commitment, blinding = create_pedersen_commitment(balance)

                # Produce commitment to output topic
                commitment_msg = {
                    "transaction_id": tx_id,
                    "account_id": account_id,
                    "transaction_type": transaction_type,
                    "commitment": commitment,
                    "timestamp": timestamp,
                    "verified": True
                }
                producer.send(settings.COMMITMENT_TOPIC, value=commitment_msg)
                producer.flush()
                logger.info(f"✅ Commitment published for {tx_id} (account={account_id}, type={transaction_type})")
            else:
                logger.error(f"❌ Signature invalid for {tx_id}")
        except Exception as e:
            logger.exception(f"Error processing message: {e}")