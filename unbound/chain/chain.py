"""
Unbound Chain

Maintains the blockchain and coordinates with the ledger to
release UBD rewards when blocks are appended.
"""

import time
from typing import List, Optional

from .block import Block, ChunkProof
from ..ledger.ledger import Ledger


class ChainError(Exception):
    pass


class Chain:
    CHUNK_REWARD = 10  # UBD per completed chunk (fixed for prototype)

    def __init__(self, ledger: Ledger):
        self._ledger = ledger
        self._blocks: List[Block] = []
        self._pending_proofs: List[ChunkProof] = []
        self._append_genesis()

    def _append_genesis(self):
        genesis = Block(
            index=0,
            prev_hash="0" * 64,
            timestamp=time.time(),
            proofs=[],
            rewards={},
        ).finalize()
        # Genesis is the only empty block allowed
        genesis.block_hash = genesis.compute_hash()
        self._blocks.append(genesis)

    @property
    def tip(self) -> Block:
        return self._blocks[-1]

    @property
    def height(self) -> int:
        return len(self._blocks) - 1

    def add_proof(self, proof: ChunkProof):
        """Queue a chunk completion proof to be included in the next block."""
        self._pending_proofs.append(proof)

    def commit_block(self) -> Optional[Block]:
        """
        Bundle pending proofs into a new block and append to chain.
        Releases UBD rewards to miners via the ledger.
        Returns None if there are no pending proofs.
        """
        if not self._pending_proofs:
            return None

        proofs = list(self._pending_proofs)
        self._pending_proofs.clear()

        rewards: dict[str, int] = {}
        for p in proofs:
            rewards[p.miner_id] = rewards.get(p.miner_id, 0) + p.reward

        block = Block(
            index=self.tip.index + 1,
            prev_hash=self.tip.block_hash,
            timestamp=time.time(),
            proofs=proofs,
            rewards=rewards,
        ).finalize()

        if not block.is_valid(self.tip):
            raise ChainError("Block validation failed")

        # Release rewards via ledger escrow
        for proof in proofs:
            try:
                self._ledger.release_escrow(
                    escrow_id=proof.job_id,
                    to_addr=proof.miner_id,
                    amount=proof.reward,
                )
            except Exception as e:
                raise ChainError(f"Ledger release failed for {proof.chunk_id}: {e}")

        self._blocks.append(block)
        return block

    def get_block(self, index: int) -> Block:
        if index < 0 or index >= len(self._blocks):
            raise ChainError(f"Block index out of range: {index}")
        return self._blocks[index]

    def verify_chain(self) -> bool:
        """Re-verify every block hash and linkage."""
        for i, block in enumerate(self._blocks):
            prev = self._blocks[i - 1] if i > 0 else None
            if i == 0:
                continue  # genesis exempt from proof requirement
            if not block.is_valid(prev):
                return False
        return True
