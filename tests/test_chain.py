"""Tests for the Unbound chain (PoUW consensus)."""

import pytest
from unbound.chain.block import Block, ChunkProof
from unbound.chain.chain import Chain, ChainError
from unbound.ledger.ledger import Ledger


def make_chain():
    ledger = Ledger()
    ledger.credit("submitter", 10_000, "genesis")
    ledger.lock_escrow("job1", "submitter", 5_000)
    return Chain(ledger), ledger


def test_genesis_block():
    chain, _ = make_chain()
    assert chain.height == 0
    assert chain.tip.index == 0


def test_commit_block_with_proof():
    chain, ledger = make_chain()
    proof = ChunkProof(
        chunk_id="job1:0",
        job_id="job1",
        miner_id="miner1",
        result_hash="abc123",
        reward=10,
    )
    chain.add_proof(proof)
    block = chain.commit_block()

    assert block is not None
    assert chain.height == 1
    assert len(block.proofs) == 1
    assert ledger.balance("miner1") == 10


def test_no_empty_block():
    chain, _ = make_chain()
    result = chain.commit_block()
    assert result is None
    assert chain.height == 0


def test_chain_integrity():
    chain, _ = make_chain()
    for i in range(3):
        chain.add_proof(ChunkProof(
            chunk_id=f"job1:{i}",
            job_id="job1",
            miner_id="miner1",
            result_hash=f"hash{i}",
            reward=10,
        ))
        chain.commit_block()
    assert chain.verify_chain()


def test_tamper_detection():
    chain, _ = make_chain()
    chain.add_proof(ChunkProof("job1:0", "job1", "miner1", "h", 10))
    chain.commit_block()
    # Tamper with the block
    chain._blocks[1].rewards["miner1"] = 9999
    assert not chain.verify_chain()


def test_multiple_miners_rewarded():
    chain, ledger = make_chain()
    for miner, i in [("miner1", 0), ("miner2", 1), ("miner1", 2)]:
        chain.add_proof(ChunkProof(f"job1:{i}", "job1", miner, f"h{i}", 10))
    chain.commit_block()
    assert ledger.balance("miner1") == 20
    assert ledger.balance("miner2") == 10
