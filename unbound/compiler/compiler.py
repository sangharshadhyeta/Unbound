"""
Unbound Compiler

Translates a Python subset AST into a flat UVM number stream.
Also produces a Schema — a user-private map that gives semantic meaning
to positions in the stream. Miners never receive the schema.

Supported Python subset:
  - Integer literals and variables
  - Arithmetic: + - * // % (unary -)
  - Comparisons: == != < <= > >=
  - Boolean logic: and, or, not
  - Assignment: x = expr
  - if / elif / else
  - while loops
  - print(expr)  → emits OUTPUT instruction
  - input()      → emits INPUT instruction
"""

import ast
from dataclasses import dataclass, field
from typing import List, Dict, Any

from ..uvm.opcodes import (
    PUSH, POP, DUP, SWAP, LOAD, STORE,
    ADD, SUB, MUL, DIV, MOD, NEG,
    EQ, NEQ, LT, LTE, GT, GTE,
    AND, OR, NOT, XOR,
    JMP, JT, JF,
    INPUT, OUTPUT, HALT,
)


@dataclass
class Schema:
    """
    User-private map giving semantic meaning to a compiled number stream.
    Never sent to miners.
    """
    # variable name → memory address
    variables: Dict[str, int] = field(default_factory=dict)
    # source line → stream position (for debugging)
    source_map: Dict[int, int] = field(default_factory=dict)
    # total stream length
    stream_length: int = 0
    # positions of OUTPUT instructions (where results appear)
    output_positions: List[int] = field(default_factory=list)


class CompileError(Exception):
    pass


class Compiler:
    def __init__(self):
        self._stream: List[int] = []
        self._schema = Schema()
        self._var_counter = 0
        self._vars: Dict[str, int] = {}  # name → memory addr

    def compile(self, source: str) -> tuple[List[int], Schema]:
        """
        Compile Python source to (stream, schema).
        stream — flat list of integers for the UVM
        schema — semantic map (user keeps this, never sent to miner)
        """
        self._stream = []
        self._schema = Schema()
        self._var_counter = 0
        self._vars = {}

        tree = ast.parse(source)
        for node in tree.body:
            self._compile_stmt(node)

        self._emit(HALT)

        self._schema.variables = dict(self._vars)
        self._schema.stream_length = len(self._stream)
        return list(self._stream), self._schema

    # ── Statements ───────────────────────────────────────────────────

    def _compile_stmt(self, node: ast.stmt):
        if isinstance(node, ast.Assign):
            self._compile_assign(node)
        elif isinstance(node, ast.Expr):
            self._compile_expr_stmt(node)
        elif isinstance(node, ast.If):
            self._compile_if(node)
        elif isinstance(node, ast.While):
            self._compile_while(node)
        elif isinstance(node, ast.For):
            self._compile_for(node)
        elif isinstance(node, ast.AugAssign):
            self._compile_augassign(node)
        elif isinstance(node, ast.Pass):
            pass
        else:
            raise CompileError(f"Unsupported statement: {type(node).__name__}")

    def _compile_assign(self, node: ast.Assign):
        if len(node.targets) != 1:
            raise CompileError("Only single-target assignment supported")
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            raise CompileError("Only simple variable assignment supported")
        self._compile_expr(node.value)
        addr = self._var_addr(target.id)
        self._emit(STORE, addr)

    def _compile_augassign(self, node: ast.AugAssign):
        if not isinstance(node.target, ast.Name):
            raise CompileError("Only simple augmented assignment supported")
        name = node.target.id
        addr = self._var_addr(name)
        self._emit(LOAD, addr)
        self._compile_expr(node.value)
        op_map = {
            ast.Add: ADD, ast.Sub: SUB, ast.Mult: MUL,
            ast.FloorDiv: DIV, ast.Mod: MOD,
        }
        op = op_map.get(type(node.op))
        if op is None:
            raise CompileError(f"Unsupported augmented operator: {type(node.op).__name__}")
        self._emit(op)
        self._emit(STORE, addr)

    def _compile_expr_stmt(self, node: ast.Expr):
        if isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Name) and call.func.id == "print":
                for arg in call.args:
                    self._compile_expr(arg)
                    pos = len(self._stream) - 1
                    self._schema.output_positions.append(pos)
                    self._emit(OUTPUT)
                return
        # Any other bare expression: evaluate and pop result
        self._compile_expr(node.value)
        self._emit(POP)

    def _compile_if(self, node: ast.If):
        self._compile_expr(node.test)

        # JF to else/end
        jf_pos = len(self._stream)
        self._emit(JF, 0)  # placeholder offset

        for stmt in node.body:
            self._compile_stmt(stmt)

        if node.orelse:
            # JMP over else block
            jmp_pos = len(self._stream)
            self._emit(JMP, 0)  # placeholder
            # patch JF to jump here (start of else)
            else_start = len(self._stream)
            self._patch_jump(jf_pos + 1, jf_pos + 2, else_start)

            for stmt in node.orelse:
                self._compile_stmt(stmt)

            # patch JMP to jump past else
            end = len(self._stream)
            self._patch_jump(jmp_pos + 1, jmp_pos + 2, end)
        else:
            end = len(self._stream)
            self._patch_jump(jf_pos + 1, jf_pos + 2, end)

    def _compile_while(self, node: ast.While):
        loop_start = len(self._stream)
        self._compile_expr(node.test)

        jf_pos = len(self._stream)
        self._emit(JF, 0)  # placeholder: jump past body

        for stmt in node.body:
            self._compile_stmt(stmt)

        # jump back to loop start
        back_offset = loop_start - (len(self._stream) + 2)
        self._emit(JMP, back_offset)

        loop_end = len(self._stream)
        self._patch_jump(jf_pos + 1, jf_pos + 2, loop_end)

    def _compile_for(self, node: ast.For):
        """Support: for x in range(n) and for x in range(start, stop)"""
        if not isinstance(node.target, ast.Name):
            raise CompileError("Only simple loop variable in for supported")
        if not (isinstance(node.iter, ast.Call) and
                isinstance(node.iter.func, ast.Name) and
                node.iter.func.id == "range"):
            raise CompileError("Only range() supported in for loops")

        args = node.iter.args
        if len(args) == 1:
            start_expr = ast.Constant(value=0)
            stop_expr = args[0]
        elif len(args) == 2:
            start_expr, stop_expr = args[0], args[1]
        else:
            raise CompileError("range() supports 1 or 2 arguments")

        var = node.target.id
        addr = self._var_addr(var)
        stop_addr = self._var_addr(f"__stop_{addr}")

        # init: var = start
        self._compile_expr(start_expr)
        self._emit(STORE, addr)

        # store stop value
        self._compile_expr(stop_expr)
        self._emit(STORE, stop_addr)

        # loop condition: var < stop
        loop_start = len(self._stream)
        self._emit(LOAD, addr)
        self._emit(LOAD, stop_addr)
        self._emit(LT)

        jf_pos = len(self._stream)
        self._emit(JF, 0)

        for stmt in node.body:
            self._compile_stmt(stmt)

        # var += 1
        self._emit(LOAD, addr)
        self._emit(PUSH, 1)
        self._emit(ADD)
        self._emit(STORE, addr)

        back_offset = loop_start - (len(self._stream) + 2)
        self._emit(JMP, back_offset)

        loop_end = len(self._stream)
        self._patch_jump(jf_pos + 1, jf_pos + 2, loop_end)

    # ── Expressions ──────────────────────────────────────────────────

    def _compile_expr(self, node: ast.expr):
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, int):
                raise CompileError(f"Only integer constants supported, got {type(node.value)}")
            self._emit(PUSH, node.value)

        elif isinstance(node, ast.Name):
            if node.id not in self._vars:
                raise CompileError(f"Undefined variable: {node.id}")
            self._emit(LOAD, self._vars[node.id])

        elif isinstance(node, ast.BinOp):
            self._compile_expr(node.left)
            self._compile_expr(node.right)
            op_map = {
                ast.Add: ADD, ast.Sub: SUB, ast.Mult: MUL,
                ast.FloorDiv: DIV, ast.Mod: MOD,
            }
            op = op_map.get(type(node.op))
            if op is None:
                raise CompileError(f"Unsupported binary op: {type(node.op).__name__}")
            self._emit(op)

        elif isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.USub):
                self._compile_expr(node.operand)
                self._emit(NEG)
            elif isinstance(node.op, ast.Not):
                self._compile_expr(node.operand)
                self._emit(PUSH, 0)
                self._emit(EQ)
            else:
                raise CompileError(f"Unsupported unary op: {type(node.op).__name__}")

        elif isinstance(node, ast.Compare):
            if len(node.ops) != 1 or len(node.comparators) != 1:
                raise CompileError("Only simple comparisons supported")
            self._compile_expr(node.left)
            self._compile_expr(node.comparators[0])
            cmp_map = {
                ast.Eq: EQ, ast.NotEq: NEQ,
                ast.Lt: LT, ast.LtE: LTE,
                ast.Gt: GT, ast.GtE: GTE,
            }
            op = cmp_map.get(type(node.ops[0]))
            if op is None:
                raise CompileError(f"Unsupported comparison: {type(node.ops[0]).__name__}")
            self._emit(op)

        elif isinstance(node, ast.BoolOp):
            ops = node.values
            self._compile_expr(ops[0])
            for operand in ops[1:]:
                self._compile_expr(operand)
                if isinstance(node.op, ast.And):
                    self._emit(AND)
                else:
                    self._emit(OR)

        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "input":
                self._emit(INPUT)
            else:
                raise CompileError(f"Unsupported function call: {ast.unparse(node)}")

        else:
            raise CompileError(f"Unsupported expression: {type(node).__name__}")

    # ── Helpers ──────────────────────────────────────────────────────

    def _emit(self, *values: int):
        self._stream.extend(values)

    def _var_addr(self, name: str) -> int:
        if name not in self._vars:
            self._vars[name] = self._var_counter
            self._var_counter += 1
        return self._vars[name]

    def _patch_jump(self, offset_pos: int, after_instr: int, target: int):
        """Write a relative offset into the stream at offset_pos."""
        self._stream[offset_pos] = target - after_instr


def compile_source(source: str) -> tuple[List[int], Schema]:
    return Compiler().compile(source)
