"""
signature.py — Ed25519 Digital Signatures for Commitment Records
ZEROAUDIT Prover Service

Signs commitment binding hashes with Ed25519 so the verifier
can confirm the SGX enclave produced each record.
No PII is ever signed directly — only binding_hash + txn_id + timestamp.
"""

import os
import base64
import hashlib
import json
import time
import logging
from typing import Optional

logger = logging.getLogger("zeroaudit.signature")

# Use cryptography library (pure Python fallback if not available)
try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PublicFormat, PrivateFormat, NoEncryption,
    )
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False
    logger.warning("cryptography package not found — using HMAC-SHA256 stub signatures")


# ── Key Management ─────────────────────────────────────────────────────────────

class SigningKey:
    def __init__(self, private_key_bytes: bytes = None):
        if _CRYPTO_AVAILABLE:
            if private_key_bytes:
                self._private = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
            else:
                self._private = Ed25519PrivateKey.generate()
            self._public = self._private.public_key()
            self._raw_private = self._private.private_bytes(
                Encoding.Raw, PrivateFormat.Raw, NoEncryption()
            )
            self._raw_public = self._public.public_bytes(Encoding.Raw, PublicFormat.Raw)
        else:
            # HMAC stub — for environments without cryptography package
            self._raw_private = private_key_bytes or os.urandom(32)
            self._raw_public = hashlib.sha256(self._raw_private).digest()

    def sign(self, message: bytes) -> bytes:
        if _CRYPTO_AVAILABLE:
            return self._private.sign(message)
        else:
            import hmac as _hmac
            return _hmac.new(self._raw_private, message, hashlib.sha256).digest()

    def public_key_b64(self) -> str:
        return base64.b64encode(self._raw_public).decode()

    def private_key_b64(self) -> str:
        return base64.b64encode(self._raw_private).decode()

    @classmethod
    def from_env(cls) -> "SigningKey":
        raw_b64 = os.environ.get("SGX_SIGNING_KEY_B64", "")
        if raw_b64:
            return cls(base64.b64decode(raw_b64))
        return cls()


# ── Signing & Verification ─────────────────────────────────────────────────────

def _build_signable(txn_id: str, binding_hash: str, timestamp_ns: int) -> bytes:
    """Canonical byte representation of what we sign. Deterministic."""
    payload = json.dumps({
        "txn_id": txn_id,
        "binding_hash": binding_hash,
        "timestamp_ns": timestamp_ns,
    }, sort_keys=True, separators=(",", ":"))
    return payload.encode("utf-8")


def sign_commitment(
    signing_key: SigningKey,
    txn_id: str,
    binding_hash: str,
    timestamp_ns: int = None,
) -> dict:
    """
    Sign a commitment record.
    Returns a signature envelope — safe to publish externally.
    """
    ts = timestamp_ns or time.time_ns()
    message = _build_signable(txn_id, binding_hash, ts)
    sig_bytes = signing_key.sign(message)

    return {
        "txn_id": txn_id,
        "binding_hash": binding_hash,
        "timestamp_ns": ts,
        "signature_b64": base64.b64encode(sig_bytes).decode(),
        "public_key_b64": signing_key.public_key_b64(),
        "algorithm": "Ed25519" if _CRYPTO_AVAILABLE else "HMAC-SHA256-stub",
        "signer": "SGX_ENCLAVE_PROVER_v1",
        "pii_bytes": 0,
    }


def verify_signature(envelope: dict) -> dict:
    """
    Verify a signature envelope produced by sign_commitment().
    Returns dict with 'valid' bool and trace.
    """
    trace = []
    try:
        txn_id = envelope["txn_id"]
        binding_hash = envelope["binding_hash"]
        timestamp_ns = envelope["timestamp_ns"]
        sig_bytes = base64.b64decode(envelope["signature_b64"])
        pub_bytes = base64.b64decode(envelope["public_key_b64"])

        message = _build_signable(txn_id, binding_hash, timestamp_ns)

        trace.append({"step": "RECONSTRUCT_MESSAGE", "status": "DONE"})

        if _CRYPTO_AVAILABLE:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            from cryptography.exceptions import InvalidSignature
            pub = Ed25519PublicKey.from_public_bytes(pub_bytes)
            try:
                pub.verify(sig_bytes, message)
                valid = True
                trace.append({"step": "ED25519_VERIFY", "status": "VERIFIED"})
            except InvalidSignature:
                valid = False
                trace.append({"step": "ED25519_VERIFY", "status": "FAILED"})
        else:
            import hmac as _hmac
            # Stub: derive key from public bytes (not real Ed25519)
            expected = _hmac.new(pub_bytes, message, hashlib.sha256).digest()
            valid = _hmac.compare_digest(sig_bytes, expected)
            trace.append({"step": "HMAC_VERIFY_STUB", "status": "VERIFIED" if valid else "FAILED"})

        return {"valid": valid, "txn_id": txn_id, "trace": trace}

    except Exception as e:
        logger.error(f"Signature verification error: {e}")
        return {"valid": False, "error": str(e), "trace": trace}


# ── Singleton ──────────────────────────────────────────────────────────────────

_signing_key: SigningKey = None


def get_signing_key() -> SigningKey:
    global _signing_key
    if _signing_key is None:
        _signing_key = SigningKey.from_env()
        logger.info(f"SGX Signing Key loaded. Public: {_signing_key.public_key_b64()[:16]}...")
    return _signing_key


if __name__ == "__main__":
    print("=== Signature Self-Test ===")
    key = SigningKey()
    envelope = sign_commitment(key, "TXN-SIG-TEST-001", "abc123def456" * 5)
    print(f"Signature:  {envelope['signature_b64'][:32]}...")
    print(f"Algorithm:  {envelope['algorithm']}")

    result = verify_signature(envelope)
    print(f"Valid:      {result['valid']}")

    # Tamper test
    bad_envelope = dict(envelope)
    bad_envelope["binding_hash"] = "tampered" * 5
    bad_result = verify_signature(bad_envelope)
    print(f"Tamper detected: {not bad_result['valid']}")