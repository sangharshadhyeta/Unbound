"""
Per-operation, per-job deterministic key derivation for Arithmetic Mask Propagation.

Each call to next_mask() produces a unique integer derived from the master
key K and an auto-incrementing counter.  The same (K, job_id, counter)
triple always produces the same mask — deterministic, reproducible, no
randomness stored.

Security: compromise of one mask reveals exactly one value.  Knowing
mask[3] gives no information about mask[4] — HMAC prevents extension.
"""

import hashlib
import hmac as _hmac

# Ed25519 field prime: 2^255 - 19.
# Well-studied, 255-bit, fits in 32 bytes.  Large enough that mask values
# appear statistically random in the integer domain relative to input data.
MODULUS: int = (1 << 255) - 19


class KeyDeriver:
    """
    Derives a unique integer mask for each operation in a job.

    K + job_id + auto-counter  →  deterministic mask sequence.

    Call next_mask() once per INPUT value encountered during mask
    compilation.  Call reset() to replay the same sequence (e.g. for
    correction verification).
    """

    def __init__(
        self,
        master_key: bytes,
        job_id: str,
        modulus: int = MODULUS,
    ):
        if len(master_key) < 16:
            raise ValueError("master_key must be at least 16 bytes")
        self._key     = master_key
        self._job_id  = job_id
        self._modulus = modulus
        self._counter = 0

    def next_mask(self) -> int:
        """Return next deterministic mask.  Counter auto-increments."""
        msg    = f"{self._job_id}:{self._counter}".encode()
        digest = _hmac.new(self._key, msg, hashlib.sha256).digest()
        self._counter += 1
        return int.from_bytes(digest, "big") % self._modulus

    def reset(self) -> None:
        """Reset counter — replays the same mask sequence from the start."""
        self._counter = 0

    @property
    def modulus(self) -> int:
        return self._modulus

    @property
    def counter(self) -> int:
        return self._counter
