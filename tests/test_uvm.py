"""Tests for the UVM runtime."""

import pytest
from unbound.uvm.vm import UVM, VMError
from unbound.uvm.opcodes import PUSH, ADD, SUB, MUL, DIV, MOD, OUTPUT, HALT, INPUT, JF, JMP, LT, STORE, LOAD, EQ


vm = UVM()


def test_push_output():
    stream = [PUSH, 42, OUTPUT, HALT]
    assert vm.execute(stream) == [42]


def test_arithmetic():
    # (3 + 4) * 2 = 14
    stream = [PUSH, 3, PUSH, 4, ADD, PUSH, 2, MUL, OUTPUT, HALT]
    assert vm.execute(stream) == [14]


def test_subtraction():
    stream = [PUSH, 10, PUSH, 3, SUB, OUTPUT, HALT]
    assert vm.execute(stream) == [7]


def test_division():
    stream = [PUSH, 10, PUSH, 3, DIV, OUTPUT, HALT]
    assert vm.execute(stream) == [3]


def test_modulo():
    stream = [PUSH, 10, PUSH, 3, MOD, OUTPUT, HALT]
    assert vm.execute(stream) == [1]


def test_input():
    stream = [INPUT, PUSH, 5, ADD, OUTPUT, HALT]
    assert vm.execute(stream, inputs=[10]) == [15]


def test_load_store():
    stream = [PUSH, 99, STORE, 0, LOAD, 0, OUTPUT, HALT]
    assert vm.execute(stream) == [99]


def test_conditional_true():
    # if 3 < 5: output 1 else output 0
    # positions: [0]PUSH [1]3 [2]PUSH [3]5 [4]LT
    #            [5]JF [6]5  → if false: ip=7+5=12
    #            [7]PUSH [8]1 [9]OUTPUT [10]JMP [11]3 → ip=12+3=15
    #            [12]PUSH [13]0 [14]OUTPUT
    #            [15]HALT
    stream = [
        PUSH, 3, PUSH, 5, LT,
        JF, 5,
        PUSH, 1, OUTPUT, JMP, 3,
        PUSH, 0, OUTPUT,
        HALT,
    ]
    assert vm.execute(stream) == [1]


def test_conditional_false():
    stream = [
        PUSH, 7, PUSH, 5, LT,
        JF, 5,
        PUSH, 1, OUTPUT, JMP, 3,
        PUSH, 0, OUTPUT,
        HALT,
    ]
    assert vm.execute(stream) == [0]


def test_multiple_outputs():
    stream = [PUSH, 1, OUTPUT, PUSH, 2, OUTPUT, PUSH, 3, OUTPUT, HALT]
    assert vm.execute(stream) == [1, 2, 3]


def test_stack_underflow():
    stream = [ADD, HALT]
    with pytest.raises(VMError, match="underflow"):
        vm.execute(stream)


def test_division_by_zero():
    stream = [PUSH, 5, PUSH, 0, DIV, HALT]
    with pytest.raises(VMError, match="zero"):
        vm.execute(stream)


def test_input_exhausted():
    stream = [INPUT, INPUT, HALT]
    with pytest.raises(VMError, match="exhausted"):
        vm.execute(stream, inputs=[1])


def test_max_steps():
    # infinite loop
    stream = [JMP, -2]
    with pytest.raises(VMError, match="exceeded"):
        vm.execute(stream)


# ── Float opcode tests ────────────────────────────────────────────────────────

import struct
from unbound.uvm.opcodes import FCONST, FADD, FSUB, FMUL, FDIV, FNEG, ITOF, FTOI, FMOD


def _fconst(val: float) -> list:
    """Emit FCONST + int64 bits for a float value."""
    bits = struct.unpack('q', struct.pack('d', val))[0]
    return [FCONST, bits]


def test_fconst():
    stream = [*_fconst(3.14), OUTPUT, HALT]
    result = vm.execute(stream)
    assert len(result) == 1
    assert abs(result[0] - 3.14) < 1e-10


def test_fadd():
    stream = [*_fconst(1.5), *_fconst(2.5), FADD, OUTPUT, HALT]
    assert abs(vm.execute(stream)[0] - 4.0) < 1e-10


def test_fsub():
    stream = [*_fconst(5.0), *_fconst(1.5), FSUB, OUTPUT, HALT]
    assert abs(vm.execute(stream)[0] - 3.5) < 1e-10


def test_fmul():
    stream = [*_fconst(2.0), *_fconst(3.14), FMUL, OUTPUT, HALT]
    assert abs(vm.execute(stream)[0] - 6.28) < 1e-10


def test_fdiv():
    stream = [*_fconst(10.0), *_fconst(4.0), FDIV, OUTPUT, HALT]
    assert abs(vm.execute(stream)[0] - 2.5) < 1e-10


def test_fneg():
    stream = [*_fconst(3.0), FNEG, OUTPUT, HALT]
    assert abs(vm.execute(stream)[0] - (-3.0)) < 1e-10


def test_itof():
    stream = [PUSH, 7, ITOF, *_fconst(0.5), FADD, OUTPUT, HALT]
    assert abs(vm.execute(stream)[0] - 7.5) < 1e-10


def test_ftoi():
    stream = [*_fconst(3.9), FTOI, OUTPUT, HALT]
    assert vm.execute(stream) == [3]


def test_ftoi_negative_truncates():
    stream = [*_fconst(-3.9), FTOI, OUTPUT, HALT]
    assert vm.execute(stream) == [-3]


def test_float_mixed_output():
    # Output both an int and a float
    stream = [PUSH, 42, OUTPUT, *_fconst(1.5), OUTPUT, HALT]
    result = vm.execute(stream)
    assert result[0] == 42
    assert abs(result[1] - 1.5) < 1e-10


# ── Capability routing tests ──────────────────────────────────────────────────

from unbound.registry.registry import Registry


def test_chunk_routes_to_capable_worker():
    reg = Registry()
    job = reg.create_job("alice", "test", [[PUSH, 1, OUTPUT, HALT]], 0, requirements=["gpu"])
    chunk = reg.next_available_chunk(capabilities=["gpu", "cuda12"])
    assert chunk is not None
    assert chunk.requirements == ["gpu"]


def test_chunk_not_routed_to_incapable_worker():
    reg = Registry()
    reg.create_job("alice", "test", [[PUSH, 1, OUTPUT, HALT]], 0, requirements=["gpu"])
    chunk = reg.next_available_chunk(capabilities=["cpu"])
    assert chunk is None


def test_chunk_no_requirements_routes_to_any_worker():
    reg = Registry()
    reg.create_job("alice", "test", [[PUSH, 1, OUTPUT, HALT]], 0, requirements=[])
    chunk = reg.next_available_chunk(capabilities=[])
    assert chunk is not None


def test_configurable_timeout_stored_on_job():
    reg = Registry()
    job = reg.create_job("alice", "test", [[PUSH, 1, OUTPUT, HALT]], 0, chunk_timeout=120.0)
    assert job.chunk_timeout == 120.0
