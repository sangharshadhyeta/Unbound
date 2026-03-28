"""
Unbound Assembler (user-side only)

Collects chunk results (lists of output numbers) and reconstructs
the final decoded output using the Schema produced by the compiler.

The assembler is never shipped to miners.
"""

from typing import Dict, List, Any
from ..compiler.compiler import Schema


class AssemblerError(Exception):
    pass


class Assembler:
    def __init__(self, schema: Schema, total_chunks: int):
        self.schema = schema
        self.total_chunks = total_chunks
        self._results: Dict[int, List[int]] = {}  # chunk_index → output numbers

    def add_result(self, chunk_index: int, output: List[int]):
        """Record the output numbers from a completed chunk."""
        self._results[chunk_index] = output

    @property
    def is_complete(self) -> bool:
        return len(self._results) == self.total_chunks

    def assemble(self) -> List[int]:
        """
        Concatenate all chunk outputs in order.
        Returns the full output number list.
        """
        if not self.is_complete:
            missing = [
                i for i in range(self.total_chunks)
                if i not in self._results
            ]
            raise AssemblerError(f"Missing chunk results: {missing}")

        full_output: List[int] = []
        for i in range(self.total_chunks):
            full_output.extend(self._results[i])
        return full_output

    def decode(self) -> List[Any]:
        """
        Assemble and decode the raw output numbers back into
        human-readable values using the schema.

        For now, output numbers are returned as-is (integers).
        Future: schema can define float scaling, string encoding, etc.
        """
        return self.assemble()
