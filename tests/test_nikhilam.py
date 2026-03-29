"""
Tests for Nikhilam masking — KeyDeriver, MaskCompiler, NikhilamMasker,
and SchemaVault serialisation guard.

Each test verifies the core guarantee: the miner receives masked integers,
executes the UVM, and the submitter recovers the exact real result.
"""

import json
import os
import pickle
import pytest

from unbound.masking import (
    KeyDeriver, MaskPlan, NikhilamError, NikhilamMasker, MODULUS,
)
from unbound.masking.mask_compiler import MaskCompiler
from unbound.masking.schema_vault import SchemaVault
from unbound.uvm.opcodes import (
    ADD, DIV, DUP, EQ, HALT, INPUT, MUL, NEG, OUTPUT, PUSH, STORE, LOAD,
    SUB, JT, JF, AND, MOD, FCONST,
)
from unbound.uvm.vm import UVM


KEY = b"test-master-key-16bytes"
M   = MODULUS


# ── KeyDeriver ───────────────────────────────────────────────────────

def test_key_deriver_deterministic():
    d1 = KeyDeriver(KEY, "job1")
    d2 = KeyDeriver(KEY, "job1")
    assert d1.next_mask() == d2.next_mask()
    assert d1.next_mask() == d2.next_mask()


def test_key_deriver_counter_increments():
    d = KeyDeriver(KEY, "job1")
    m0 = d.next_mask()
    m1 = d.next_mask()
    assert d.counter == 2
    assert m0 != m1          # different per operation


def test_key_deriver_reset_replays():
    d = KeyDeriver(KEY, "job1")
    masks_first  = [d.next_mask() for _ in range(5)]
    d.reset()
    masks_second = [d.next_mask() for _ in range(5)]
    assert masks_first == masks_second


def test_key_deriver_job_isolation():
    """Same K, different job_id → different mask sequence."""
    d1 = KeyDeriver(KEY, "jobA")
    d2 = KeyDeriver(KEY, "jobB")
    assert d1.next_mask() != d2.next_mask()


def test_key_deriver_short_key_rejected():
    with pytest.raises(ValueError, match="at least 16 bytes"):
        KeyDeriver(b"short", "job1")


def test_key_deriver_masks_in_range():
    d = KeyDeriver(KEY, "job1")
    for _ in range(20):
        assert 0 <= d.next_mask() < M


# ── MaskPlan.correct ─────────────────────────────────────────────────

def test_mask_plan_correct_basic():
    plan = MaskPlan(masked_inputs=[99], output_corrections=[10], modulus=1000)
    assert plan.correct([115]) == [105]   # 115 - 10 = 105


def test_mask_plan_correct_negative_result():
    """Values above M//2 are returned as negative integers."""
    plan = MaskPlan(masked_inputs=[], output_corrections=[3], modulus=100)
    # real = (98 - 3) % 100 = 95 > 50 → 95 - 100 = -5
    assert plan.correct([98]) == [-5]


def test_mask_plan_wrong_output_count():
    plan = MaskPlan(masked_inputs=[], output_corrections=[1, 2], modulus=M)
    with pytest.raises(ValueError, match="Expected 2"):
        plan.correct([42])


# ── MaskCompiler — ADD (linear) ──────────────────────────────────────

def _run_masked(stream, inputs):
    """Compile mask plan, run UVM on masked inputs, correct output."""
    masker = NikhilamMasker(KEY)
    plan   = masker.prepare(stream, inputs, job_id="t")
    miner_out = UVM().execute(stream, inputs=plan.masked_inputs)
    return plan.correct(miner_out)


def test_add_two_inputs():
    stream = [INPUT, INPUT, ADD, OUTPUT, HALT]
    assert _run_masked(stream, [5, 3]) == [8]


def test_add_three_inputs():
    stream = [INPUT, INPUT, ADD, INPUT, ADD, OUTPUT, HALT]
    assert _run_masked(stream, [10, 20, 30]) == [60]


def test_add_input_and_constant():
    stream = [INPUT, PUSH, 100, ADD, OUTPUT, HALT]
    assert _run_masked(stream, [42]) == [142]


def test_sub_two_inputs():
    stream = [INPUT, INPUT, SUB, OUTPUT, HALT]
    assert _run_masked(stream, [10, 3]) == [7]


def test_neg_input():
    stream = [INPUT, NEG, OUTPUT, HALT]
    assert _run_masked(stream, [7]) == [-7]


# ── MaskCompiler — MUL (quadratic) ───────────────────────────────────

def test_mul_two_inputs():
    stream = [INPUT, INPUT, MUL, OUTPUT, HALT]
    assert _run_masked(stream, [5, 3]) == [15]


def test_mul_input_by_constant():
    stream = [INPUT, PUSH, 4, MUL, OUTPUT, HALT]
    assert _run_masked(stream, [7]) == [28]


def test_mul_then_add():
    # (a * b) + c
    stream = [INPUT, INPUT, MUL, INPUT, ADD, OUTPUT, HALT]
    assert _run_masked(stream, [3, 4, 5]) == [17]


def test_add_then_mul():
    # (a + b) * c
    stream = [INPUT, INPUT, ADD, INPUT, MUL, OUTPUT, HALT]
    assert _run_masked(stream, [5, 3, 2]) == [16]


def test_mul_chained():
    # a * b * c
    stream = [INPUT, INPUT, MUL, INPUT, MUL, OUTPUT, HALT]
    assert _run_masked(stream, [2, 3, 4]) == [24]


def test_dot_product_two_elements():
    # a1*b1 + a2*b2  (typical inner product)
    stream = [
        INPUT, INPUT, MUL,   # a1*b1
        INPUT, INPUT, MUL,   # a2*b2
        ADD,                 # sum
        OUTPUT, HALT,
    ]
    assert _run_masked(stream, [2, 3, 4, 5]) == [26]   # 6 + 20


# ── MaskCompiler — STORE / LOAD (memory) ────────────────────────────

def test_store_load_preserves_mask():
    # x = INPUT; y = x * INPUT; print(y)
    stream = [
        INPUT,       STORE, 0,     # x = input[0]
        INPUT,       STORE, 1,     # y = input[1]
        LOAD, 0,  LOAD, 1,  MUL,  OUTPUT,   # x * y
        HALT,
    ]
    assert _run_masked(stream, [6, 7]) == [42]


def test_dup_preserves_mask():
    # a * a  (square via DUP)
    stream = [INPUT, DUP, MUL, OUTPUT, HALT]
    assert _run_masked(stream, [9]) == [81]


# ── MaskCompiler — multiple outputs ──────────────────────────────────

def test_multiple_outputs():
    stream = [INPUT, OUTPUT, INPUT, OUTPUT, HALT]
    assert _run_masked(stream, [11, 22]) == [11, 22]


def test_output_after_mul_and_add():
    # Two separate computations, two outputs
    stream = [
        INPUT, INPUT, ADD, OUTPUT,   # a+b
        INPUT, INPUT, MUL, OUTPUT,   # c*d
        HALT,
    ]
    assert _run_masked(stream, [3, 4, 5, 6]) == [7, 30]


# ── MaskCompiler — DIV by public constant ───────────────────────────

def test_div_by_public_constant():
    # 20 // 4 = 5
    stream = [INPUT, PUSH, 4, DIV, OUTPUT, HALT]
    assert _run_masked(stream, [20]) == [5]


# ── NikhilamError cases ───────────────────────────────────────────────

def test_error_mul_by_masked_then_compare():
    """Comparison on a masked value must be rejected."""
    stream = [INPUT, PUSH, 5, EQ, JT, 0, HALT]
    with pytest.raises(NikhilamError, match="masked"):
        _run_masked(stream, [5])


def test_error_and_on_masked():
    stream = [INPUT, PUSH, 1, AND, OUTPUT, HALT]
    with pytest.raises(NikhilamError):
        _run_masked(stream, [3])


def test_error_mod_on_masked():
    stream = [INPUT, PUSH, 3, MOD, OUTPUT, HALT]
    with pytest.raises(NikhilamError, match="MOD"):
        _run_masked(stream, [7])


def test_error_float_ops_rejected():
    stream = [FCONST, 0, OUTPUT, HALT]
    with pytest.raises(NikhilamError, match="Float"):
        _run_masked(stream, [])


def test_error_div_masked_divisor():
    stream = [PUSH, 10, INPUT, DIV, OUTPUT, HALT]
    with pytest.raises(NikhilamError, match="masked divisor"):
        _run_masked(stream, [2])


def test_error_jt_on_masked():
    # Condition comes directly from INPUT — masked → error
    stream = [INPUT, JT, 0, HALT]
    with pytest.raises(NikhilamError, match="masked"):
        _run_masked(stream, [1])


def test_error_jf_on_masked():
    stream = [INPUT, JF, 0, HALT]
    with pytest.raises(NikhilamError, match="masked"):
        _run_masked(stream, [0])


# ── Determinism & isolation ───────────────────────────────────────────

def test_same_job_same_masks():
    stream = [INPUT, OUTPUT, HALT]
    m = NikhilamMasker(KEY)
    p1 = m.prepare(stream, [42], job_id="j1")
    p2 = m.prepare(stream, [42], job_id="j1")
    assert p1.masked_inputs == p2.masked_inputs


def test_different_jobs_different_masks():
    stream = [INPUT, OUTPUT, HALT]
    m = NikhilamMasker(KEY)
    p1 = m.prepare(stream, [42], job_id="j1")
    p2 = m.prepare(stream, [42], job_id="j2")
    assert p1.masked_inputs != p2.masked_inputs


def test_masked_inputs_differ_from_real():
    stream = [INPUT, OUTPUT, HALT]
    m = NikhilamMasker(KEY)
    plan = m.prepare(stream, [42], job_id="j")
    assert plan.masked_inputs[0] != 42


def test_miner_never_sees_real_value():
    """Masked value must not equal real value for any of 100 random-ish inputs."""
    stream = [INPUT, OUTPUT, HALT]
    m = NikhilamMasker(KEY)
    for v in range(100):
        plan = m.prepare(stream, [v], job_id=f"j{v}")
        assert plan.masked_inputs[0] != v


# ── SchemaVault serialisation guard ──────────────────────────────────

def _write_temp_schema(tmp_path):
    schema = {"variables": {"x": 0}, "output_positions": [2], "stream_length": 4}
    p = tmp_path / "prog.schema"
    p.write_text(json.dumps(schema))
    return str(p)


def test_schema_vault_from_key(tmp_path):
    schema_path = _write_temp_schema(tmp_path)
    vault = SchemaVault.from_key(KEY, schema_path)
    assert vault.variables == {"x": 0}
    assert vault.output_positions == [2]


def test_schema_vault_prepare_and_correct(tmp_path):
    schema_path = _write_temp_schema(tmp_path)
    vault  = SchemaVault.from_key(KEY, schema_path)
    stream = [INPUT, INPUT, MUL, OUTPUT, HALT]
    plan   = vault.prepare(stream, [6, 7], job_id="v1")
    mout   = UVM().execute(stream, inputs=plan.masked_inputs)
    assert plan.correct(mout) == [42]


def test_schema_vault_repr_hides_key(tmp_path):
    schema_path = _write_temp_schema(tmp_path)
    vault = SchemaVault.from_key(KEY, schema_path)
    assert "sealed" in repr(vault)
    assert KEY.decode() not in repr(vault)


def test_schema_vault_pickle_blocked(tmp_path):
    schema_path = _write_temp_schema(tmp_path)
    vault = SchemaVault.from_key(KEY, schema_path)
    with pytest.raises(TypeError, match="serialised"):
        pickle.dumps(vault)
