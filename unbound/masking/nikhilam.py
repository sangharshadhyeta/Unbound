"""
AMPMasker — user-facing privacy layer for Unbound job submission.

Arithmetic Mask Propagation (AMP): each input value is additively masked by a
key-derived offset before leaving the submitter's machine. Masks propagate
algebraically through the full computation — including multiplication via
quadratic cross-product correction — so the submitter recovers exact outputs
from a blind evaluator without noise, approximation, or trusted hardware.

The additive complement principle underlying this scheme draws from the Vedic
sutra Nikhilam Navatashcaramam Dashatah (All from 9, last from 10), which
formalises complement-based arithmetic. AMP extends that principle into a
general algebraic propagation rule over a prime field.

Privacy model
-------------
  • Each INPUT value gets a unique, deterministic, key-derived mask.
  • Masks are additive: miner sees (value + mask) mod M.
  • The mask propagates through the computation algebraically; the
    submitter precomputes the correction and recovers exact results.
  • K and schema never leave the submitter's machine.
  • Per-job key derivation: same data submitted as two different jobs
    produces two different masked streams — no cross-job correlation.

Usage
-----
    masker = AMPMasker(master_key)
    plan   = masker.prepare(stream, inputs, job_id="job-abc")

    # Send plan.masked_inputs to the miner via the INPUT buffer.
    # The stream (bytecode) is unchanged — send as-is.

    # After the miner returns results:
    real_results = plan.correct(miner_results)
"""

from typing import List

from .key_deriver import KeyDeriver, MODULUS
from .mask_compiler import MaskCompiler, MaskPlan, MaskError  # noqa: F401

# Backwards-compatible alias
NikhilamError = MaskError


class AMPMasker:
    """
    Prepares masked job inputs and corrects miner outputs.

    master_key — secret bytes known only to the submitter (≥ 16 bytes).
                 Derive from a passphrase via SchemaVault; never hardcode.
    modulus    — working integer modulus M (default: Ed25519 field prime).
    """

    def __init__(self, master_key: bytes, modulus: int = MODULUS):
        if len(master_key) < 16:
            raise ValueError("master_key must be at least 16 bytes")
        self._master_key = master_key
        self._modulus    = modulus

    def prepare(
        self,
        stream: List[int],
        inputs: List[int],
        job_id: str,
    ) -> MaskPlan:
        """
        Mask inputs for a single job submission.

        stream   — compiled UVM integer stream (opcodes + immediates)
        inputs   — real sensitive values, in the order INPUT instructions
                   consume them
        job_id   — unique job identifier; used in key derivation so that
                   the same data submitted as different jobs produces
                   different masked streams

        Returns a MaskPlan:
          .masked_inputs       — send these to the miner's INPUT buffer
          .output_corrections  — one correction per OUTPUT instruction
          .correct(outputs)    — apply corrections; returns real values
        """
        deriver = KeyDeriver(self._master_key, job_id, self._modulus)
        return MaskCompiler().compile(stream, inputs, deriver)


# Backwards-compatible alias
NikhilamMasker = AMPMasker
