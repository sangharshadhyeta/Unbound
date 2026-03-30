"""
Tests for Beaver triple generation and the degree-2 → degree-1 linearisation.

Covers:
  - BeaverTriple invariant: w = u * v mod M
  - linearise() identity: e*f + e*v + f*u + w = masked_a * masked_b (mod M)
  - PUBLIC/SECRET MUL classification in mask_compiler
  - MaskPlan.degree2_muls count
  - MaskPlan.linearised_stream: generated for SECRET×SECRET, absent for PUBLIC×SECRET
  - Linearised stream produces identical corrected results to original stream
"""

import pytest

from unbound.masking.beaver import BeaverTriple, generate_triple
from unbound.masking.key_deriver import MODULUS
from unbound.masking import AMPMasker, MaskPlan
from unbound.uvm.opcodes import INPUT, MUL, OUTPUT, HALT, PUSH, ADD, POP
from unbound.uvm.vm import UVM


MASTER_KEY = b"test-beaver-key-0"
M = MODULUS


# ── BeaverTriple invariant ────────────────────────────────────────────────────

def test_triple_satisfies_uv_equals_w():
    for _ in range(20):
        t = generate_triple()
        assert t.w == (t.u * t.v) % M


def test_linearise_equals_masked_product():
    """e*f + e*v + f*u + w  ≡  masked_a * masked_b  (mod M)"""
    for _ in range(20):
        t = generate_triple()
        masked_a = (17 + 5) % M   # real=17, mask=5
        masked_b = (31 + 9) % M   # real=31, mask=9
        product_direct    = (masked_a * masked_b) % M
        product_linearise = t.linearise(masked_a, masked_b, M)
        assert product_linearise == product_direct


def test_reveal_blinding_values():
    t = generate_triple()
    masked_a, masked_b = 1000, 2000
    e, f = t.reveal(masked_a, masked_b, M)
    assert e == (masked_a - t.u) % M
    assert f == (masked_b - t.v) % M


# ── PUBLIC × SECRET: degree-1, no linearised_stream ─────────────────────────

def test_public_secret_mul_is_degree1():
    """PUSH literal × INPUT value — one mask is 0 → degree-1, no linearised stream."""
    # stream: PUSH 3, INPUT, MUL, OUTPUT, HALT
    stream = [PUSH, 3, INPUT, MUL, OUTPUT, HALT]
    masker = AMPMasker(MASTER_KEY)
    plan   = masker.prepare(stream, [7], job_id="job-pub-sec")
    assert plan.degree2_muls == 0
    assert plan.linearised_stream is None


def test_public_secret_mul_correct_result():
    stream = [PUSH, 5, INPUT, MUL, OUTPUT, HALT]
    masker = AMPMasker(MASTER_KEY)
    plan   = masker.prepare(stream, [4], job_id="job-ps-2")
    uvm    = UVM()
    masked_out = uvm.execute(stream, inputs=plan.masked_inputs)
    real   = plan.correct(masked_out)
    assert real == [20]


# ── SECRET × SECRET: degree-2, linearised_stream generated ──────────────────

def test_secret_secret_mul_counted():
    """Both operands from INPUT → degree-2, degree2_muls = 1."""
    stream = [INPUT, INPUT, MUL, OUTPUT, HALT]
    masker = AMPMasker(MASTER_KEY)
    plan   = masker.prepare(stream, [3, 7], job_id="job-ss-1")
    assert plan.degree2_muls == 1


def test_linearised_stream_generated_for_degree2():
    stream = [INPUT, INPUT, MUL, OUTPUT, HALT]
    masker = AMPMasker(MASTER_KEY)
    plan   = masker.prepare(stream, [3, 7], job_id="job-ss-ls")
    assert plan.linearised_stream is not None


def test_linearised_stream_contains_no_mul():
    """MUL is replaced by POP, POP, PUSH <constant> — no MUL opcode survives."""
    stream = [INPUT, INPUT, MUL, OUTPUT, HALT]
    masker = AMPMasker(MASTER_KEY)
    plan   = masker.prepare(stream, [5, 6], job_id="job-no-mul")
    assert MUL not in plan.linearised_stream


def test_linearised_stream_correct_result():
    """Executing the linearised stream gives the same real answer as the original."""
    stream = [INPUT, INPUT, MUL, OUTPUT, HALT]
    masker = AMPMasker(MASTER_KEY)
    plan   = masker.prepare(stream, [6, 7], job_id="job-lin-result")

    uvm = UVM()
    # Original stream with masked inputs
    orig_out = uvm.execute(stream, inputs=plan.masked_inputs)
    # Linearised stream with the same masked inputs
    lin_out  = uvm.execute(plan.linearised_stream, inputs=plan.masked_inputs)

    # Both produce the same corrected real result
    assert plan.correct(orig_out) == [42]
    assert plan.correct(lin_out)  == [42]


def test_two_secret_secret_muls():
    """Two degree-2 MULs → degree2_muls=2, both replaced in linearised_stream."""
    # (a * b) + (c * d)
    stream = [INPUT, INPUT, MUL, INPUT, INPUT, MUL, ADD, OUTPUT, HALT]
    masker = AMPMasker(MASTER_KEY)
    plan   = masker.prepare(stream, [2, 3, 4, 5], job_id="job-2muls")
    assert plan.degree2_muls == 2
    assert plan.linearised_stream is not None
    assert MUL not in plan.linearised_stream

    uvm = UVM()
    lin_out = uvm.execute(plan.linearised_stream, inputs=plan.masked_inputs)
    real    = plan.correct(lin_out)
    assert real == [2*3 + 4*5]   # 6 + 20 = 26


# ── Branching programs: no linearised_stream (offsets would shift) ───────────

def test_branching_program_no_linearised_stream():
    """JMP in stream → beaver_ok=False → linearised_stream stays None."""
    from unbound.uvm.opcodes import JMP
    # PUSH 1, JMP 0 (no-op jump), INPUT, INPUT, MUL, OUTPUT, HALT
    stream = [PUSH, 1, JMP, 0, INPUT, INPUT, MUL, OUTPUT, HALT]
    masker = AMPMasker(MASTER_KEY)
    plan   = masker.prepare(stream, [3, 4], job_id="job-branch")
    assert plan.degree2_muls == 1     # MUL is still counted
    assert plan.linearised_stream is None   # but no linearised stream produced


# ── Mixed program: some degree-1, some degree-2 ──────────────────────────────

def test_mixed_degree_program():
    """PUSH × INPUT (degree-1) followed by INPUT × INPUT (degree-2)."""
    # (5 * a) + (b * c)
    stream = [PUSH, 5, INPUT, MUL, INPUT, INPUT, MUL, ADD, OUTPUT, HALT]
    masker = AMPMasker(MASTER_KEY)
    plan   = masker.prepare(stream, [2, 3, 4], job_id="job-mixed")
    assert plan.degree2_muls == 1   # only the b*c is degree-2

    uvm = UVM()
    lin_out = uvm.execute(plan.linearised_stream, inputs=plan.masked_inputs)
    real    = plan.correct(lin_out)
    assert real == [5*2 + 3*4]   # 10 + 12 = 22
