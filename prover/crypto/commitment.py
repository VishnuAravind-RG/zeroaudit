import hashlib
import secrets
import logging

logger = logging.getLogger(__name__)

def create_pedersen_commitment(value: float) -> tuple[str, int]:
    """
    Create a Pedersen commitment: C = xG + rH
    For simplicity, we use SHA-256 as a binding commitment.
    In production, use elliptic curve multiplication (e.g., with secp256k1).
    """
    # Convert value to integer cents to avoid floating point issues
    value_int = int(value * 100)
    
    # Generate a random blinding factor (256 bits)
    blinding_factor = secrets.randbits(256)
    
    # Create commitment: H(value || blinding_factor)
    commitment_input = f"{value_int}:{blinding_factor}".encode()
    commitment = hashlib.sha256(commitment_input).hexdigest()
    
    logger.debug(f"Created commitment for value {value}")
    return commitment, blinding_factor

def verify_commitment(commitment: str, value: float, blinding_factor: int) -> bool:
    """Verify a commitment by recomputing it."""
    value_int = int(value * 100)
    expected_input = f"{value_int}:{blinding_factor}".encode()
    expected = hashlib.sha256(expected_input).hexdigest()
    return commitment == expected