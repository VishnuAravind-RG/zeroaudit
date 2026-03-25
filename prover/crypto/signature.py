"""
prover/crypto/signature.py — Stub implementation for the prover (demo mode)
"""

import logging

logger = logging.getLogger("zeroaudit.prover.signature")

class SigningKey:
    """Stub signing key."""
    pass

def sign_commitment(commitment, key):
    """Stub for signing."""
    return b"stub_signature"

def verify_signature(commitment, signature, key):
    """Stub for verification. Always returns True for demo."""
    logger.debug("Signature verification stub: always True")
    return True

def get_signing_key():
    """Stub for getting signing key."""
    return SigningKey()

def verify_transaction_signature(record: dict) -> bool:
    """Stub for transaction signature verification. Always True for demo."""
    logger.debug("Transaction signature verification stub: always True")
    return True
