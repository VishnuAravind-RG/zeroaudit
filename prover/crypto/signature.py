"""
signature.py - Ed25519 attestation for ZEROAUDIT commitments

Every commitment leaving the prover enclave is signed. The verifier, sitting
in the external DMZ, holds only the public key and can therefore establish
two things without any shared secret:

  1. authenticity - the record was produced by the enclave that holds the
     signing key, not injected onto the Kafka topic by anyone else;
  2. integrity    - not one byte of the signed envelope has changed in
     transit or at rest.

The signed payload deliberately covers the hash-chain link as well as the
commitment, so a tampered ledger fails signature verification rather than
merely failing a chain walk.

Key handling
------------
SGX_SIGNING_KEY_B64 (32-byte Ed25519 seed, base64) is read from the
environment so a restarted prover keeps its identity. Absent that, an
ephemeral key is generated and loudly logged - fine for a scratch run,
useless for a ledger anyone intends to audit later.
"""

import os
import base64
import logging
from typing import Optional

logger = logging.getLogger("zeroaudit.prover.signature")

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives import serialization
    from cryptography.exceptions import InvalidSignature
    _CRYPTO = True
except ImportError:  # pragma: no cover - only on minimal installs
    _CRYPTO = False
    logger.error(
        "cryptography not installed - signatures cannot be produced or checked"
    )

    class InvalidSignature(Exception):
        pass


# -- Canonical signing payload -----------------------------------------------

def signing_payload(
    txn_id: str,
    binding_hash: str,
    timestamp_ns: int,
    chain_hash: str = "",
) -> bytes:
    """Deterministic byte encoding of what a signature commits to.

    Field-separated with a byte that cannot appear in any of the hex or ID
    fields, so distinct field splits cannot produce identical payloads.
    """
    parts = [txn_id, binding_hash, str(timestamp_ns), chain_hash]
    return b"\x1f".join(p.encode("utf-8") for p in parts)


# -- Signing key --------------------------------------------------------------

class SigningKey:
    """Ed25519 signing key held inside the prover enclave."""

    def __init__(self, seed: bytes = None):
        if not _CRYPTO:
            raise RuntimeError("cryptography is required for signing")
        if seed is not None:
            if len(seed) != 32:
                raise ValueError("Ed25519 seed must be exactly 32 bytes")
            self._key = Ed25519PrivateKey.from_private_bytes(seed)
        else:
            self._key = Ed25519PrivateKey.generate()

    def sign(self, payload: bytes) -> str:
        return base64.b64encode(self._key.sign(payload)).decode()

    def public_key_b64(self) -> str:
        raw = self._key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return base64.b64encode(raw).decode()

    def key_id(self) -> str:
        """Short stable identifier, published alongside every signature."""
        import hashlib
        raw = self._key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return hashlib.sha3_256(raw).hexdigest()[:16]


# -- Signing / verification ---------------------------------------------------

def sign_commitment(
    key: SigningKey,
    txn_id: str,
    binding_hash: str,
    timestamp_ns: int,
    chain_hash: str = "",
) -> dict:
    """Sign a commitment envelope. Returns the fields to publish."""
    payload = signing_payload(txn_id, binding_hash, timestamp_ns, chain_hash)
    return {
        "signature_b64": key.sign(payload),
        "signing_key_id": key.key_id(),
        "sig_alg": "Ed25519",
    }


def verify_signature(
    public_key_b64: str,
    signature_b64: str,
    txn_id: str,
    binding_hash: str,
    timestamp_ns: int,
    chain_hash: str = "",
) -> bool:
    """Verify a published signature using only the public key."""
    if not _CRYPTO:
        return False
    try:
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        pub.verify(
            base64.b64decode(signature_b64),
            signing_payload(txn_id, binding_hash, timestamp_ns, chain_hash),
        )
        return True
    except (InvalidSignature, ValueError, TypeError) as exc:
        logger.debug("signature rejected for %s: %s", txn_id, type(exc).__name__)
        return False


def verify_transaction_signature(record: dict) -> bool:
    """Ingress firewall: check a raw transaction's signature before committing.

    The simulator signs each transaction with a per-run bank key. Records that
    carry no signature are accepted only when INGRESS_REQUIRE_SIGNATURE is off,
    which keeps the pipeline runnable against unsigned upstream feeds.
    """
    sig = record.get("signature_b64")
    pub = record.get("bank_pubkey_b64")
    require = os.environ.get("INGRESS_REQUIRE_SIGNATURE", "false").lower() == "true"

    if not sig or not pub:
        if require:
            logger.warning(
                "unsigned transaction %s rejected (INGRESS_REQUIRE_SIGNATURE=true)",
                record.get("txn_id"),
            )
            return False
        return True

    return verify_signature(
        public_key_b64=pub,
        signature_b64=sig,
        txn_id=record.get("txn_id", ""),
        binding_hash=record.get("payload_hash", ""),
        timestamp_ns=record.get("timestamp_ns", 0),
    )


# -- Process-wide signing key -------------------------------------------------

_SIGNING_KEY: Optional[SigningKey] = None


def get_signing_key() -> SigningKey:
    global _SIGNING_KEY
    if _SIGNING_KEY is None:
        raw = os.environ.get("SGX_SIGNING_KEY_B64", "").strip()
        seed = None
        if raw:
            try:
                decoded = base64.b64decode(raw)
                if len(decoded) == 32:
                    seed = decoded
                else:
                    logger.error(
                        "SGX_SIGNING_KEY_B64 decodes to %d bytes, expected 32 - "
                        "generating an ephemeral key", len(decoded)
                    )
            except Exception:
                logger.error("SGX_SIGNING_KEY_B64 is not valid base64 - generating an ephemeral key")
        else:
            logger.warning(
                "SGX_SIGNING_KEY_B64 unset - generating an ephemeral signing key. "
                "Previously published signatures will not verify against it."
            )
        _SIGNING_KEY = SigningKey(seed)
        logger.info("Ed25519 signing key ready - key_id=%s", _SIGNING_KEY.key_id())
    return _SIGNING_KEY


def generate_seed_b64() -> str:
    """Helper for operators: mint a signing seed to paste into .env."""
    return base64.b64encode(os.urandom(32)).decode()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    key = SigningKey(seed=bytes(range(32)))
    env = sign_commitment(key, "TXN-1", "ab" * 32, 1234567890, "cd" * 32)
    print("key_id:    %s" % env["signing_key_id"])
    print("signature: %s..." % env["signature_b64"][:40])

    pub = key.public_key_b64()
    ok = verify_signature(pub, env["signature_b64"], "TXN-1", "ab" * 32, 1234567890, "cd" * 32)
    bad = verify_signature(pub, env["signature_b64"], "TXN-1", "ff" * 32, 1234567890, "cd" * 32)
    tampered_chain = verify_signature(pub, env["signature_b64"], "TXN-1", "ab" * 32, 1234567890, "00" * 32)
    print("verify(valid):           %s" % ok)
    print("verify(tampered hash):   %s" % bad)
    print("verify(tampered chain):  %s" % tampered_chain)
    print("fresh seed for .env:     %s" % generate_seed_b64())
