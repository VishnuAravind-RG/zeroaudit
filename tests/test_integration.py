import unittest
import threading
import time
import json
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../verifier')))

# Mock the cryptographic functions for testing
def verify_bank_signature(data, signature, public_key):
    # Mocked function always returns True for valid signature
    return signature == "valid_sig" and public_key == "valid_key"

def create_pedersen_commitment(value):
    # Mocked function returns a fake commitment and blinding factor
    return f"commitment_{value}", f"blinding_{value}"

def verify_commitment(commitment):
    # Mocked function always returns True for valid commitment
    return commitment.startswith("commitment_")

# Mock classes to simulate Kafka (for integration test without actual Kafka)
class MockKafkaConsumer:
    def __init__(self, messages):
        self.messages = messages
        self.index = 0
    def __iter__(self):
        return self
    def __next__(self):
        if self.index >= len(self.messages):
            raise StopIteration
        msg = self.messages[self.index]
        self.index += 1
        return msg

class MockKafkaProducer:
    def __init__(self):
        self.sent = []
    def send(self, topic, value):
        self.sent.append((topic, value))
    def flush(self):
        pass

class TestIntegration(unittest.TestCase):
    
    def test_prover_flow(self):
        """Simulate the prover flow: receive transaction, verify, create commitment."""
        # Sample transaction as it would come from Debezium
        raw_tx = {
            "payload": {
                "after": {
                    "transaction_id": "test_001",
                    "account_id": "acc_123",
                    "amount": 1000.00,
                    "balance": 5000.00,
                    "bank_signature": "valid_sig",
                    "bank_public_key": "valid_key",
                    "timestamp": "2026-03-07T12:00:00Z"
                }
            }
        }
        
        # Mock consumer that yields this message once
        consumer = MockKafkaConsumer([raw_tx])
        producer = MockKafkaProducer()
        
        # Process one message (similar to consumer.py logic)
        for message in consumer:
            payload = message.get('payload', {})
            after = payload.get('after', {})
            tx_id = after.get('transaction_id')
            amount = float(after.get('amount', 0))
            balance = float(after.get('balance', 0))
            signature = after.get('bank_signature')
            pub_key = after.get('bank_public_key')
            
            signed_data = f"{tx_id}{amount}{balance}"
            # Use our verify function
            if verify_bank_signature(signed_data, signature, pub_key):
                commitment, blinding = create_pedersen_commitment(balance)
                # Send to commitments topic
                commitment_msg = {
                    "transaction_id": tx_id,
                    "commitment": commitment,
                    "timestamp": after.get('timestamp'),
                    "verified": True
                }
                producer.send("commitments", commitment_msg)
                producer.flush()
        
        # Check that one commitment was sent
        self.assertEqual(len(producer.sent), 1)
        topic, msg = producer.sent[0]
        self.assertEqual(topic, "commitments")
        self.assertEqual(msg["transaction_id"], "test_001")
        self.assertTrue(verify_commitment(msg["commitment"]))
    
    def test_verifier_flow(self):
        """Test that verifier accepts valid commitment."""
        # Create a commitment
        value = 1234.56
        commitment, blinding = create_pedersen_commitment(value)
        self.assertTrue(verify_commitment(commitment))
        
        # In a real flow, the verifier would not have the value, just the commitment.
        # Here we just test the verify_commitment function.

if __name__ == '__main__':
    unittest.main()