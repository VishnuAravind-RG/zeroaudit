import unittest
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from verifier.verifier import verify_commitment, verify_commitment_chain

class TestVerifier(unittest.TestCase):
    
    def test_verify_commitment_valid(self):
        """Test that a well-formed commitment is accepted."""
        commitment = "a" * 64  # 64 hex chars
        self.assertTrue(verify_commitment(commitment))
    
    def test_verify_commitment_invalid_length(self):
        """Test that a commitment with wrong length is rejected."""
        commitment = "a" * 63
        self.assertFalse(verify_commitment(commitment))
    
    def test_verify_commitment_invalid_chars(self):
        """Test that a commitment with non-hex chars is rejected."""
        commitment = "z" + "0" * 63
        self.assertFalse(verify_commitment(commitment))
    
    def test_chain_verification_empty(self):
        """Empty chain is considered valid."""
        self.assertTrue(verify_commitment_chain([]))
    
    def test_chain_verification_all_valid(self):
        """Chain with all valid commitments should be valid."""
        commitments = [
            {"transaction_id": "tx1", "commitment": "a"*64, "timestamp": "2025-01-01T00:00:00", "verified": True},
            {"transaction_id": "tx2", "commitment": "b"*64, "timestamp": "2025-01-01T00:00:01", "verified": True}
        ]
        self.assertTrue(verify_commitment_chain(commitments))
    
    def test_chain_verification_one_invalid(self):
        """Chain with an unverified commitment should be invalid."""
        commitments = [
            {"transaction_id": "tx1", "commitment": "a"*64, "timestamp": "2025-01-01T00:00:00", "verified": True},
            {"transaction_id": "tx2", "commitment": "b"*64, "timestamp": "2025-01-01T00:00:01", "verified": False}
        ]
        self.assertFalse(verify_commitment_chain(commitments))
    
    def test_chain_verification_timestamp_order(self):
        """Chain with out-of-order timestamps is still accepted (optional)."""
        commitments = [
            {"transaction_id": "tx2", "commitment": "b"*64, "timestamp": "2025-01-01T00:00:01", "verified": True},
            {"transaction_id": "tx1", "commitment": "a"*64, "timestamp": "2025-01-01T00:00:00", "verified": True}
        ]
        # Our implementation sorts by timestamp, so it should be valid.
        self.assertTrue(verify_commitment_chain(commitments))

if __name__ == '__main__':
    unittest.main()