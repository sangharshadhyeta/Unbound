"""Tests for the Compiler + UVM round-trip."""

import pytest
from unbound.compiler.compiler import compile_source, CompileError
from unbound.uvm.vm import UVM

vm = UVM()


def run(source: str, inputs=None) -> list:
    stream, schema = compile_source(source)
    return vm.execute(stream, inputs=inputs)


def test_simple_print():
    assert run("print(42)") == [42]


def test_arithmetic_expression():
    assert run("print(3 + 4 * 2)") == [11]


def test_variable_assignment():
    src = """
x = 10
y = 3
print(x + y)
"""
    assert run(src) == [13]


def test_subtraction():
    assert run("print(10 - 3)") == [7]


def test_floor_division():
    assert run("print(10 // 3)") == [3]


def test_modulo():
    assert run("print(10 % 3)") == [1]


def test_negation():
    assert run("print(-5)") == [-5]


def test_if_true():
    src = """
x = 5
if x > 3:
    print(1)
"""
    assert run(src) == [1]


def test_if_false():
    src = """
x = 2
if x > 3:
    print(1)
"""
    assert run(src) == []


def test_if_else():
    src = """
x = 2
if x > 3:
    print(1)
else:
    print(0)
"""
    assert run(src) == [0]


def test_while_loop():
    src = """
i = 0
total = 0
while i < 5:
    total = total + i
    i = i + 1
print(total)
"""
    assert run(src) == [10]


def test_for_range():
    src = """
total = 0
for i in range(5):
    total = total + i
print(total)
"""
    assert run(src) == [10]


def test_for_range_start_stop():
    src = """
total = 0
for i in range(2, 6):
    total = total + i
print(total)
"""
    assert run(src) == [14]


def test_multiple_prints():
    src = """
print(1)
print(2)
print(3)
"""
    assert run(src) == [1, 2, 3]


def test_input():
    src = """
x = input()
print(x + 1)
"""
    assert run(src, inputs=[41]) == [42]


def test_augassign():
    src = """
x = 10
x += 5
print(x)
"""
    assert run(src) == [15]


def test_comparison_eq():
    assert run("print(3 == 3)") == [1]
    assert run("print(3 == 4)") == [0]


def test_schema_variables():
    _, schema = compile_source("x = 7\nprint(x)")
    assert "x" in schema.variables


def test_undefined_variable():
    with pytest.raises(CompileError, match="Undefined"):
        compile_source("print(z)")


def test_unsupported_float():
    with pytest.raises(CompileError):
        compile_source("print(3.14)")
