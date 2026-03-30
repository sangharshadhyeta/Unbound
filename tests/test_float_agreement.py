"""
Tests for float tolerance agreement and float-op auto-detection.

_results_agree: the agreement function used when k-of-2 miners submit
results for the same chunk. Integer outputs are exact; float outputs use
combined rel+abs tolerance.

_has_float_ops: scans job streams at submit time to set float_mode on
the JobRecord automatically, without any submitter input.
"""

import math

import pytest

from unbound.verifier.verifier import results_agree as _results_agree
from unbound.api.app import _has_float_ops
from unbound.uvm.opcodes import FADD, FCONST, FTOI, ADD, OUTPUT, HALT


# ── _results_agree ────────────────────────────────────────────────────

def test_agree_exact_integers():
    assert _results_agree([1, 2, 3], [1, 2, 3], float_mode=False, epsilon=0.0)


def test_disagree_exact_integers():
    assert not _results_agree([1, 2, 3], [1, 2, 4], float_mode=False, epsilon=0.0)


def test_agree_length_mismatch():
    assert not _results_agree([1, 2], [1, 2, 3], float_mode=False, epsilon=0.0)


def test_agree_floats_within_abs_floor():
    # Two values that differ only at the last bit of float64 — within 1e-9 abs floor
    a = [1.0]
    b = [1.0 + 1e-15]
    assert _results_agree(a, b, float_mode=True, epsilon=0.0)


def test_disagree_floats_outside_tolerance():
    a = [1.0]
    b = [1.01]
    # epsilon=0 → abs_tol=1e-9, these are 0.01 apart — should disagree
    assert not _results_agree(a, b, float_mode=True, epsilon=0.0)


def test_agree_floats_with_user_epsilon():
    # ML loss values: submitter sets epsilon=1e-4 for GPU/CPU tolerance
    a = [0.35124]
    b = [0.35127]
    assert _results_agree(a, b, float_mode=True, epsilon=1e-4)


def test_disagree_floats_outside_user_epsilon():
    a = [0.35]
    b = [0.36]
    assert not _results_agree(a, b, float_mode=True, epsilon=1e-4)


def test_agree_mixed_int_and_float_outputs():
    # Integer index (exact) + float score (tolerance)
    a = [42, 0.91230]
    b = [42, 0.91231]
    assert _results_agree(a, b, float_mode=True, epsilon=1e-4)


def test_disagree_mixed_wrong_integer():
    # Float score agrees but integer index differs — must disagree
    a = [42, 0.9123]
    b = [43, 0.9123]
    assert not _results_agree(a, b, float_mode=True, epsilon=1e-4)


def test_float_mode_false_ignores_tolerance():
    # Without float_mode, even tiny float differences are treated as exact mismatch
    a = [0.1 + 0.2]          # 0.30000000000000004 in IEEE 754
    b = [0.3]
    assert not _results_agree(a, b, float_mode=False, epsilon=0.0)


def test_agree_empty_results():
    assert _results_agree([], [], float_mode=False, epsilon=0.0)
    assert _results_agree([], [], float_mode=True, epsilon=1e-4)


def test_agree_near_zero_floats():
    # Near-zero values: relative tolerance alone would fail (rel_tol * ~0 ≈ 0)
    # abs_tol=1e-9 must carry the comparison
    a = [1e-12]
    b = [2e-12]
    assert _results_agree(a, b, float_mode=True, epsilon=0.0)


# ── _has_float_ops ────────────────────────────────────────────────────

def test_detects_float_opcode_in_stream():
    # Stream containing FADD
    assert _has_float_ops([[ADD, FADD, OUTPUT, HALT]])


def test_detects_fconst():
    assert _has_float_ops([[FCONST, 0, OUTPUT, HALT]])


def test_detects_ftoi():
    assert _has_float_ops([[FTOI, OUTPUT, HALT]])


def test_no_float_opcodes_in_integer_stream():
    assert not _has_float_ops([[ADD, OUTPUT, HALT]])


def test_detects_float_in_any_chunk():
    # Only the second chunk has a float op — should still return True
    int_stream = [ADD, OUTPUT, HALT]
    float_stream = [FADD, OUTPUT, HALT]
    assert _has_float_ops([int_stream, float_stream])


def test_empty_streams():
    assert not _has_float_ops([[]])
    assert not _has_float_ops([])


# ── Capability auto-wiring ────────────────────────────────────────────

from unbound.registry.registry import Registry


def test_float_job_requires_float_capability():
    """Float chunks must only go to miners that declared float capability."""
    registry = Registry()
    float_stream = [FCONST, 0, OUTPUT, HALT]
    job = registry.create_job(
        submitter="alice", description="", chunks=[float_stream],
        payment=0, float_mode=True,
    )
    chunks = [c for c in registry._chunks.values() if c.job_id == job.job_id]
    assert all("float" in c.requirements for c in chunks)


def test_float_job_not_dispatched_to_integer_miner():
    """A miner without float capability must not receive float chunks."""
    registry = Registry()
    float_stream = [FCONST, 0, OUTPUT, HALT]
    registry.create_job(
        submitter="alice", description="", chunks=[float_stream],
        payment=0, float_mode=True,
    )
    # Integer-only miner declares no capabilities
    chunk = registry.next_available_chunk(capabilities=[])
    assert chunk is None


def test_float_job_dispatched_to_float_miner():
    """A miner with float capability receives float chunks."""
    registry = Registry()
    float_stream = [FCONST, 0, OUTPUT, HALT]
    registry.create_job(
        submitter="alice", description="", chunks=[float_stream],
        payment=0, float_mode=True,
    )
    chunk = registry.next_available_chunk(capabilities=["float"])
    assert chunk is not None


def test_integer_job_has_no_float_requirement():
    """Integer-only jobs must not gain a float requirement."""
    registry = Registry()
    int_stream = [ADD, OUTPUT, HALT]
    job = registry.create_job(
        submitter="alice", description="", chunks=[int_stream],
        payment=0, float_mode=False,
    )
    chunks = [c for c in registry._chunks.values() if c.job_id == job.job_id]
    assert all("float" not in c.requirements for c in chunks)


def test_float_mode_preserves_existing_requirements():
    """Extra requirements (e.g. gpu) survive alongside the auto-added float."""
    registry = Registry()
    float_stream = [FADD, OUTPUT, HALT]
    job = registry.create_job(
        submitter="alice", description="", chunks=[float_stream],
        payment=0, float_mode=True, requirements=["gpu"],
    )
    chunks = [c for c in registry._chunks.values() if c.job_id == job.job_id]
    assert all("float" in c.requirements and "gpu" in c.requirements for c in chunks)


def test_float_requirement_not_duplicated():
    """If submitter already included float in requirements, don't add it twice."""
    registry = Registry()
    float_stream = [FADD, OUTPUT, HALT]
    job = registry.create_job(
        submitter="alice", description="", chunks=[float_stream],
        payment=0, float_mode=True, requirements=["float"],
    )
    chunks = [c for c in registry._chunks.values() if c.job_id == job.job_id]
    assert all(c.requirements.count("float") == 1 for c in chunks)
