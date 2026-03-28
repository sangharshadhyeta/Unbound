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
