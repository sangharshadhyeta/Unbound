"""
Tests for array/vector opcodes: ILOAD, ISTORE, VSUM, VDOT.

Covers:
  - Direct VM execution of ILOAD, ISTORE, VSUM, VDOT
  - Compiler support for list literals, subscript read/write,
    sum(), len(), dot()
  - LEB128 encode/decode round-trip for multi-immediate opcodes
  - AMP masking through array operations
"""

import pytest
from unbound.uvm.vm import UVM, VMError
from unbound.uvm.opcodes import (
    PUSH, OUTPUT, HALT,
    ILOAD, ISTORE, VSUM, VDOT,
    IMMEDIATE_COUNT,
)
from unbound.uvm.encoding import encode, decode
from unbound.compiler.compiler import compile_source, CompileError
from unbound.masking import AMPMasker

# ── VM: ILOAD ─────────────────────────────────────────────────────────────────

class TestVMILOAD:
    def test_basic_read(self):
        vm = UVM()
        memory = {10: 42, 11: 99}
        # push index 0, ILOAD base=10 → should read mem[10]=42
        stream = [PUSH, 0, ILOAD, 10, OUTPUT, HALT]
        assert vm.execute(stream, memory=memory) == [42]

    def test_offset_index(self):
        vm = UVM()
        memory = {5: 1, 6: 2, 7: 3}
        # push index 2, ILOAD base=5 → should read mem[7]=3
        stream = [PUSH, 2, ILOAD, 5, OUTPUT, HALT]
        assert vm.execute(stream, memory=memory) == [3]

    def test_missing_element_returns_zero(self):
        vm = UVM()
        stream = [PUSH, 3, ILOAD, 100, OUTPUT, HALT]
        assert vm.execute(stream) == [0]


# ── VM: ISTORE ────────────────────────────────────────────────────────────────

class TestVMISTORE:
    def test_basic_write(self):
        vm = UVM()
        # PUSH 99 (value), PUSH 0 (index), ISTORE base=20, then read back
        stream = [PUSH, 99, PUSH, 0, ISTORE, 20, PUSH, 0, ILOAD, 20, OUTPUT, HALT]
        assert vm.execute(stream) == [99]

    def test_write_at_offset(self):
        vm = UVM()
        stream = [PUSH, 55, PUSH, 2, ISTORE, 10, PUSH, 2, ILOAD, 10, OUTPUT, HALT]
        assert vm.execute(stream) == [55]

    def test_overwrite(self):
        vm = UVM()
        memory = {0: 1}
        stream = [PUSH, 7, PUSH, 0, ISTORE, 0, PUSH, 0, ILOAD, 0, OUTPUT, HALT]
        assert vm.execute(stream, memory=memory) == [7]


# ── VM: VSUM ──────────────────────────────────────────────────────────────────

class TestVMVSUM:
    def test_sum_three(self):
        vm = UVM()
        memory = {0: 10, 1: 20, 2: 30}
        stream = [VSUM, 0, 3, OUTPUT, HALT]
        assert vm.execute(stream, memory=memory) == [60]

    def test_sum_single(self):
        vm = UVM()
        memory = {5: 42}
        stream = [VSUM, 5, 1, OUTPUT, HALT]
        assert vm.execute(stream, memory=memory) == [42]

    def test_sum_with_zeros(self):
        vm = UVM()
        memory = {0: 1, 2: 3}  # mem[1] missing → 0
        stream = [VSUM, 0, 3, OUTPUT, HALT]
        assert vm.execute(stream, memory=memory) == [4]


# ── VM: VDOT ──────────────────────────────────────────────────────────────────

class TestVMVDOT:
    def test_dot_product(self):
        vm = UVM()
        # a = [1, 2, 3] at base 0; b = [4, 5, 6] at base 10
        memory = {0: 1, 1: 2, 2: 3, 10: 4, 11: 5, 12: 6}
        # dot = 1*4 + 2*5 + 3*6 = 4+10+18 = 32
        stream = [VDOT, 0, 10, 3, OUTPUT, HALT]
        assert vm.execute(stream, memory=memory) == [32]

    def test_dot_length_one(self):
        vm = UVM()
        memory = {0: 7, 10: 8}
        stream = [VDOT, 0, 10, 1, OUTPUT, HALT]
        assert vm.execute(stream, memory=memory) == [56]

    def test_dot_zeros(self):
        vm = UVM()
        memory = {0: 0, 1: 0, 10: 5, 11: 5}
        stream = [VDOT, 0, 10, 2, OUTPUT, HALT]
        assert vm.execute(stream, memory=memory) == [0]


# ── Encoding: multi-immediate ops ─────────────────────────────────────────────

class TestEncoding:
    def test_immediate_counts(self):
        assert IMMEDIATE_COUNT[ILOAD]  == 1
        assert IMMEDIATE_COUNT[ISTORE] == 1
        assert IMMEDIATE_COUNT[VSUM]   == 2
        assert IMMEDIATE_COUNT[VDOT]   == 3

    def test_vsum_roundtrip(self):
        stream = [VSUM, 5, 10, OUTPUT, HALT]
        assert decode(encode(stream)) == stream

    def test_vdot_roundtrip(self):
        stream = [VDOT, 0, 10, 3, OUTPUT, HALT]
        assert decode(encode(stream)) == stream

    def test_iload_roundtrip(self):
        stream = [PUSH, 2, ILOAD, 5, OUTPUT, HALT]
        assert decode(encode(stream)) == stream

    def test_istore_roundtrip(self):
        stream = [PUSH, 9, PUSH, 0, ISTORE, 3, HALT]
        assert decode(encode(stream)) == stream


# ── Compiler: list literals ───────────────────────────────────────────────────

class TestCompilerListLiteral:
    def test_list_literal_stored(self):
        stream, schema = compile_source("x = [10, 20, 30]")
        assert "x" in schema.list_vars
        base, length = schema.list_vars["x"]
        assert length == 3
        src = "x = [10, 20, 30]\nprint(x[0])\nprint(x[1])\nprint(x[2])"
        stream2, _ = compile_source(src)
        assert UVM().execute(stream2) == [10, 20, 30]

    def test_list_schema(self):
        _, schema = compile_source("xs = [1, 2, 3, 4, 5]")
        assert schema.list_vars["xs"][1] == 5


# ── Compiler: subscript read ──────────────────────────────────────────────────

class TestCompilerSubscriptRead:
    def test_literal_index(self):
        src = "x = [5, 6, 7]\nprint(x[0])\nprint(x[2])"
        stream, _ = compile_source(src)
        assert UVM().execute(stream) == [5, 7]

    def test_variable_index(self):
        src = "x = [10, 20, 30]\ni = 1\nprint(x[i])"
        stream, _ = compile_source(src)
        assert UVM().execute(stream) == [20]

    def test_loop_read(self):
        src = (
            "x = [3, 1, 4, 1, 5]\n"
            "i = 0\n"
            "while i < 5:\n"
            "    print(x[i])\n"
            "    i = i + 1\n"
        )
        stream, _ = compile_source(src)
        assert UVM().execute(stream) == [3, 1, 4, 1, 5]


# ── Compiler: subscript write ─────────────────────────────────────────────────

class TestCompilerSubscriptWrite:
    def test_write_and_read_back(self):
        src = "x = [0, 0, 0]\nx[1] = 99\nprint(x[1])"
        stream, _ = compile_source(src)
        assert UVM().execute(stream) == [99]

    def test_write_with_variable_index(self):
        src = "x = [0, 0, 0]\ni = 2\nx[i] = 77\nprint(x[i])"
        stream, _ = compile_source(src)
        assert UVM().execute(stream) == [77]


# ── Compiler: sum() ───────────────────────────────────────────────────────────

class TestCompilerSum:
    def test_sum_list(self):
        src = "x = [1, 2, 3, 4, 5]\nprint(sum(x))"
        stream, _ = compile_source(src)
        assert UVM().execute(stream) == [15]

    def test_sum_single(self):
        src = "x = [42]\nprint(sum(x))"
        stream, _ = compile_source(src)
        assert UVM().execute(stream) == [42]

    def test_sum_after_write(self):
        src = "x = [1, 1, 1]\nx[0] = 10\nprint(sum(x))"
        stream, _ = compile_source(src)
        assert UVM().execute(stream) == [12]


# ── Compiler: len() ───────────────────────────────────────────────────────────

class TestCompilerLen:
    def test_len(self):
        src = "x = [1, 2, 3]\nprint(len(x))"
        stream, _ = compile_source(src)
        assert UVM().execute(stream) == [3]

    def test_len_five(self):
        src = "x = [0, 0, 0, 0, 0]\nprint(len(x))"
        stream, _ = compile_source(src)
        assert UVM().execute(stream) == [5]


# ── Compiler: dot() ───────────────────────────────────────────────────────────

class TestCompilerDot:
    def test_dot_product(self):
        # [1,2,3] · [4,5,6] = 4+10+18 = 32
        src = "a = [1, 2, 3]\nb = [4, 5, 6]\nprint(dot(a, b))"
        stream, _ = compile_source(src)
        assert UVM().execute(stream) == [32]

    def test_dot_identity(self):
        src = "a = [3, 4]\nb = [1, 0]\nprint(dot(a, b))"
        stream, _ = compile_source(src)
        assert UVM().execute(stream) == [3]

    def test_dot_length_mismatch_error(self):
        src = "a = [1, 2]\nb = [1, 2, 3]\nprint(dot(a, b))"
        with pytest.raises(CompileError, match="equal-length"):
            compile_source(src)


# ── Compiler: error cases ─────────────────────────────────────────────────────

class TestCompilerErrors:
    def test_undefined_list_subscript(self):
        with pytest.raises(CompileError):
            compile_source("print(x[0])")

    def test_empty_list_error(self):
        with pytest.raises(CompileError):
            compile_source("x = []")


# ── AMP masking through array operations ──────────────────────────────────────

class TestMaskingArrays:
    def _masker(self):
        return AMPMasker(b"test-key-arrays-16b")

    def test_vsum_masked(self):
        # sum of INPUT values via VSUM
        src = (
            "x = [0, 0, 0]\n"
            "x[0] = input()\n"
            "x[1] = input()\n"
            "x[2] = input()\n"
            "print(sum(x))\n"
        )
        stream, _ = compile_source(src)
        masker = self._masker()
        inputs = [10, 20, 30]
        plan = masker.prepare(stream, inputs, job_id="vsum-test")

        vm = UVM()
        masked_out = vm.execute(stream, inputs=plan.masked_inputs)
        real_out = plan.correct(masked_out)
        assert real_out == [60]

    def test_vdot_masked(self):
        # dot product of two INPUT-filled arrays
        src = (
            "a = [0, 0]\n"
            "b = [0, 0]\n"
            "a[0] = input()\n"
            "a[1] = input()\n"
            "b[0] = input()\n"
            "b[1] = input()\n"
            "print(dot(a, b))\n"
        )
        stream, _ = compile_source(src)
        masker = self._masker()
        # [3, 4] · [2, 1] = 6 + 4 = 10
        inputs = [3, 4, 2, 1]
        plan = masker.prepare(stream, inputs, job_id="vdot-test")

        vm = UVM()
        masked_out = vm.execute(stream, inputs=plan.masked_inputs)
        real_out = plan.correct(masked_out)
        assert real_out == [10]

    def test_iload_masked(self):
        src = (
            "x = [0, 0]\n"
            "x[0] = input()\n"
            "x[1] = input()\n"
            "print(x[0])\n"
            "print(x[1])\n"
        )
        stream, _ = compile_source(src)
        masker = self._masker()
        inputs = [7, 13]
        plan = masker.prepare(stream, inputs, job_id="iload-test")

        vm = UVM()
        masked_out = vm.execute(stream, inputs=plan.masked_inputs)
        real_out = plan.correct(masked_out)
        assert real_out == [7, 13]

    def test_masked_index_raises(self):
        """Indexing by a masked (INPUT) value must be rejected."""
        from unbound.masking.mask_compiler import MaskCompiler, MaskError
        from unbound.masking.key_deriver import KeyDeriver
        # PUSH 5 (value), INPUT (masked index), ISTORE base=0
        stream = [PUSH, 5, 1, ISTORE, 0, HALT]  # 1 = INPUT opcode
        from unbound.uvm.opcodes import INPUT as INPUT_OP
        stream = [PUSH, 5, INPUT_OP, ISTORE, 0, HALT]
        deriver = KeyDeriver(b"test-key-arrays-16b", "job", 2**255 - 19)
        with pytest.raises(MaskError, match="masked index"):
            MaskCompiler().compile(stream, [42], deriver)
