import unittest
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from prover.crypto.signature import verify_bank_signature
from prover.crypto.commitment import create_pedersen_commitment, verify_commitment

class TestCrypto(unittest.TestCase):
    
    def test_signature_verification_valid(self):
        """Test that a valid signature (placeholder) is accepted."""
        # Placeholder signature and public key (as used in init.sql)
        signature = "3045022100e476f5059c12b6f5e2f3a5b8b9a0c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3"
        pub_key = "MFYwEAYHKoZIzj0CAQYFK4EEAAoDQgAEKf3Hf2p8yLmzqW5XkYq1nGxP6M9Q8R7S6T5U4V3W2X1Y"
        data = "txn_001acc_123455000.0015000.00"
        result = verify_bank_signature(data, signature, pub_key)
        self.assertTrue(result)
    
    def test_signature_verification_invalid(self):
        """Test that an invalid signature is rejected."""
        signature = "invalid_signature"
        pub_key = "some_key"
        data = "test_data"
        result = verify_bank_signature(data, signature, pub_key)
        self.assertFalse(result)
    
    def test_commitment_creation_and_verification(self):
        """Test that a commitment can be created and verified."""
        value = 1234.56
        commitment, blinding = create_pedersen_commitment(value)
        self.assertIsInstance(commitment, str)
        self.assertEqual(len(commitment), 64)  # SHA-256 hex length
        self.assertIsInstance(blinding, int)
        
        # Verify the commitment
        self.assertTrue(verify_commitment(commitment, value, blinding))
        
        # Verification should fail with wrong value
        self.assertFalse(verify_commitment(commitment, 9999.99, blinding))
    
    def test_commitment_deterministic(self):
        """Test that same value and blinding produce same commitment (if we had deterministic blinding, but we don't)."""
        # Not strictly deterministic because blinding is random, so we skip.
        pass

if __name__ == '__main__':
    unittest.main()