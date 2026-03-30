"""
Beaver Triple — degree-2 → degree-1 multiplication linearisation.

A Beaver triple (u, v, w) satisfies w = u * v (mod M).

For a SECRET × SECRET multiplication where the miner holds
masked_a = a + r_a  and  masked_b = b + r_b:

  e = masked_a - u    (public — precomputed by submitter at prepare time)
  f = masked_b - v    (public — precomputed by submitter at prepare time)

  e*f + e*v + f*u + w  =  masked_a * masked_b            (identity)

Since e, f, u, v, w are all public constants at execution time,
every operation the miner performs is degree-1 (PUBLIC × constant or
constant + constant).  The submitter still applies the same correction
used for a plain MUL — the result seen by the miner is identical.

The mask_compiler uses this to replace SECRET × SECRET MUL in the
linearised_stream with: POP, POP, PUSH <precomputed product>.
The submitter precomputes the product at prepare time; the miner
never performs a masked multiplication.
"""

import random
from dataclasses import dataclass

from .key_deriver import MODULUS


@dataclass(frozen=True)
class BeaverTriple:
    """Preprocessing triple (u, v, w) with w = u * v mod M."""
    u: int
    v: int
    w: int

    def reveal(self, masked_a: int, masked_b: int, M: int = MODULUS):
        """
        Return public blinding values (e, f) for the miner.

        e = masked_a - u  and  f = masked_b - v are safe to reveal
        because u and v are fresh random values — they carry no
        information about a or b.
        """
        e = (masked_a - self.u) % M
        f = (masked_b - self.v) % M
        return e, f

    def linearise(self, masked_a: int, masked_b: int, M: int = MODULUS) -> int:
        """
        Compute masked_a * masked_b using only degree-1 operations.

        Verifies the Beaver identity:
          e*f + e*v + f*u + w  =  masked_a * masked_b  (mod M)

        Returns the product (mod M).
        """
        e, f = self.reveal(masked_a, masked_b, M)
        return (e * f + e * self.v + f * self.u + self.w) % M


def generate_triple(modulus: int = MODULUS) -> BeaverTriple:
    """Generate a fresh random Beaver triple (u, v, w) with w = u*v mod M."""
    u = random.randrange(modulus)
    v = random.randrange(modulus)
    return BeaverTriple(u=u, v=v, w=(u * v) % modulus)
