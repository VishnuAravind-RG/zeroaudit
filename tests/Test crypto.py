"""
test_crypto.py — ZEROAUDIT Cryptographic Unit Tests
Run: pytest tests/test_crypto.py -v
"""

import pytest
import time
from prover.crypto.lwe import LWEKeyPair, commit, verify, derive_randomness, Q, N, K
from prover.crypto.commitment import CommitmentStore
from prover.crypto.signature import SigningKey, sign_commitment, verify_signature


# ── LWE Tests ─────────────────────────────────────────────────────────────────

class TestLWE:
    def setup_method(self):
        import os
        self.keypair = LWEKeyPair(seed=bytes(32))  # deterministic for tests
        self.master_key = os.urandom(32)

    def test_keypair_dimensions(self):
        assert len(self.keypair.A) == K
        assert len(self.keypair.A[0]) == K
        assert len(self.keypair.A[0][0]) == N
        assert len(self.keypair.s) == K
        assert len(self.keypair.t) == K

    def test_commitment_produces_output(self):
        c = commit(self.keypair, 150000, "TXN-001", self.master_key)
        assert c["txn_id"] == "TXN-001"
        assert len(c["commitment_b64"]) > 100
        assert c["pii_bytes"] == 0
        assert c["size_kb"] > 0

    def test_commitment_deterministic(self):
        c1 = commit(self.keypair, 150000, "TXN-001", self.master_key)
        c2 = commit(self.keypair, 150000, "TXN-001", self.master_key)
        assert c1["binding_hash"] == c2["binding_hash"]

    def test_different_amounts_different_commitments(self):
        c1 = commit(self.keypair, 100000, "TXN-002", self.master_key)
        c2 = commit(self.keypair, 200000, "TXN-002", self.master_key)
        assert c1["binding_hash"] != c2["binding_hash"]

    def test_verification_correct_amount(self):
        c = commit(self.keypair, 500000, "TXN-003", self.master_key)
        result = verify(self.keypair, c, 500000, "TXN-003", self.master_key)
        assert result["verified"] is True
        assert result["pii_bytes"] == 0

    def test_verification_tampered_amount(self):
        c = commit(self.keypair, 500000, "TXN-004", self.master_key)
        result = verify(self.keypair, c, 999999, "TXN-004", self.master_key)
        assert result["verified"] is False

    def test_verification_trace_steps(self):
        c = commit(self.keypair, 100, "TXN-005", self.master_key)
        result = verify(self.keypair, c, 100, "TXN-005", self.master_key)
        step_names = [s["step"] for s in result["trace"]]
        assert "DERIVE_R" in step_names
        assert "RESULT" in step_names

    def test_no_pii_in_commitment(self):
        c = commit(self.keypair, 150000, "TXN-006", self.master_key)
        assert c["pii_bytes"] == 0
        # Ensure raw amount not in output
        c_str = str(c)
        assert "150000" not in c_str or "amount" not in c_str


# ── Commitment Store Tests ────────────────────────────────────────────────────

class TestCommitmentStore:
    def setup_method(self):
        self.store = CommitmentStore()

    def test_add_and_get(self):
        r = self.store.add("TXN-S001", 75000, "ACC-1234", "RTGS", 0.1)
        assert r.txn_id == "TXN-S001"
        assert r.pii_bytes == 0
        assert r.account_hash != "ACC-1234"  # must be hashed

        fetched = self.store.get("TXN-S001")
        assert fetched is not None
        assert fetched.txn_id == "TXN-S001"

    def test_anomaly_score_quarantine(self):
        r = self.store.add("TXN-S002", 10000, "ACC-5678", "WIRE_TRANSFER", 0.9)
        assert r.status == "QUARANTINED"

    def test_normal_score_verified(self):
        r = self.store.add("TXN-S003", 5000, "ACC-9999", "NEFT", 0.2)
        assert r.status == "VERIFIED"

    def test_authorize(self):
        self.store.add("TXN-S004", 5000, "ACC-1111", "NEFT", 0.9)
        self.store.authorize("TXN-S004")
        assert self.store.get("TXN-S004").status == "VERIFIED"

    def test_reject(self):
        self.store.add("TXN-S005", 5000, "ACC-2222", "RTGS", 0.9)
        self.store.reject("TXN-S005")
        assert self.store.get("TXN-S005").status == "REJECTED"

    def test_audit_export_zero_pii(self):
        self.store.add("TXN-S006", 99999, "ACC-3333", "FX_CONVERSION", 0.05)
        export = self.store.audit_export()
        for record in export:
            assert record["pii_bytes"] == 0
            assert "amount" not in record  # no raw amount

    def test_stats(self):
        self.store.add("TXN-STAT1", 1000, "ACC-A", "NEFT", 0.1)
        self.store.add("TXN-STAT2", 2000, "ACC-B", "NEFT", 0.9)
        s = self.store.stats()
        assert s["total"] >= 2
        assert "chain_integrity_pct" in s


# ── Signature Tests ───────────────────────────────────────────────────────────

class TestSignature:
    def setup_method(self):
        self.key = SigningKey()

    def test_sign_and_verify(self):
        env = sign_commitment(self.key, "TXN-SIG001", "a" * 64)
        result = verify_signature(env)
        assert result["valid"] is True

    def test_tampered_binding_fails(self):
        env = sign_commitment(self.key, "TXN-SIG002", "b" * 64)
        env["binding_hash"] = "c" * 64  # tamper
        result = verify_signature(env)
        assert result["valid"] is False

    def test_tampered_txn_id_fails(self):
        env = sign_commitment(self.key, "TXN-SIG003", "d" * 64)
        env["txn_id"] = "TXN-FAKE"
        result = verify_signature(env)
        assert result["valid"] is False

    def test_no_pii_in_envelope(self):
        env = sign_commitment(self.key, "TXN-SIG004", "e" * 64)
        assert "amount" not in str(env)
        assert "account" not in str(env)

    def test_different_keys_fail(self):
        key2 = SigningKey()
        env = sign_commitment(self.key, "TXN-SIG005", "f" * 64)
        # Swap public key to key2's — should fail
        env["public_key_b64"] = key2.public_key_b64()
        result = verify_signature(env)
        assert result["valid"] is False