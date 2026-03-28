"""
Unbound Block

A block is a batch of completed chunk proofs.
Proof of Useful Work: blocks are valid only when they contain
verified chunk completions — no empty blocks, no hash puzzles.
"""

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class ChunkProof:
    """Records that a miner completed a specific chunk."""
    chunk_id: str       # job_id:index
    job_id: str
    miner_id: str
    result_hash: str    # SHA-256 of the output number list
    reward: int         # UBD paid to miner


@dataclass
class Block:
    index: int
    prev_hash: str
    timestamp: float
    proofs: List[ChunkProof]
    rewards: Dict[str, int]   # miner_id → total UBD earned in this block
    block_hash: str = ""

    def compute_hash(self) -> str:
        payload = {
            "index": self.index,
            "prev_hash": self.prev_hash,
            "timestamp": self.timestamp,
            "proofs": [
                {
                    "chunk_id": p.chunk_id,
                    "miner_id": p.miner_id,
                    "result_hash": p.result_hash,
                    "reward": p.reward,
                }
                for p in self.proofs
            ],
            "rewards": self.rewards,
        }
        raw = json.dumps(payload, sort_keys=True).encode()
        return hashlib.sha256(raw).hexdigest()

    def finalize(self) -> "Block":
        self.block_hash = self.compute_hash()
        return self

    def is_valid(self, prev_block: "Block | None") -> bool:
        if self.block_hash != self.compute_hash():
            return False
        expected_prev = prev_block.block_hash if prev_block else "0" * 64
        if self.prev_hash != expected_prev:
            return False
        if not self.proofs:
            return False  # no empty blocks
        return True
