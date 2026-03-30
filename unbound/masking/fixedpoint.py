"""
FixedPointMasker — AMP masking for programs that work with scaled-integer inputs.

Many practical programs pass floating-point values as inputs but express their
arithmetic using integer UVM opcodes (ADD, MUL, etc.) after an upfront scaling
step. FixedPointMasker handles the scale/unscale automatically:

    1. Submitter's real float inputs are multiplied by `scale` and rounded to int.
    2. AMPMasker masks those integers and produces a MaskPlan.
    3. After the miner returns, FixedPointPlan.correct() undoes the mask correction
       and divides by the appropriate power of scale to recover float results.

Example — dot product of float vectors with 3 decimal precision:

    from unbound.masking import FixedPointMasker

    # Program computes dot product of two length-3 vectors (pre-scaled by 1000)
    masker = FixedPointMasker(master_key, scale=1000)
    plan   = masker.prepare(stream, [0.1, 0.2, 0.3, 0.4, 0.5, 0.6], job_id="fp-001")

    masked_out  = miner.run(stream, plan.masked_inputs)
    real_result = plan.correct(masked_out)   # → [float, ...]

Output scaling
--------------
The `output_scale` parameter controls how outputs are divided.  By default it
equals `scale ** 2` so that a single MUL of two scaled inputs produces a correctly
unscaled result.  For programs that only use ADD/SUB/NEG (linear in inputs),
pass `output_scale=scale`.

Limitations
-----------
  - Inputs must be representable as round(v * scale) without overflow.
  - Programs must use only integer UVM opcodes; float opcodes (FADD, FMUL, …)
    are not supported (they bypass AMP entirely).
  - Fixed-point truncation in integer DIV is the caller's responsibility —
    the masker scales values before submission but cannot fix algorithmic
    precision loss inside the program.
"""

from dataclasses import dataclass
from typing import List, Optional

from .nikhilam import AMPMasker
from .mask_compiler import MaskPlan
from .key_deriver import MODULUS


@dataclass
class FixedPointPlan:
    """
    Returned by FixedPointMasker.prepare().  Analogous to MaskPlan but
    correct() returns floats instead of integers.

    masked_inputs   — send to the miner's INPUT buffer (integers)
    output_scale    — each corrected integer is divided by this to recover
                      the real float result
    _plan           — the underlying integer MaskPlan (private)
    """
    masked_inputs:  List[int]
    output_scale:   int
    _plan:          MaskPlan

    def correct(self, miner_outputs: List[int]) -> List[float]:
        """
        Recover real float outputs from the miner's masked integer outputs.

        Applies integer AMP correction first, then divides each result by
        output_scale to undo the fixed-point scaling.
        """
        int_results = self._plan.correct(miner_outputs)
        return [v / self.output_scale for v in int_results]


class FixedPointMasker:
    """
    AMP masking for programs that accept scaled-integer representations of
    floating-point inputs.

    master_key    — secret bytes (≥ 16).  Same key as AMPMasker.
    scale         — integer multiplier applied to each float input before masking.
                    E.g. scale=1000 gives 3 decimal places of precision.
    output_scale  — divisor applied to corrected outputs.  Defaults to scale**2
                    (appropriate when outputs result from a single multiplication
                    of two scaled inputs, e.g. a dot product).  For linear-only
                    programs (ADD/SUB/NEG), pass output_scale=scale.
    modulus       — working field prime (default: Ed25519 field prime).
    """

    def __init__(
        self,
        master_key:   bytes,
        scale:        int = 1000,
        output_scale: Optional[int] = None,
        modulus:      int = MODULUS,
    ):
        if scale <= 0:
            raise ValueError("scale must be a positive integer")
        self._amp          = AMPMasker(master_key, modulus)
        self._scale        = scale
        self._output_scale = output_scale if output_scale is not None else scale * scale

    def prepare(
        self,
        stream:       List[int],
        float_inputs: List[float],
        job_id:       str,
    ) -> FixedPointPlan:
        """
        Mask a list of float inputs for a job submission.

        stream        — compiled UVM integer stream (opcodes + immediates)
        float_inputs  — real float values in INPUT-instruction order
        job_id        — unique job identifier (used in key derivation)

        Returns a FixedPointPlan:
          .masked_inputs  — send to the miner's INPUT buffer
          .correct(out)   — recover float results from miner's integer outputs
        """
        int_inputs = [round(v * self._scale) for v in float_inputs]
        plan = self._amp.prepare(stream, int_inputs, job_id)
        return FixedPointPlan(
            masked_inputs=plan.masked_inputs,
            output_scale=self._output_scale,
            _plan=plan,
        )
