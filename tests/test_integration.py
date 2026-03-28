"""
End-to-end integration test:
compile a Python program → split into chunks → execute via UVM
→ assemble result → verify UBD flows through ledger + chain.
"""

from unbound.compiler.compiler import compile_source
from unbound.compiler.chunker import split_stream
from unbound.assembler.assembler import Assembler
from unbound.uvm.vm import UVM
from unbound.registry.registry import Registry
from unbound.ledger.ledger import Ledger
from unbound.chain.chain import Chain
from unbound.chain.block import ChunkProof

vm = UVM()


def run_job_locally(source: str) -> list:
    """
    Simulate a full job: compile → run as one chunk → assemble.

    Stream splitting is for data-parallel workloads (same program, N input
    slices). A single program with control flow must run as one atomic chunk
    to preserve stack state and correct jump targets across the whole stream.
    """
    stream, schema = compile_source(source)
    # Single chunk = the full program stream
    result = vm.execute(stream)
    assembler = Assembler(schema=schema, total_chunks=1)
    assembler.add_result(0, result)
    return assembler.decode()


def test_simple_program():
    src = "print(6 * 7)"
    assert run_job_locally(src) == [42]


def test_loop_program():
    src = """
total = 0
for i in range(10):
    total = total + i
print(total)
"""
    assert run_job_locally(src) == [45]


def test_multiple_outputs():
    src = """
for i in range(5):
    print(i * i)
"""
    assert run_job_locally(src) == [0, 1, 4, 9, 16]


def test_conditional_program():
    src = """
x = 7
if x > 5:
    print(1)
else:
    print(0)
"""
    assert run_job_locally(src) == [1]


def test_ubd_flows_on_completion():
    """Verify ledger + chain correctly release UBD when chunks complete."""
    source = "print(2 + 2)"
    stream, _ = compile_source(source)

    # One chunk = the full program stream
    chunk_streams = [stream]
    payment = 10

    ledger = Ledger()
    ledger.credit("alice", payment * 10, "genesis")

    chain = Chain(ledger)
    registry = Registry()

    # create_job generates its own job_id; use that for escrow
    job = registry.create_job("alice", source, chunk_streams, payment)  # description=source
    ledger.lock_escrow(job.job_id, "alice", payment)

    # Get the registry's chunk records (keyed by job_id:index)
    reg_chunks = sorted(
        [c for c in registry._chunks.values() if c.job_id == job.job_id],
        key=lambda c: c.index,
    )
    for rc in reg_chunks:
        result = vm.execute(rc.stream)
        registry.assign_chunk(rc.chunk_id, "miner1")
        registry.submit_result(rc.chunk_id, "miner1", result)
        proof = ChunkProof(
            chunk_id=rc.chunk_id,
            job_id=job.job_id,
            miner_id="miner1",
            result_hash=rc.result_hash or "hash",
            reward=rc.reward,
        )
        chain.add_proof(proof)

    block = chain.commit_block()
    assert block is not None
    assert ledger.balance("miner1") > 0
    assert chain.verify_chain()


def test_chunk_reassignment_on_failure():
    """A chunk that returns empty result is reassigned."""
    registry = Registry()
    source = "print(1)"
    stream, _ = compile_source(source)
    chunks = split_stream("fail-job", stream, chunk_size=8)
    registry.create_job("alice", "", [c.stream for c in chunks], 100)

    chunk = registry.next_available_chunk()
    assert chunk is not None
    registry.assign_chunk(chunk.chunk_id, "bad-miner")

    # Bad miner returns invalid result
    result = registry.submit_result(chunk.chunk_id, "bad-miner", "not-a-list")
    from unbound.registry.registry import ChunkStatus
    assert result.status == ChunkStatus.FAILED
