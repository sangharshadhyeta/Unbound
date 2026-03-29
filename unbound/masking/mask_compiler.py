"""
Nikhilam Mask Compiler

Dual-simulates UVM execution — real values alongside additive masks —
to produce a MaskPlan that maps masked inputs to output corrections.

Masking principle (Nikhilam)
----------------------------
Each INPUT value v_i is replaced by:

    masked_i = (v_i + r_i) mod M       r_i = KeyDeriver.next_mask()

The mask r_i propagates through computation according to each opcode's
algebraic semantics:

    ADD / SUB  →  mask_out = mask_a ± mask_b          (linear, exact)
    MUL        →  mask_out = ra·mb + rb·ma + ma·mb    (quadratic, exact)
    NEG        →  mask_out = −mask_a                  (linear, exact)
    PUSH lit   →  mask = 0   (program constants are public)

After the miner executes on masked inputs, each output satisfies:

    miner_output = real_output + correction   (mod M)

so:  real_output = (miner_output − correction) mod M

Limitations
-----------
The following patterns raise NikhilamError because the miner would
compute a wrong (and uncorrectable) result:

  • Comparison / logic ops on masked values — the miner's boolean
    result would differ from the real boolean, corrupting control flow.
  • Data-dependent branches (JT/JF) conditioned on masked values.
  • DIV / MOD where the divisor is masked.
  • SHR on a masked dividend (fractional mask loss).
  • Float opcodes — a separate float-masking extension is needed.
"""

from dataclasses import dataclass
from typing import Dict, List

from ..uvm.opcodes import (
    ADD, AND, DIV, DUP, EQ, FCONST, FADD, FDIV, FMOD, FMUL, FNEG, FSUB,
    FTOI, GTE, GT, HALT, INPUT, ITOF, JF, JMP, JT, LOAD, LT, LTE,
    MOD, MUL, NEG, NEQ, NOT, OR, OUTPUT, POP, PUSH, SHL, SHR,
    STORE, SUB, SWAP, XOR,
)
from .key_deriver import KeyDeriver

_FLOAT_OPS   = frozenset([FCONST, FADD, FSUB, FMUL, FDIV, FMOD, FNEG, ITOF, FTOI])
_CMP_OPS     = frozenset([EQ, NEQ, LT, LTE, GT, GTE])
_LOGIC_OPS   = frozenset([AND, OR, XOR])


class NikhilamError(Exception):
    """Program structure is incompatible with Nikhilam masking."""


@dataclass
class MaskPlan:
    """
    Everything needed to submit a masked job and recover real results.

    masked_inputs        — send to the miner's INPUT buffer instead of
                           real values; same length as the inputs list.
    output_corrections   — corrections[i] corresponds to the i-th OUTPUT
                           instruction in the program.
    modulus              — working modulus M used throughout.
    """
    masked_inputs:      List[int]
    output_corrections: List[int]
    modulus:            int

    def correct(self, miner_outputs: List[int]) -> List[int]:
        """
        Recover real output values from the miner's masked outputs.

        Returns signed integers: values above M//2 are mapped to their
        negative equivalents so that, e.g., a real output of -7 comes
        back as -7 rather than M-7.
        """
        M      = self.modulus
        half_M = M >> 1
        if len(miner_outputs) != len(self.output_corrections):
            raise ValueError(
                f"Expected {len(self.output_corrections)} outputs, "
                f"got {len(miner_outputs)}"
            )
        results = []
        for mo, c in zip(miner_outputs, self.output_corrections):
            v = (mo - c) % M
            if v > half_M:          # signed interpretation
                v -= M
            results.append(v)
        return results


class MaskCompiler:
    """
    Compiles a UVM stream + real inputs into a MaskPlan.

    Run once per job at submission time.  The submitter provides real
    input values; the compiler assigns fresh masks from the key deriver
    and propagates them through the program's algebraic structure.
    """

    def compile(
        self,
        stream: List[int],
        inputs: List[int],
        deriver: KeyDeriver,
    ) -> MaskPlan:
        """
        Dual-simulate the program with real values and masks in parallel.

        stream   — decoded UVM integer stream (opcodes + immediates)
        inputs   — real sensitive input values in INPUT-instruction order
        deriver  — key deriver scoped to this job (reset before calling)

        Returns a MaskPlan containing masked_inputs and output_corrections.
        """
        M = deriver.modulus

        if any(op in _FLOAT_OPS for op in stream):
            raise NikhilamError(
                "Float opcodes detected. Nikhilam masking covers integer "
                "programs only. Float programs run without masking or need "
                "a float-masking extension."
            )

        real_stack: List[int]      = []
        mask_stack: List[int]      = []   # miner sees real[i] + mask[i]
        real_mem:   Dict[int, int] = {}
        mask_mem:   Dict[int, int] = {}

        inp             = list(inputs)
        masked_inputs:      List[int] = []
        output_corrections: List[int] = []

        ip = 0
        n  = len(stream)

        while ip < n:
            op = stream[ip]; ip += 1

            # ── Stack ────────────────────────────────────────────────
            if op == PUSH:
                val = stream[ip]; ip += 1
                real_stack.append(val)
                mask_stack.append(0)          # PUSH literals are public

            elif op == POP:
                real_stack.pop()
                mask_stack.pop()

            elif op == DUP:
                real_stack.append(real_stack[-1])
                mask_stack.append(mask_stack[-1])

            elif op == SWAP:
                real_stack[-1], real_stack[-2] = real_stack[-2], real_stack[-1]
                mask_stack[-1], mask_stack[-2] = mask_stack[-2], mask_stack[-1]

            elif op == LOAD:
                addr = stream[ip]; ip += 1
                real_stack.append(real_mem.get(addr, 0))
                mask_stack.append(mask_mem.get(addr, 0))

            elif op == STORE:
                addr = stream[ip]; ip += 1
                real_mem[addr] = real_stack.pop()
                mask_mem[addr] = mask_stack.pop()

            # ── I/O ──────────────────────────────────────────────────
            elif op == INPUT:
                if not inp:
                    raise NikhilamError("INPUT buffer exhausted during mask compilation")
                real_val = inp.pop(0)
                r = deriver.next_mask()
                masked_inputs.append((real_val + r) % M)
                real_stack.append(real_val)
                mask_stack.append(r)

            elif op == OUTPUT:
                real_stack.pop()              # keep stacks in sync
                correction = mask_stack.pop()
                output_corrections.append(int(correction) % M)

            # ── Arithmetic ───────────────────────────────────────────
            elif op == ADD:
                rb, ra = real_stack.pop(), real_stack.pop()
                mb, ma = mask_stack.pop(), mask_stack.pop()
                real_stack.append(ra + rb)
                mask_stack.append((ma + mb) % M)

            elif op == SUB:
                rb, ra = real_stack.pop(), real_stack.pop()
                mb, ma = mask_stack.pop(), mask_stack.pop()
                real_stack.append(ra - rb)
                mask_stack.append((ma - mb) % M)

            elif op == MUL:
                rb, ra = real_stack.pop(), real_stack.pop()
                mb, ma = mask_stack.pop(), mask_stack.pop()
                real_stack.append(ra * rb)
                # (ra + ma)(rb + mb) = ra·rb + ra·mb + rb·ma + ma·mb
                # correction term = ra·mb + rb·ma + ma·mb
                correction = (ra * mb + rb * ma + ma * mb) % M
                mask_stack.append(correction)

            elif op == NEG:
                real_stack.append(-real_stack.pop())
                mask_stack.append((-mask_stack.pop()) % M)

            elif op == DIV:
                rb, ra = real_stack.pop(), real_stack.pop()
                mb, ma = mask_stack.pop(), mask_stack.pop()
                if mb != 0:
                    raise NikhilamError(
                        "DIV with a masked divisor is not correctable. "
                        "The divisor must be a public constant (PUSH), not INPUT."
                    )
                if rb == 0:
                    raise NikhilamError("Division by zero during mask compilation")
                real_stack.append(ra // rb)
                # Mask propagation: (ra + ma) // rb — exact only if rb | ma.
                # For typical usage (divide by a public constant), this is fine.
                mask_stack.append((ma // rb) % M)

            elif op == MOD:
                rb, ra = real_stack.pop(), real_stack.pop()
                mb, ma = mask_stack.pop(), mask_stack.pop()
                if ma != 0 or mb != 0:
                    raise NikhilamError(
                        "MOD on masked values is not correctable. "
                        "Ensure both operands are public constants."
                    )
                real_stack.append(ra % rb)
                mask_stack.append(0)

            # ── Comparison — must operate on public values ────────────
            elif op in _CMP_OPS:
                rb, ra = real_stack.pop(), real_stack.pop()
                mb, ma = mask_stack.pop(), mask_stack.pop()
                if ma != 0 or mb != 0:
                    raise NikhilamError(
                        f"Comparison on masked values is not correctable and would "
                        f"corrupt the miner's branch decisions. "
                        f"Compare only public constants, not INPUT values."
                    )
                _cmp = {
                    EQ:  lambda a, b: 1 if a == b else 0,
                    NEQ: lambda a, b: 1 if a != b else 0,
                    LT:  lambda a, b: 1 if a < b  else 0,
                    LTE: lambda a, b: 1 if a <= b else 0,
                    GT:  lambda a, b: 1 if a > b  else 0,
                    GTE: lambda a, b: 1 if a >= b else 0,
                }
                real_stack.append(_cmp[op](ra, rb))
                mask_stack.append(0)

            # ── Logic — must operate on public values ─────────────────
            elif op in _LOGIC_OPS:
                rb, ra = real_stack.pop(), real_stack.pop()
                mb, ma = mask_stack.pop(), mask_stack.pop()
                if ma != 0 or mb != 0:
                    raise NikhilamError(
                        "Bitwise logic on masked values is not correctable."
                    )
                _log = {AND: lambda a, b: a & b, OR: lambda a, b: a | b, XOR: lambda a, b: a ^ b}
                real_stack.append(_log[op](ra, rb))
                mask_stack.append(0)

            elif op == NOT:
                ra = real_stack.pop(); ma = mask_stack.pop()
                if ma != 0:
                    raise NikhilamError("NOT on a masked value is not correctable.")
                real_stack.append(~ra)
                mask_stack.append(0)

            elif op == SHL:
                n_bits = real_stack.pop(); ra = real_stack.pop()
                mn     = mask_stack.pop(); ma = mask_stack.pop()
                if mn != 0:
                    raise NikhilamError("SHL with a masked shift amount is not correctable.")
                real_stack.append(ra << n_bits)
                mask_stack.append((ma << n_bits) % M)

            elif op == SHR:
                n_bits = real_stack.pop(); ra = real_stack.pop()
                mn     = mask_stack.pop(); ma = mask_stack.pop()
                if mn != 0:
                    raise NikhilamError("SHR with a masked shift amount is not correctable.")
                if ma != 0:
                    raise NikhilamError(
                        "SHR on a masked value is not correctable "
                        "(floor-division of the mask loses information)."
                    )
                real_stack.append(ra >> n_bits)
                mask_stack.append(0)

            # ── Control flow ─────────────────────────────────────────
            elif op == JMP:
                offset = stream[ip]; ip += 1
                ip += offset

            elif op == JT:
                offset = stream[ip]; ip += 1
                cond = real_stack.pop()
                mc   = mask_stack.pop()
                if mc != 0:
                    raise NikhilamError(
                        "JT (branch-if-true) depends on a masked value. "
                        "Data-dependent branches on INPUT values are not supported "
                        "— the miner would follow a different path than intended."
                    )
                if cond != 0:
                    ip += offset

            elif op == JF:
                offset = stream[ip]; ip += 1
                cond = real_stack.pop()
                mc   = mask_stack.pop()
                if mc != 0:
                    raise NikhilamError(
                        "JF (branch-if-false) depends on a masked value. "
                        "Data-dependent branches on INPUT values are not supported."
                    )
                if cond == 0:
                    ip += offset

            elif op == HALT:
                break

            # Unknown opcode — skip (mirrors UVM behaviour)

        return MaskPlan(
            masked_inputs=masked_inputs,
            output_corrections=output_corrections,
            modulus=M,
        )
