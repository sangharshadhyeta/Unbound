"""
Unbound Virtual Machine (UVM)

Executes a flat array of integers as a program.
Identical runtime on both user side and miner side.
The miner sees only numbers — semantic meaning lives in the schema (user only).
"""

from typing import List, Union
from .opcodes import (
    PUSH, POP, DUP, SWAP, LOAD, STORE,
    ADD, SUB, MUL, DIV, MOD, NEG,
    EQ, NEQ, LT, LTE, GT, GTE,
    AND, OR, NOT, XOR, SHL, SHR,
    JMP, JT, JF,
    INPUT, OUTPUT, HALT,
    HAS_IMMEDIATE,
)


class VMError(Exception):
    pass


class UVM:
    """
    Stack-based virtual machine operating on integer number streams.

    stream  : flat list of integers encoding opcodes + operands
    inputs  : list of integers available to INPUT instructions
    memory  : addressable integer memory (dict, sparse)
    stack   : operand stack
    outputs : results produced by OUTPUT instructions
    """

    MAX_STEPS = 1_000_000  # prevent infinite loops

    def __init__(self, memory_size: int = 1024):
        self.memory_size = memory_size

    def execute(
        self,
        stream: Union[List[int], bytes],
        inputs: List[int] | None = None,
        memory: dict[int, int] | None = None,
    ) -> List[int]:
        """
        Run a number stream and return the output number list.

        stream  — the flat integer program (opcodes + data intermixed),
                  or a bytes object (binary-encoded program)
        inputs  — values consumed by INPUT instructions
        memory  — pre-seeded memory (e.g. for chunk continuations)
        """
        # Accept binary-encoded streams transparently
        if isinstance(stream, (bytes, bytearray)):
            from .encoding import decode
            stream = decode(stream)
        stack: List[int] = []
        mem: dict[int, int] = dict(memory) if memory else {}
        inp = list(inputs) if inputs else []
        out: List[int] = []
        ip = 0          # instruction pointer
        steps = 0

        n = len(stream)

        while ip < n:
            if steps >= self.MAX_STEPS:
                raise VMError(f"Execution exceeded {self.MAX_STEPS} steps")
            steps += 1

            op = stream[ip]
            ip += 1

            # ── Stack ──────────────────────────────────────────────
            if op == PUSH:
                val = stream[ip]; ip += 1
                stack.append(val)

            elif op == POP:
                self._pop(stack)

            elif op == DUP:
                stack.append(self._peek(stack))

            elif op == SWAP:
                a = self._pop(stack)
                b = self._pop(stack)
                stack.append(a)
                stack.append(b)

            elif op == LOAD:
                addr = stream[ip]; ip += 1
                stack.append(mem.get(addr, 0))

            elif op == STORE:
                addr = stream[ip]; ip += 1
                mem[addr] = self._pop(stack)

            # ── Arithmetic ────────────────────────────────────────
            elif op == ADD:
                b = self._pop(stack); a = self._pop(stack)
                stack.append(a + b)

            elif op == SUB:
                b = self._pop(stack); a = self._pop(stack)
                stack.append(a - b)

            elif op == MUL:
                b = self._pop(stack); a = self._pop(stack)
                stack.append(a * b)

            elif op == DIV:
                b = self._pop(stack); a = self._pop(stack)
                if b == 0:
                    raise VMError("Division by zero")
                stack.append(a // b)

            elif op == MOD:
                b = self._pop(stack); a = self._pop(stack)
                if b == 0:
                    raise VMError("Modulo by zero")
                stack.append(a % b)

            elif op == NEG:
                stack.append(-self._pop(stack))

            # ── Comparison ────────────────────────────────────────
            elif op == EQ:
                b = self._pop(stack); a = self._pop(stack)
                stack.append(1 if a == b else 0)

            elif op == NEQ:
                b = self._pop(stack); a = self._pop(stack)
                stack.append(1 if a != b else 0)

            elif op == LT:
                b = self._pop(stack); a = self._pop(stack)
                stack.append(1 if a < b else 0)

            elif op == LTE:
                b = self._pop(stack); a = self._pop(stack)
                stack.append(1 if a <= b else 0)

            elif op == GT:
                b = self._pop(stack); a = self._pop(stack)
                stack.append(1 if a > b else 0)

            elif op == GTE:
                b = self._pop(stack); a = self._pop(stack)
                stack.append(1 if a >= b else 0)

            # ── Logic ─────────────────────────────────────────────
            elif op == AND:
                b = self._pop(stack); a = self._pop(stack)
                stack.append(a & b)

            elif op == OR:
                b = self._pop(stack); a = self._pop(stack)
                stack.append(a | b)

            elif op == NOT:
                stack.append(~self._pop(stack))

            elif op == XOR:
                b = self._pop(stack); a = self._pop(stack)
                stack.append(a ^ b)

            elif op == SHL:
                n_bits = self._pop(stack); a = self._pop(stack)
                stack.append(a << n_bits)

            elif op == SHR:
                n_bits = self._pop(stack); a = self._pop(stack)
                stack.append(a >> n_bits)

            # ── Control flow ──────────────────────────────────────
            elif op == JMP:
                offset = stream[ip]; ip += 1
                ip += offset

            elif op == JT:
                offset = stream[ip]; ip += 1
                cond = self._pop(stack)
                if cond != 0:
                    ip += offset

            elif op == JF:
                offset = stream[ip]; ip += 1
                cond = self._pop(stack)
                if cond == 0:
                    ip += offset

            # ── I/O ───────────────────────────────────────────────
            elif op == INPUT:
                if not inp:
                    raise VMError("INPUT: input buffer exhausted")
                stack.append(inp.pop(0))

            elif op == OUTPUT:
                out.append(self._pop(stack))

            elif op == HALT:
                break

            # Unknown opcode — treat as NOP (miner sees only numbers)
            # This is intentional: schema-less execution skips unknowns

        return out

    @staticmethod
    def _pop(stack: List[int]) -> int:
        if not stack:
            raise VMError("Stack underflow")
        return stack.pop()

    @staticmethod
    def _peek(stack: List[int]) -> int:
        if not stack:
            raise VMError("Stack underflow on peek")
        return stack[-1]
