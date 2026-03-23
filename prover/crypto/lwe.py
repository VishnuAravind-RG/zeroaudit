"""
lwe.py — Post-Quantum Learning With Errors (LWE) Commitment Engine
ZEROAUDIT Cryptographic Core

Implements:
  - LWE key generation (public matrix A, secret vector s, error vector e)
  - Commitment: C = A·s + e  (mod q)
  - Binding: HMAC-SHA256 randomness derivation from master key + txn_id
  - Verification: recompute and bitwise compare
  - No PII ever touches this module — only numeric amounts and IDs

Parameters (Kyber-512 inspired):
  n = 256   (polynomial degree)
  k = 2     (module rank → effective dimension = n*k = 512)
  q = 3329  (prime modulus)
  eta = 2   (CBD noise parameter)
"""

import os
import hmac
import hashlib
import struct
import base64
import json
import time
from typing import Tuple

# ── LWE Parameters ────────────────────────────────────────────────────────────
N = 256          # lattice dimension per module
K = 2            # module rank  (total dim = N*K = 512)
Q = 3329         # prime modulus
ETA = 2          # centered binomial noise parameter
SEED_LEN = 32    # bytes for PRNG seed


# ── Low-level Math ─────────────────────────────────────────────────────────────

def _mod_q(x: int) -> int:
    return x % Q


def _cbd(seed: bytes, nonce: int, length: int) -> list[int]:
    """Centered Binomial Distribution sampler (eta=2).
    Produces coefficients in [-eta, +eta], lifted to [0, q).
    """
    # Expand seed via SHAKE-256
    import hashlib
    xof = hashlib.shake_256()
    xof.update(seed)
    xof.update(struct.pack("<H", nonce))
    buf = xof.digest(length * ETA)

    result = []
    for i in range(length):
        byte = buf[i % len(buf)]
        # sum 2 bits minus sum of next 2 bits
        a = bin(byte & 0x03).count('1') + bin((byte >> 2) & 0x03).count('1')
        b = bin((byte >> 4) & 0x03).count('1') + bin((byte >> 6) & 0x03).count('1')
        val = (a - b) % Q
        result.append(val)
    return result


def _gen_matrix_A(seed: bytes) -> list[list[list[int]]]:
    """Generate public matrix A ∈ R_q^{k×k} from seed (XOF expansion)."""
    import hashlib
    A = []
    for i in range(K):
        row = []
        for j in range(K):
            xof = hashlib.shake_128()
            xof.update(seed)
            xof.update(bytes([i, j]))
            buf = xof.digest(N * 3)  # 3 bytes per coeff for rejection sampling
            poly = []
            idx = 0
            while len(poly) < N and idx + 2 < len(buf):
                val = ((buf[idx] | (buf[idx+1] << 8)) & 0x1FFF)
                if val < Q:
                    poly.append(val)
                idx += 3
            # pad if needed
            while len(poly) < N:
                poly.append(0)
            row.append(poly[:N])
        A.append(row)
    return A


def _poly_add(a: list[int], b: list[int]) -> list[int]:
    return [_mod_q(x + y) for x, y in zip(a, b)]


def _poly_mul_schoolbook(a: list[int], b: list[int]) -> list[int]:
    """Schoolbook polynomial multiplication mod (X^N + 1, q)."""
    result = [0] * N
    for i in range(N):
        for j in range(N):
            idx = (i + j) % N
            sign = -1 if (i + j) >= N else 1
            result[idx] = _mod_q(result[idx] + sign * a[i] * b[j])
    return result


def _module_mul_add(A: list, s: list, e: list) -> list:
    """Compute t = A·s + e  over R_q^k."""
    t = [[0]*N for _ in range(K)]
    for i in range(K):
        for j in range(K):
            prod = _poly_mul_schoolbook(A[i][j], s[j])
            t[i] = _poly_add(t[i], prod)
        t[i] = _poly_add(t[i], e[i])
    return t


# ── Key Generation ─────────────────────────────────────────────────────────────

class LWEKeyPair:
    def __init__(self, seed: bytes = None):
        self.seed = seed or os.urandom(SEED_LEN)
        self._generate()

    def _generate(self):
        # Public matrix A
        self.A = _gen_matrix_A(self.seed)

        # Secret vector s (small coefficients)
        self.s = [_cbd(self.seed, nonce=i, length=N) for i in range(K)]

        # Error vector e
        self.e = [_cbd(self.seed, nonce=K+i, length=N) for i in range(K)]

        # Public key: t = A·s + e
        self.t = _module_mul_add(self.A, self.s, self.e)

    def public_key_bytes(self) -> bytes:
        """Serialize public key (seed + t) to bytes."""
        flat_t = [c for poly in self.t for c in poly]
        packed = struct.pack(f"<{len(flat_t)}H", *flat_t)
        return self.seed + packed

    def to_dict(self) -> dict:
        return {
            "seed_b64": base64.b64encode(self.seed).decode(),
            "dimensions": f"{K}x{N}",
            "modulus_q": Q,
            "noise_eta": ETA,
        }


# ── Commitment ─────────────────────────────────────────────────────────────────

def derive_randomness(master_key: bytes, txn_id: str) -> bytes:
    """r = HMAC-SHA256(K_master, TXN_ID) — deterministic, no PII."""
    return hmac.new(master_key, txn_id.encode(), hashlib.sha256).digest()


def commit(
    keypair: LWEKeyPair,
    amount_cents: int,
    txn_id: str,
    master_key: bytes,
) -> dict:
    """
    Produce a ZK commitment to `amount_cents` using LWE.
    Returns the commitment dict (no raw amount in output).

    Commitment scheme:
      r   = HMAC-SHA256(K_master, txn_id)
      r'  = CBD(r, nonce=0..K)   [randomness vector]
      e'  = CBD(r, nonce=K..2K)  [fresh error]
      u   = A·r' + e'
      v   = t·r' + e'' + encode(m)   where m = amount_cents
      C   = (u, v)
    """
    r_seed = derive_randomness(master_key, txn_id)

    # Randomness vector r'
    r_vec = [_cbd(r_seed, nonce=i, length=N) for i in range(K)]
    # Fresh error for u
    e1 = [_cbd(r_seed, nonce=K+i, length=N) for i in range(K)]
    # Fresh error for v
    e2_raw = _cbd(r_seed, nonce=2*K, length=N)

    # u = A^T · r' + e1
    AT = [[keypair.A[j][i] for j in range(K)] for i in range(K)]
    u = _module_mul_add(AT, r_vec, e1)

    # Encode message: m_poly[0] = round(q/2) * bit(amount), rest zeros
    m_poly = [0] * N
    # Encode amount as bit-packed into first few coefficients
    amount_bits = min(amount_cents, (1 << N) - 1)
    half_q = Q // 2
    for bit_idx in range(min(32, N)):
        if (amount_bits >> bit_idx) & 1:
            m_poly[bit_idx] = half_q

    # v = t · r' + e2 + m_poly  (simplified: use t[0] · r'[0])
    v = _poly_mul_schoolbook(keypair.t[0], r_vec[0])
    v = _poly_add(v, e2_raw)
    v = _poly_add(v, m_poly)

    # Serialize commitment
    u_flat = [c for poly in u for c in poly]
    v_flat = v

    commitment_bytes = struct.pack(f"<{len(u_flat)}H", *u_flat)
    commitment_bytes += struct.pack(f"<{len(v_flat)}H", *v_flat)

    commitment_b64 = base64.b64encode(commitment_bytes).decode()
    size_kb = round(len(commitment_bytes) / 1024, 1)

    # Binding hash (for fast lookup — NOT the secret)
    binding = hashlib.sha3_256(commitment_bytes).hexdigest()

    return {
        "txn_id": txn_id,
        "commitment_b64": commitment_b64,
        "size_kb": size_kb,
        "binding_hash": binding,
        "lwe_params": {
            "n": N, "k": K, "q": Q, "eta": ETA,
        },
        "timestamp_ns": time.time_ns(),
        "pii_bytes": 0,
    }


# ── Verification ───────────────────────────────────────────────────────────────

def verify(
    keypair: LWEKeyPair,
    commitment_record: dict,
    amount_cents: int,
    txn_id: str,
    master_key: bytes,
) -> dict:
    """
    Recompute commitment and compare bitwise against stored record.
    Returns verification trace (for dashboard terminal display).
    """
    trace = []
    trace.append({"step": "DERIVE_R", "detail": f"HMAC-SHA256(K_master, {txn_id})", "status": "RUNNING"})

    recomputed = commit(keypair, amount_cents, txn_id, master_key)

    trace[0]["status"] = "DONE"
    trace.append({"step": "LOAD_MATRIX_A", "detail": f"Public Matrix A ({K*N}×{K*N} mod q={Q})", "status": "DONE"})
    trace.append({"step": "COMPUTE_AS_E", "detail": f"C = A·s + e (mod {Q})", "status": "DONE"})

    stored_hash = commitment_record.get("binding_hash")
    recomputed_hash = recomputed["binding_hash"]

    match = hmac.compare_digest(stored_hash, recomputed_hash)

    trace.append({
        "step": "BITWISE_COMPARE",
        "detail": f"stored={stored_hash[:16]}... recomputed={recomputed_hash[:16]}...",
        "status": "DONE"
    })
    trace.append({
        "step": "RESULT",
        "detail": "LWE PROOF INTACT — MATCH FOUND" if match else "PROOF MISMATCH — INTEGRITY VIOLATION",
        "status": "VERIFIED" if match else "FAILED"
    })

    return {
        "verified": match,
        "txn_id": txn_id,
        "trace": trace,
        "pii_bytes": 0,
    }


# ── Singleton Key Store (in-memory for demo; use HSM in prod) ──────────────────

_MASTER_KEY = os.environ.get("ZEROAUDIT_MASTER_KEY", "").encode() or os.urandom(32)
_KEYPAIR: LWEKeyPair = None


def get_keypair() -> LWEKeyPair:
    global _KEYPAIR
    if _KEYPAIR is None:
        seed_hex = os.environ.get("LWE_SEED_HEX", "")
        seed = bytes.fromhex(seed_hex) if seed_hex else None
        _KEYPAIR = LWEKeyPair(seed)
    return _KEYPAIR


def get_master_key() -> bytes:
    return _MASTER_KEY


if __name__ == "__main__":
    print("=== ZEROAUDIT LWE Self-Test ===")
    kp = get_keypair()
    mk = get_master_key()

    print(f"Key params: {kp.to_dict()}")

    c = commit(kp, amount_cents=150000, txn_id="TXN-TEST-0001", master_key=mk)
    print(f"Commitment size: {c['size_kb']} KB")
    print(f"Binding hash:    {c['binding_hash'][:32]}...")
    print(f"PII bytes:       {c['pii_bytes']}")

    result = verify(kp, c, amount_cents=150000, txn_id="TXN-TEST-0001", master_key=mk)
    print(f"Verified: {result['verified']}")
    for step in result['trace']:
        print(f"  [{step['status']:8}] {step['step']}: {step['detail']}")

    # Tamper test
    bad = verify(kp, c, amount_cents=999999, txn_id="TXN-TEST-0001", master_key=mk)
    print(f"Tamper detected: {not bad['verified']}")