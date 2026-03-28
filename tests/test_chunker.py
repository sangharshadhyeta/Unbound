"""Tests for stream chunking and chunk re-execution."""

from unbound.compiler.compiler import compile_source
from unbound.compiler.chunker import split_stream
from unbound.uvm.vm import UVM

vm = UVM()


def test_chunks_cover_full_stream():
    src = "total = 0\nfor i in range(10):\n    total = total + i\nprint(total)"
    stream, schema = compile_source(src)
    chunks = split_stream("job1", stream, chunk_size=8)
    reassembled = []
    for c in chunks:
        reassembled.extend(c.stream)
    assert reassembled == stream


def test_full_stream_executes_correctly():
    src = "total = 0\nfor i in range(5):\n    total = total + i\nprint(total)"
    stream, _ = compile_source(src)
    result = vm.execute(stream)
    assert result == [10]


def test_chunk_count():
    src = "print(1)\nprint(2)\nprint(3)\nprint(4)\nprint(5)"
    stream, _ = compile_source(src)
    chunks = split_stream("job2", stream, chunk_size=4)
    assert len(chunks) >= 2
    total = sum(len(c.stream) for c in chunks)
    assert total == len(stream)


def test_chunk_ids():
    stream = [1, 2, 99]  # PUSH 2 HALT
    chunks = split_stream("myjob", stream, chunk_size=1)
    for i, c in enumerate(chunks):
        assert c.chunk_id == f"myjob:{i}"
        assert c.job_id == "myjob"
        assert c.index == i
        assert c.total == len(chunks)
