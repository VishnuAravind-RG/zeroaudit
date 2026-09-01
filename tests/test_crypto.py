"""
test_crypto.py - Module-LWE commitments, hash chain, Ed25519 attestation

Note the filename. This suite previously lived in "Test crypto.py" - capital T,
embedded space - which the default `test_*.py` discovery pattern does not
match, so none of it ever ran.

    pytest tests/test_crypto.py -v
"""

import base64
import pytest

from prover.crypto.lwe import (
    LWEKeyPair, LWEPublicKey, commit, verify, open_commitment,
    derive_randomness, encode_amount, AMOUNT_BITS, N, K, Q, ETA,
    _poly_mul_numpy, _poly_mul_schoolbook,
)
from prover.crypto.commitment import CommitmentStore, compute_chain_hash, GENESIS_HASH
from prover.crypto.signature import (
    SigningKey, sign_commitment, verify_signature, signing_payload,
)


@pytest.fixture(scope="module")
def keypair():
    return LWEKeyPair(seed=bytes(range(32)))


@pytest.fixture(scope="module")
def master_key():
    return b"zeroaudit-test-master-key-32byte"


class TestRingArithmetic:
    def test_numpy_matches_reference(self):
        """The fast path must be bit-identical to the schoolbook version."""
        import random
        random.seed(42)
        for _ in range(25):
            a = [random.randrange(Q) for _ in range(N)]
            b = [random.randrange(Q) for _ in range(N)]
            assert _poly_mul_numpy(a, b) == _poly_mul_schoolbook(a, b)

    def test_negacyclic_wraparound(self):
        """X^N must reduce to -1, not +1."""
        x = [0] * N
        x[1] = 1                                # the polynomial X
        result = x
        for _ in range(N - 1):                  # raise to X^N
            result = _poly_mul_schoolbook(result, x)
        assert result[0] == (Q - 1) % Q         # -1 mod q
        assert all(c == 0 for c in result[1:])


class TestEncoding:
    def test_full_64_bit_domain(self):
        """Every bit of a 64-bit amount must land in a distinct coefficient."""
        for bit in range(AMOUNT_BITS):
            encoded = encode_amount(1 << bit)
            assert encoded[bit] == Q // 2
            assert sum(1 for c in encoded if c) == 1

    def test_rejects_out_of_domain(self):
        with pytest.raises(ValueError):
            encode_amount(1 << AMOUNT_BITS)
        with pytest.raises(ValueError):
            encode_amount(-1)


class TestLWE:
    def test_keypair_dimensions(self, keypair):
        assert len(keypair.t) == K
        assert all(len(poly) == N for poly in keypair.t)
        assert all(0 <= c < Q for poly in keypair.t for c in poly)

    def test_keypair_deterministic_in_seed(self):
        assert LWEKeyPair(seed=bytes(32)).t == LWEKeyPair(seed=bytes(32)).t

    def test_commitment_shape(self, keypair, master_key):
        c = commit(keypair, 150_000, "TXN-1", master_key)
        assert len(c["binding_hash"]) == 64
        assert c["pii_bytes"] == 0
        assert c["lwe_params"] == {"n": N, "k": K, "q": Q, "eta": ETA,
                                   "amount_bits": AMOUNT_BITS}
        assert base64.b64decode(c["commitment_b64"])

    def test_commitment_deterministic(self, keypair, master_key):
        a = commit(keypair, 150_000, "TXN-1", master_key)
        b = commit(keypair, 150_000, "TXN-1", master_key)
        assert a["binding_hash"] == b["binding_hash"]

    def test_different_amounts_differ(self, keypair, master_key):
        a = commit(keypair, 150_000, "TXN-1", master_key)
        b = commit(keypair, 150_001, "TXN-1", master_key)
        assert a["binding_hash"] != b["binding_hash"]

    def test_different_txn_ids_differ(self, keypair, master_key):
        a = commit(keypair, 150_000, "TXN-1", master_key)
        b = commit(keypair, 150_000, "TXN-2", master_key)
        assert a["binding_hash"] != b["binding_hash"]

    def test_verification_accepts_true_amount(self, keypair, master_key):
        c = commit(keypair, 150_000, "TXN-1", master_key)
        assert verify(keypair, c, 150_000, "TXN-1", master_key)["verified"] is True

    @pytest.mark.parametrize("delta", [1, 1 << 8, 1 << 31, 1 << 32, 1 << 40, 1 << 52])
    def test_binding_holds_across_64_bit_domain(self, keypair, master_key, delta):
        """Regression: the old 32-bit encoding made amount and amount+2^32 collide."""
        c = commit(keypair, 150_000, "TXN-1", master_key)
        assert verify(keypair, c, 150_000 + delta, "TXN-1", master_key)["verified"] is False

    def test_no_collisions_over_random_amounts(self, keypair, master_key):
        import random
        random.seed(7)
        amounts = random.sample(range(1, 1 << 60), 300)
        hashes = {commit(keypair, a, "TXN-FIXED", master_key)["binding_hash"] for a in amounts}
        assert len(hashes) == len(amounts)

    def test_no_pii_in_commitment(self, keypair, master_key):
        c = commit(keypair, 987_654_321, "TXN-PII", master_key)
        blob = str(c)
        assert "987654321" not in blob
        assert c["pii_bytes"] == 0

    def test_verification_trace_terminates(self, keypair, master_key):
        c = commit(keypair, 150_000, "TXN-1", master_key)
        trace = verify(keypair, c, 150_000, "TXN-1", master_key)["trace"]
        assert trace[-1]["step"] == "RESULT"
        assert trace[-1]["status"] == "VERIFIED"


class TestPublicKeyOpening:
    def test_external_open_with_public_key_only(self, keypair, master_key):
        """An auditor holding only the public key can check a disclosure."""
        c = commit(keypair, 4_200_000, "TXN-OPEN", master_key)
        pub = LWEPublicKey.from_b64(keypair.public_key_b64())
        blinding = derive_randomness(master_key, "TXN-OPEN").hex()

        assert open_commitment(pub, c["commitment_b64"], 4_200_000, blinding) is True
        assert open_commitment(pub, c["commitment_b64"], 4_200_001, blinding) is False

    def test_public_key_roundtrip(self, keypair):
        pub = LWEPublicKey.from_b64(keypair.public_key_b64())
        assert pub.t == keypair.t
        assert pub.fingerprint() == keypair.fingerprint()

    def test_public_key_carries_no_secret(self, keypair):
        """The secret vector must not be recoverable from the serialised key."""
        blob = keypair.public_key_bytes()
        secret_bytes = bytes(c % 256 for poly in keypair.s for c in poly)
        assert secret_bytes not in blob

    def test_malformed_public_key_rejected(self):
        with pytest.raises(ValueError):
            LWEPublicKey(b"too-short")


class TestSignature:
    def test_sign_and_verify(self):
        key = SigningKey(seed=bytes(range(32)))
        env = sign_commitment(key, "TXN-1", "ab" * 32, 123, "cd" * 32)
        assert verify_signature(key.public_key_b64(), env["signature_b64"],
                                "TXN-1", "ab" * 32, 123, "cd" * 32) is True

    @pytest.mark.parametrize("field,value", [
        ("txn_id", "TXN-2"),
        ("binding_hash", "ff" * 32),
        ("timestamp_ns", 999),
        ("chain_hash", "00" * 32),
    ])
    def test_any_tampered_field_fails(self, field, value):
        key = SigningKey(seed=bytes(range(32)))
        args = {"txn_id": "TXN-1", "binding_hash": "ab" * 32,
                "timestamp_ns": 123, "chain_hash": "cd" * 32}
        env = sign_commitment(key, **args)
        args[field] = value
        assert verify_signature(key.public_key_b64(), env["signature_b64"], **args) is False

    def test_wrong_key_fails(self):
        alice, eve = SigningKey(), SigningKey()
        env = sign_commitment(alice, "TXN-1", "ab" * 32, 123, "cd" * 32)
        assert verify_signature(eve.public_key_b64(), env["signature_b64"],
                                "TXN-1", "ab" * 32, 123, "cd" * 32) is False

    def test_signing_payload_is_unambiguous(self):
        """Field boundaries must not be forgeable by shifting content across them."""
        assert signing_payload("a", "bc", 1, "d") != signing_payload("ab", "c", 1, "d")

    def test_seed_determines_identity(self):
        assert SigningKey(seed=bytes(32)).key_id() == SigningKey(seed=bytes(32)).key_id()
        assert SigningKey(seed=bytes(32)).key_id() != SigningKey(seed=bytes(range(32))).key_id()


class TestCommitmentStore:
    @pytest.fixture
    def store(self):
        return CommitmentStore(ledger=None)

    def test_add_and_get(self, store):
        rec = store.add("TXN-A", 100_000, "ACC-1", "RTGS", 0.1)
        assert store.get("TXN-A") is rec
        assert rec.account_hash != "ACC-1"          # hashed, never raw
        assert rec.seq == 1

    def test_quarantine_threshold(self, store):
        assert store.add("TXN-H", 1, "ACC", "RTGS", 0.95).status == "QUARANTINED"
        assert store.add("TXN-L", 1, "ACC", "RTGS", 0.10).status == "VERIFIED"

    def test_status_transitions(self, store):
        store.add("TXN-S", 1, "ACC", "RTGS", 0.9)
        assert store.authorize("TXN-S") and store.get("TXN-S").status == "VERIFIED"
        assert store.reject("TXN-S") and store.get("TXN-S").status == "REJECTED"

    def test_export_is_zero_pii(self, store):
        store.add("TXN-E", 777_777, "ACC-SECRET", "RTGS", 0.1)
        for rec in store.audit_export():
            assert rec["pii_bytes"] == 0
            assert "ACC-SECRET" not in str(rec)
            assert "777777" not in str(rec)
            assert not any(k in rec for k in ("amount", "amount_cents", "account_id"))

    def test_export_includes_commitment_for_verification(self, store):
        """A bare hash gives an auditor nothing to verify against."""
        store.add("TXN-C", 1000, "ACC", "RTGS", 0.1)
        assert store.audit_export()[0]["commitment_b64"]

    def test_hot_ring_is_bounded(self):
        small = CommitmentStore(ledger=None, ring_size=10)
        for i in range(50):
            small.add("TXN-%d" % i, 100, "ACC", "RTGS", 0.1)
        assert small.stats()["hot_ring"] == 10
        assert small.stats()["total"] == 50

    def test_public_keys_expose_no_secrets(self, store):
        keys = store.public_keys()
        blob = str(keys).lower()
        assert "lwe_public_key_b64" in keys and "ed25519_public_key_b64" in keys
        assert "private" not in blob and "master" not in blob

    def test_opening_roundtrip(self, store):
        store.add("TXN-O", 555_000, "ACC", "RTGS", 0.1)
        op = store.opening_for("TXN-O", 555_000)
        pub = LWEPublicKey.from_b64(op["lwe_public_key_b64"])
        assert open_commitment(pub, op["commitment_b64"], 555_000, op["blinding_seed_hex"])


class TestHashChain:
    @pytest.fixture
    def store(self):
        s = CommitmentStore(ledger=None)
        for i in range(25):
            s.add("TXN-%03d" % i, 1000 + i, "ACC-%d" % (i % 4), "RTGS", 0.1)
        return s

    def test_chain_is_intact(self, store):
        result = store.verify_chain()
        assert result["intact"] is True
        assert result["checked"] == 25

    def test_genesis_link(self):
        s = CommitmentStore(ledger=None)
        assert s.add("TXN-0", 1, "ACC", "RTGS").prev_chain_hash == GENESIS_HASH

    def test_links_are_sequential(self, store):
        records = sorted(store.audit_export(), key=lambda r: r["seq"])
        for prev, cur in zip(records, records[1:]):
            assert cur["prev_chain_hash"] == prev["chain_hash"]
            assert cur["seq"] == prev["seq"] + 1

    def test_edit_breaks_the_chain(self, store):
        records = sorted(store.audit_export(), key=lambda r: r["seq"])
        victim = records[10]
        forged = compute_chain_hash(victim["prev_chain_hash"], "de" + "ad" * 31,
                                    victim["txn_id"], victim["timestamp_ns"])
        assert forged != victim["chain_hash"]

    def test_deletion_breaks_the_chain(self, store):
        records = [r for r in sorted(store.audit_export(), key=lambda r: r["seq"])
                   if r["seq"] != 12]
        breaks = [r["seq"] for prev, r in zip(records, records[1:])
                  if r["prev_chain_hash"] != prev["chain_hash"]]
        assert breaks
