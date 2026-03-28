"""Tests for binary stream encoding."""

from unbound.compiler.compiler import compile_source
from unbound.uvm.vm import UVM
from unbound.uvm.encoding import encode, decode, size_report

vm = UVM()


def test_encode_decode_roundtrip():
    src = "total = 0\nfor i in range(10):\n    total = total + i\nprint(total)"
    stream, _ = compile_source(src)
    binary = encode(stream)
    restored = decode(binary)
    assert restored == stream


def test_vm_accepts_bytes():
    src = "print(6 * 7)"
    stream, _ = compile_source(src)
    binary = encode(stream)
    result = vm.execute(binary)
    assert result == [42]


def test_leb128_smaller_than_json_and_fixed():
    programs = {
        "large literals": "x = 1000000\ny = 999999\nprint(x + y)",
        "small ints":     "total = 0\nfor i in range(100):\n    total = total + i\nprint(total)",
        "fibonacci":      "a=0\nb=1\ni=0\nwhile i<20:\n    print(a)\n    c=a+b\n    a=b\n    b=c\n    i=i+1",
    }
    for label, src in programs.items():
        stream, _ = compile_source(src)
        r = size_report(stream)
        print(f"\n{label}: {r}")
        assert r["leb128_bytes"] <= r["json_bytes"],  f"{label}: LEB128 should be ≤ JSON"
        assert r["leb128_bytes"] <= r["fixed_bytes"], f"{label}: LEB128 should be ≤ fixed"


def test_fibonacci_binary():
    src = """
a = 0
b = 1
i = 0
while i < 10:
    print(a)
    c = a + b
    a = b
    b = c
    i = i + 1
"""
    stream, _ = compile_source(src)
    binary = encode(stream)
    result = vm.execute(binary)
    assert result == [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]


def test_binary_is_bytes():
    stream, _ = compile_source("print(1)")
    assert isinstance(encode(stream), bytes)


def test_all_opcodes_fit_in_one_byte():
    from unbound.uvm.opcodes import OPCODE_NAMES
    for opcode in OPCODE_NAMES:
        assert 0 <= opcode <= 255, f"Opcode {opcode} doesn't fit in 1 byte"
