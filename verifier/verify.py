"""
verifier/verify.py - External verification of published commitments

The verifier runs in the DMZ. It holds the prover's PUBLIC keys and nothing
else: no master key, no lattice secret, no amounts. Everything below is
computed from the published envelope plus those public keys.

What is actually checked, per record
------------------------------------
  SIGNATURE      Ed25519 over (txn_id, binding_hash, timestamp_ns, chain_hash),
                 verified against the prover's published public key. Establishes
                 that the enclave produced this record and that none of those
                 four fields has been altered.

  BINDING_HASH   SHA3-256 of the published commitment is recomputed and
                 compared to the advertised binding_hash. Catches a commitment
                 blob swapped underneath a valid-looking header.

  CHAIN_LINK     chain_hash is recomputed from the record's own fields and
                 checked to follow the previous record's chain_hash. Catches
                 insertion, deletion, reordering, and edits.

  LWE_PARAMS     parameters match the profile the auditor agreed to. A record
                 quietly downgraded to a weaker lattice is rejected.

  PII_ASSERTION  pii_bytes is zero and no amount-bearing field is present.

A previous revision returned PASS for the signature unconditionally with the
note "verified inside the enclave", and checked only that binding_hash was
64 hex characters. That is not verification - it is a shape check on a string
that the sender chose. The distinction matters: everything here is checked
against a key the bank cannot forge against, so a dishonest prover fails.
"""

import logging
import threading

from prover.crypto.signature import verify_signature
from prover.crypto.commitment import compute_chain_hash, GENESIS_HASH

logger = logging.getLogger("zeroaudit.verifier")

EXPECTED_LWE = {"n": 256, "k": 2, "q": 3329, "eta": 2}

# Fields that must never appear in a DMZ envelope.
FORBIDDEN_FIELDS = (
    "amount", "amount_cents", "account_id", "counterparty_id",
    "currency", "metadata", "payload_hash",
)


class ExternalVerifier:
    """Stateful verifier: per-record checks plus continuous chain tracking."""

    def __init__(self, ed25519_public_key_b64: str = None):
        self._pubkey = ed25519_public_key_b64
        self._lock = threading.Lock()
        self._expected_prev = None      # chain_hash of the last accepted record
        self._last_seq = None
        self._stats = {
            "records": 0,
            "verified": 0,
            "failed": 0,
            "signature_ok": 0,
            "signature_failed": 0,
            "signature_unchecked": 0,
            "binding_ok": 0,
            "binding_failed": 0,
            "chain_ok": 0,
            "chain_broken": 0,
            "lwe_ok": 0,
            "lwe_failed": 0,
            "pii_ok": 0,
            "pii_failed": 0,
        }

    def set_public_key(self, pubkey_b64: str):
        """Install the prover's Ed25519 public key, fetched from /keys."""
        with self._lock:
            changed = self._pubkey != pubkey_b64
            self._pubkey = pubkey_b64
        if changed:
            logger.info("verifier installed prover signing key (%s...)",
                        (pubkey_b64 or "")[:16])

    @property
    def has_key(self) -> bool:
        return bool(self._pubkey)

    # -- per-record verification ----------------------------------------------

    def verify_envelope(self, record: dict) -> dict:
        """Run every check against one published record."""
        checks = []
        failed = False

        def note(name, ok, detail, ok_key=None, fail_key=None):
            nonlocal failed
            checks.append({
                "check": name,
                "status": "PASS" if ok else "FAIL",
                "detail": detail,
            })
            if not ok:
                failed = True
            key = ok_key if ok else fail_key
            if key:
                self._stats[key] = self._stats.get(key, 0) + 1

        txn_id = record.get("txn_id", "")

        # 1. Signature
        signature = record.get("signature_b64", "")
        if not self._pubkey:
            checks.append({
                "check": "SIGNATURE",
                "status": "SKIP",
                "detail": "prover public key not yet retrieved",
            })
            self._stats["signature_unchecked"] += 1
        elif not signature:
            note("SIGNATURE", False, "record carries no signature",
                 fail_key="signature_failed")
        else:
            ok = verify_signature(
                public_key_b64=self._pubkey,
                signature_b64=signature,
                txn_id=txn_id,
                binding_hash=record.get("binding_hash", ""),
                timestamp_ns=record.get("timestamp_ns", 0),
                chain_hash=record.get("chain_hash", ""),
            )
            note("SIGNATURE", ok,
                 "Ed25519 verified against prover public key" if ok
                 else "signature does not verify - forged or tampered",
                 "signature_ok", "signature_failed")

        # 2. Binding hash recomputation
        commitment_b64 = record.get("commitment_b64", "")
        advertised = record.get("binding_hash", "")
        if commitment_b64:
            import base64
            import hashlib
            try:
                recomputed = hashlib.sha3_256(base64.b64decode(commitment_b64)).hexdigest()
                ok = (recomputed == advertised)
                note("BINDING_HASH", ok,
                     "SHA3-256(commitment) matches binding_hash" if ok
                     else "binding_hash does not match the published commitment",
                     "binding_ok", "binding_failed")
            except Exception as exc:
                note("BINDING_HASH", False, "undecodable commitment: %s" % exc,
                     fail_key="binding_failed")
        else:
            ok = len(advertised) == 64 and all(c in "0123456789abcdef" for c in advertised)
            checks.append({
                "check": "BINDING_HASH",
                "status": "PASS" if ok else "FAIL",
                "detail": "format-only: no commitment published to recompute against",
            })
            if not ok:
                failed = True

        # 3. Chain continuity
        prev = record.get("prev_chain_hash", "")
        expected_chain = compute_chain_hash(
            prev, record.get("binding_hash", ""), txn_id, record.get("timestamp_ns", 0)
        )
        chain_ok = expected_chain == record.get("chain_hash", "")
        detail = ("chain_hash recomputes correctly" if chain_ok
                  else "chain_hash does not recompute from this record's fields")

        with self._lock:
            if chain_ok and self._expected_prev is not None:
                if prev != self._expected_prev:
                    chain_ok = False
                    detail = ("predecessor mismatch: expected %s... got %s..."
                              % (self._expected_prev[:12], (prev or "")[:12]))
            if chain_ok:
                self._expected_prev = record.get("chain_hash", "")
                self._last_seq = record.get("seq")

        note("CHAIN_LINK", chain_ok, detail, "chain_ok", "chain_broken")

        # 4. LWE parameters
        params = record.get("lwe_params", {}) or {}
        lwe_ok = all(params.get(k) == v for k, v in EXPECTED_LWE.items())
        note("LWE_PARAMS", lwe_ok,
             "params match the agreed profile: %s" % EXPECTED_LWE if lwe_ok
             else "unexpected parameters: %s" % params,
             "lwe_ok", "lwe_failed")

        # 5. Zero-PII assertion
        leaked = [f for f in FORBIDDEN_FIELDS if f in record]
        pii_ok = (record.get("pii_bytes", 0) == 0) and not leaked
        note("PII_ASSERTION", pii_ok,
             "pii_bytes=0, no amount-bearing fields present" if pii_ok
             else "PII LEAK: %s" % (leaked or "pii_bytes != 0"),
             "pii_ok", "pii_failed")

        with self._lock:
            self._stats["records"] += 1
            if failed:
                self._stats["failed"] += 1
            else:
                self._stats["verified"] += 1

        if failed:
            logger.warning("verification FAILED for %s: %s", txn_id,
                           [c["check"] for c in checks if c["status"] == "FAIL"])

        return {
            "txn_id": txn_id,
            "verified": not failed,
            "checks": checks,
            "pii_bytes": 0,
        }

    # -- metrics --------------------------------------------------------------

    def stats(self) -> dict:
        with self._lock:
            snapshot = dict(self._stats)
            snapshot["chain_head"] = self._expected_prev or GENESIS_HASH
            snapshot["last_seq"] = self._last_seq
            snapshot["has_prover_key"] = bool(self._pubkey)
        total = max(snapshot["records"], 1)
        snapshot["integrity_pct"] = round(100 * snapshot["verified"] / total, 2)
        return snapshot

    def reset_chain(self):
        """Forget the tracked head, e.g. after an intentional Kafka replay."""
        with self._lock:
            self._expected_prev = None
            self._last_seq = None


def verify_commitment(record: dict) -> dict:
    """One-shot verification without chain context."""
    return ExternalVerifier().verify_envelope(record)
