"""
lwe.py - Post-Quantum Module-LWE Commitment Engine
ZEROAUDIT Cryptographic Core

Implements a binding + hiding commitment scheme over the ring
R_q = Z_q[X]/(X^N + 1), under the Module-LWE hardness assumption
(Kyber-style parameter profile).

Scheme
------
KeyGen:
    A  = expand(seed)               A in R_q^{k x k}
    s  = CBD(eta)                   secret,  s in R_q^k
    e  = CBD(eta)                   error,   e in R_q^k
    t  = A.s + e                    public,  t in R_q^k
    public key = (seed, t)

Commit(m, txn_id):
    r    = CBD(HMAC(K_master, txn_id))      blinding vector
    e1   = CBD(...)                          error for u
    e2   = CBD(...)                          error for v
    u    = A^T.r + e1
    v    = <t, r> + e2 + encode(m)
    C    = (u, v)
    binding_hash = SHA3-256(C)

Open(C, m, txn_id):  recompute C and compare in constant time.

Security properties
-------------------
  binding  - encode() injects the FULL 64-bit amount, one bit per
             coefficient scaled by floor(q/2). Two distinct amounts
             therefore differ in at least one coefficient by ~q/2,
             which no CBD(eta=2) error term can bridge.
  hiding   - (u, v) is pseudorandom under Module-LWE; the blinding vector
             and error terms derive from a secret master key and are
             discarded after use.
  pq       - reduces to Module-LWE, believed hard for quantum adversaries.

Parameters (Kyber-512 profile):
    n = 256   polynomial degree
    k = 2     module rank  (effective lattice dimension n*k = 512)
    q = 3329  prime modulus
    eta = 2   centered binomial noise parameter

Performance
-----------
Ring multiplication uses a NumPy negacyclic convolution when NumPy is
available and falls back to a pure-Python schoolbook loop otherwise.
Both paths are bit-identical; the fallback exists so the crypto core
carries no hard third-party dependency.

No PII ever enters this module - only integer amounts and opaque IDs.
"""

import os
import hmac
import time
import base64
import struct
import hashlib
import logging
from typing import Optional

logger = logging.getLogger("zeroaudit.lwe")

try:
    import numpy as _np
    _NUMPY = True
except ImportError:  # pragma: no cover - only on minimal installs
    _NUMPY = False
    logger.warning("NumPy unavailable - falling back to pure-Python ring arithmetic")

# -- LWE parameters -----------------------------------------------------------
N = 256          # polynomial degree
K = 2            # module rank  (total lattice dimension = N*K = 512)
Q = 3329         # prime modulus
ETA = 2          # centered binomial noise parameter
SEED_LEN = 32    # bytes of public-matrix seed

AMOUNT_BITS = 64  # full width of the committed amount (was 32 - see CHANGELOG)

if AMOUNT_BITS > N:  # pragma: no cover - guards a future parameter change
    raise ValueError("AMOUNT_BITS exceeds ring degree N")


# -- Ring arithmetic ----------------------------------------------------------

def _poly_add(a: list, b: list) -> list:
    return [(x + y) % Q for x, y in zip(a, b)]


def _poly_mul_schoolbook(a: list, b: list) -> list:
    """Negacyclic multiply in Z_q[X]/(X^N + 1). Reference implementation."""
    result = [0] * N
    for i in range(N):
        ai = a[i]
        if ai == 0:
            continue
        for j in range(N):
            idx = i + j
            if idx >= N:
                result[idx - N] = (result[idx - N] - ai * b[j]) % Q
            else:
                result[idx] = (result[idx] + ai * b[j]) % Q
    return result


def _poly_mul_numpy(a: list, b: list) -> list:
    """Negacyclic multiply via full convolution + fold. Identical output.

    For f, g of degree < N the product mod (X^N + 1) folds the high half
    back with a sign flip, because X^N = -1 in the ring:
        c = conv(f, g)                     length 2N-1
        result[i] = c[i] - c[i + N]
    Coefficients stay well inside int64: N * (q-1)^2 is about 2.8e9.
    """
    conv = _np.convolve(
        _np.asarray(a, dtype=_np.int64),
        _np.asarray(b, dtype=_np.int64),
    )
    folded = conv[:N].copy()
    folded[: N - 1] -= conv[N:]
    return (folded % Q).tolist()


def _poly_mul(a: list, b: list) -> list:
    return _poly_mul_numpy(a, b) if _NUMPY else _poly_mul_schoolbook(a, b)


def _module_mul_add(A: list, s: list, e: list) -> list:
    """t = A.s + e  over R_q^k."""
    out = []
    for i in range(K):
        acc = [0] * N
        for j in range(K):
            acc = _poly_add(acc, _poly_mul(A[i][j], s[j]))
        out.append(_poly_add(acc, e[i]))
    return out


def _inner_product(t: list, r: list) -> list:
    """<t, r> = sum_i t[i] * r[i]  over R_q."""
    acc = [0] * N
    for i in range(K):
        acc = _poly_add(acc, _poly_mul(t[i], r[i]))
    return acc


# -- Samplers -----------------------------------------------------------------

def _cbd(seed: bytes, nonce: int, length: int) -> list:
    """Centered binomial sampler, eta=2.

    Consumes 4 fresh bits per coefficient: b = popcount(lo2) - popcount(hi2),
    giving b in [-2, 2] with binomial weights (1,4,6,4,1)/16, lifted to [0, q).
    """
    xof = hashlib.shake_256()
    xof.update(seed)
    xof.update(struct.pack("<H", nonce & 0xFFFF))
    buf = xof.digest((length + 1) // 2)          # 2 coefficients per byte

    out = []
    for i in range(length):
        byte = buf[i >> 1]
        nib = (byte & 0x0F) if (i & 1) == 0 else (byte >> 4)
        a = (nib & 1) + ((nib >> 1) & 1)
        b = ((nib >> 2) & 1) + ((nib >> 3) & 1)
        out.append((a - b) % Q)
    return out


def _gen_matrix_A(seed: bytes) -> list:
    """Expand public matrix A in R_q^{k x k} from a seed by rejection sampling."""
    A = []
    for i in range(K):
        row = []
        for j in range(K):
            xof = hashlib.shake_128()
            xof.update(seed)
            xof.update(bytes([i, j]))
            buf = xof.digest(N * 3 + 64)         # 12 bits per candidate, oversampled
            poly, idx = [], 0
            while len(poly) < N and idx + 1 < len(buf):
                val = (buf[idx] | (buf[idx + 1] << 8)) & 0x0FFF
                if val < Q:
                    poly.append(val)
                idx += 2
            while len(poly) < N:                 # pragma: no cover - vanishingly rare
                poly.append(0)
            row.append(poly[:N])
        A.append(row)
    return A


# -- Key material -------------------------------------------------------------

class LWEKeyPair:
    """Module-LWE keypair. Deterministic in `seed` - same seed, same key."""

    def __init__(self, seed: bytes = None):
        self.seed = seed or os.urandom(SEED_LEN)
        self._generate()

    def _generate(self):
        self.A = _gen_matrix_A(self.seed)
        self.s = [_cbd(self.seed, nonce=i, length=N) for i in range(K)]
        self.e = [_cbd(self.seed, nonce=K + i, length=N) for i in range(K)]
        self.t = _module_mul_add(self.A, self.s, self.e)

    def public_key_bytes(self) -> bytes:
        """Public key = seed || t. Safe to publish; reveals nothing about s."""
        flat_t = [c for poly in self.t for c in poly]
        return self.seed + struct.pack("<%dH" % len(flat_t), *flat_t)

    def public_key_b64(self) -> str:
        return base64.b64encode(self.public_key_bytes()).decode()

    def fingerprint(self) -> str:
        """Short stable identifier for the public key - goes on every record."""
        return hashlib.sha3_256(self.public_key_bytes()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "seed_b64": base64.b64encode(self.seed).decode(),
            "dimensions": "%dx%d" % (K, N),
            "modulus_q": Q,
            "noise_eta": ETA,
            "fingerprint": self.fingerprint(),
        }


class LWEPublicKey:
    """Verifier-side view of a keypair: A and t only, never s.

    Carries exactly what an external auditor needs to re-derive a commitment
    from a disclosed opening, and nothing more.
    """

    def __init__(self, blob: bytes):
        if len(blob) != SEED_LEN + K * N * 2:
            raise ValueError("malformed public key: %d bytes" % len(blob))
        self.seed = blob[:SEED_LEN]
        flat = struct.unpack("<%dH" % (K * N), blob[SEED_LEN:])
        self.t = [list(flat[i * N:(i + 1) * N]) for i in range(K)]
        self.A = _gen_matrix_A(self.seed)

    @classmethod
    def from_b64(cls, b64: str) -> "LWEPublicKey":
        return cls(base64.b64decode(b64))

    def fingerprint(self) -> str:
        flat_t = [c for poly in self.t for c in poly]
        blob = self.seed + struct.pack("<%dH" % len(flat_t), *flat_t)
        return hashlib.sha3_256(blob).hexdigest()[:16]


# -- Message encoding ---------------------------------------------------------

def encode_amount(amount_cents: int) -> list:
    """Encode a 64-bit amount as one bit per coefficient, scaled by floor(q/2).

    Distinct amounts differ in at least one coefficient by floor(q/2) = 1664,
    three orders of magnitude beyond the reach of a CBD(eta=2) error term
    (max |e| = 2). This is what makes the commitment binding across the full
    64-bit domain.
    """
    if amount_cents < 0:
        raise ValueError("amount_cents must be non-negative")
    if amount_cents >= (1 << AMOUNT_BITS):
        raise ValueError("amount_cents exceeds the %d-bit commitment domain" % AMOUNT_BITS)

    m = [0] * N
    half_q = Q // 2
    for bit in range(AMOUNT_BITS):
        if (amount_cents >> bit) & 1:
            m[bit] = half_q
    return m


# -- Commitment ---------------------------------------------------------------

def derive_randomness(master_key: bytes, txn_id: str) -> bytes:
    """Blinding seed r = HMAC-SHA256(K_master, txn_id). Deterministic, no PII."""
    return hmac.new(master_key, txn_id.encode(), hashlib.sha256).digest()


def _commit_core(A: list, t: list, amount_cents: int, r_seed: bytes) -> bytes:
    """Shared commitment kernel, used by both the prover and the verifier."""
    r_vec = [_cbd(r_seed, nonce=i, length=N) for i in range(K)]
    e1 = [_cbd(r_seed, nonce=K + i, length=N) for i in range(K)]
    e2 = _cbd(r_seed, nonce=2 * K, length=N)

    # u = A^T . r + e1
    AT = [[A[j][i] for j in range(K)] for i in range(K)]
    u = _module_mul_add(AT, r_vec, e1)

    # v = <t, r> + e2 + encode(m)
    v = _inner_product(t, r_vec)
    v = _poly_add(v, e2)
    v = _poly_add(v, encode_amount(amount_cents))

    flat = [c for poly in u for c in poly] + v
    return struct.pack("<%dH" % len(flat), *flat)


def commit(keypair: LWEKeyPair, amount_cents: int, txn_id: str, master_key: bytes) -> dict:
    """Produce a Module-LWE commitment to `amount_cents`.

    The returned dict carries no raw amount - only the commitment, its
    binding hash, and public parameters.
    """
    r_seed = derive_randomness(master_key, txn_id)
    blob = _commit_core(keypair.A, keypair.t, amount_cents, r_seed)

    return {
        "txn_id": txn_id,
        "commitment_b64": base64.b64encode(blob).decode(),
        "size_kb": round(len(blob) / 1024, 2),
        "binding_hash": hashlib.sha3_256(blob).hexdigest(),
        "pubkey_fingerprint": keypair.fingerprint(),
        "lwe_params": {"n": N, "k": K, "q": Q, "eta": ETA, "amount_bits": AMOUNT_BITS},
        "timestamp_ns": time.time_ns(),
        "pii_bytes": 0,
    }


def open_commitment(
    public_key: LWEPublicKey,
    commitment_b64: str,
    amount_cents: int,
    blinding_seed_hex: str,
) -> bool:
    """Verify a disclosed opening against a published commitment.

    This is the selective-disclosure path: the bank reveals (amount, blinding)
    for one transaction under audit, and the auditor - holding only the public
    key - recomputes the commitment and compares. Every other transaction in
    the ledger stays sealed.
    """
    try:
        expected = base64.b64decode(commitment_b64)
        recomputed = _commit_core(
            public_key.A, public_key.t, amount_cents, bytes.fromhex(blinding_seed_hex)
        )
    except (ValueError, TypeError) as exc:
        logger.warning("malformed opening: %s", exc)
        return False
    return hmac.compare_digest(expected, recomputed)


# -- Verification trace (prover side, for the audit terminal) -----------------

def verify(
    keypair: LWEKeyPair,
    commitment_record: dict,
    amount_cents: int,
    txn_id: str,
    master_key: bytes,
) -> dict:
    """Recompute a commitment and compare it, emitting a step-by-step trace."""
    trace = [{
        "step": "DERIVE_BLINDING",
        "detail": "r = HMAC-SHA256(K_master, %s)" % txn_id,
        "status": "DONE",
    }]

    try:
        recomputed = commit(keypair, amount_cents, txn_id, master_key)
    except ValueError as exc:
        trace.append({"step": "ENCODE_AMOUNT", "detail": str(exc), "status": "FAILED"})
        return {"verified": False, "txn_id": txn_id, "trace": trace, "pii_bytes": 0}

    trace.append({
        "step": "EXPAND_MATRIX_A",
        "detail": "A in R_q^(%dx%d), deg %d, q=%d" % (K, K, N, Q),
        "status": "DONE",
    })
    trace.append({
        "step": "RECOMPUTE_COMMITMENT",
        "detail": "u = A^T.r + e1 ; v = <t,r> + e2 + encode(m)  [%d-bit domain]" % AMOUNT_BITS,
        "status": "DONE",
    })

    stored = commitment_record.get("binding_hash", "")
    fresh = recomputed["binding_hash"]
    match = bool(stored) and hmac.compare_digest(stored, fresh)

    trace.append({
        "step": "CONSTANT_TIME_COMPARE",
        "detail": "stored=%s... recomputed=%s..." % (stored[:16], fresh[:16]),
        "status": "DONE",
    })
    trace.append({
        "step": "RESULT",
        "detail": "LWE PROOF INTACT - BINDING VERIFIED" if match
                  else "PROOF MISMATCH - INTEGRITY VIOLATION",
        "status": "VERIFIED" if match else "FAILED",
    })

    return {"verified": match, "txn_id": txn_id, "trace": trace, "pii_bytes": 0}


# -- Process-wide key store ---------------------------------------------------
#
# Both the master key and the lattice seed are read from the environment so a
# restarted prover reproduces the same keypair. Without them a fresh random key
# is generated and every previously published commitment becomes unopenable -
# tolerable for a scratch run, fatal for a ledger.

_KEYPAIR: Optional[LWEKeyPair] = None
_MASTER_KEY: Optional[bytes] = None


def _load_master_key() -> bytes:
    raw = os.environ.get("ZEROAUDIT_MASTER_KEY", "").strip()
    if raw:
        if len(raw) == 64:
            try:
                return bytes.fromhex(raw)
            except ValueError:
                pass
        return raw.encode()
    logger.warning(
        "ZEROAUDIT_MASTER_KEY unset - generating an ephemeral key. "
        "Commitments will not survive a restart."
    )
    return os.urandom(32)


def get_keypair() -> LWEKeyPair:
    global _KEYPAIR
    if _KEYPAIR is None:
        seed_hex = os.environ.get("LWE_SEED_HEX", "").strip()
        seed = None
        if seed_hex:
            try:
                seed = bytes.fromhex(seed_hex)[:SEED_LEN].ljust(SEED_LEN, b"\0")
            except ValueError:
                logger.error("LWE_SEED_HEX is not valid hex - using a random seed")
        else:
            logger.warning("LWE_SEED_HEX unset - generating an ephemeral lattice seed")
        _KEYPAIR = LWEKeyPair(seed)
        logger.info("LWE keypair ready - fingerprint=%s", _KEYPAIR.fingerprint())
    return _KEYPAIR


def get_master_key() -> bytes:
    global _MASTER_KEY
    if _MASTER_KEY is None:
        _MASTER_KEY = _load_master_key()
    return _MASTER_KEY


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("=== ZEROAUDIT Module-LWE Self-Test ===")
    print("backend: %s" % ("numpy" if _NUMPY else "pure-python"))

    kp = get_keypair()
    mk = get_master_key()
    print("params:  %s" % kp.to_dict())

    t0 = time.perf_counter()
    c = commit(kp, amount_cents=150_000, txn_id="TXN-TEST-0001", master_key=mk)
    dt = time.perf_counter() - t0
    print("commit:  %.2f ms  (%d commits/s single-core)" % (dt * 1000, 1 / dt))
    print("size:    %s KB   binding=%s..." % (c["size_kb"], c["binding_hash"][:32]))

    print("open(correct):     %s" % verify(kp, c, 150_000, "TXN-TEST-0001", mk)["verified"])
    print("open(tampered):    %s" % verify(kp, c, 999_999, "TXN-TEST-0001", mk)["verified"])
    print("open(+2^32):       %s" % verify(kp, c, 150_000 + (1 << 32), "TXN-TEST-0001", mk)["verified"])
    print("open(+2^40):       %s" % verify(kp, c, 150_000 + (1 << 40), "TXN-TEST-0001", mk)["verified"])

    pub = LWEPublicKey.from_b64(kp.public_key_b64())
    r_hex = derive_randomness(mk, "TXN-TEST-0001").hex()
    print("external open:     %s" % open_commitment(pub, c["commitment_b64"], 150_000, r_hex))
    print("external tampered: %s" % open_commitment(pub, c["commitment_b64"], 150_001, r_hex))
