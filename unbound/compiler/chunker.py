"""
Chunker — splits a UVM number stream into micro-task chunks.

Each chunk is a self-contained sub-stream. The chunker ensures boundaries
fall only between complete instructions (never mid-opcode + immediate).
"""

from dataclasses import dataclass, field
from typing import List
from ..uvm.opcodes import IMMEDIATE_COUNT


@dataclass
class Chunk:
    chunk_id: str          # job_id:index
    job_id: str
    index: int             # position in chunk sequence
    total: int             # total chunks in this job
    stream: List[int]      # the number sub-stream
    input_keys: List[int]  # memory addresses this chunk reads as inputs
    output_keys: List[int] # memory addresses this chunk writes as outputs
    input_values: List[int] = field(default_factory=list)  # seeded at dispatch


def split_stream(
    job_id: str,
    stream: List[int],
    chunk_size: int = 256,
) -> List[Chunk]:
    """
    Split a flat stream into chunks of approximately chunk_size instructions.
    Boundaries respect instruction boundaries (opcode + optional immediate).
    """
    chunks = []
    boundaries = _instruction_boundaries(stream)

    # group boundaries into chunks
    groups: List[List[int]] = []
    current: List[int] = []
    count = 0

    for start, length in boundaries:
        current.append(start)
        count += length
        if count >= chunk_size:
            groups.append(current)
            current = []
            count = 0
    if current:
        groups.append(current)

    total = len(groups)
    for idx, group in enumerate(groups):
        starts = group
        end_idx = (
            groups[idx + 1][0] if idx + 1 < len(groups) else len(stream)
        )
        sub_stream = stream[starts[0]:end_idx]
        chunk = Chunk(
            chunk_id=f"{job_id}:{idx}",
            job_id=job_id,
            index=idx,
            total=total,
            stream=sub_stream,
            input_keys=[],
            output_keys=[],
        )
        chunks.append(chunk)

    return chunks


def _instruction_boundaries(stream: List[int]) -> List[tuple[int, int]]:
    """
    Return list of (start_pos, total_length) for each instruction.
    Instructions are 1 integer for the opcode + 1 integer for each immediate.
    """
    boundaries = []
    i = 0
    n = len(stream)
    while i < n:
        op = stream[i]
        length = 1 + IMMEDIATE_COUNT.get(op, 0)
        boundaries.append((i, length))
        i += length
    return boundaries
