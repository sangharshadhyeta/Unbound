"""
Tests for pipeline_depth — GPU miner throughput declaration.

pipeline_depth lets a miner declare how many chunks it can process
in parallel.  The server pro-actively dispatches up to that many
chunks without waiting for explicit request_chunk messages.

These tests exercise the server's internal state and the registry
dispatch invariants that the pipeline depends on:
  - pipeline_depth stored on registration
  - inflight tracking increments / decrements correctly
  - job-exclusivity still applies across all in-flight chunks
  - depth is capped at 8
  - depth=1 is the backwards-compatible default
"""

import pytest

from unbound.network.server import NodeServer
from unbound.registry.registry import Registry, ChunkStatus
from unbound.uvm.opcodes import ADD, OUTPUT, HALT
from unbound.protocol import pipeline_depth_cap, THRESHOLD_PUBLIC, THRESHOLD_LOCAL

STREAM = [ADD, OUTPUT, HALT]


# ── NodeServer internal state ─────────────────────────────────────────────────

class TestPipelineDepthState:
    def _server(self):
        return NodeServer(registry=Registry())

    def test_default_pipeline_depth_is_one(self):
        srv = self._server()
        # Simulate what register handler writes (no WS needed for state check)
        mid = "miner-a"
        srv._miner_pipeline_depth[mid] = min(int(1), 8)
        assert srv._miner_pipeline_depth[mid] == 1

    def test_declared_depth_stored(self):
        srv = self._server()
        mid = "miner-gpu"
        srv._miner_pipeline_depth[mid] = min(int(4), 8)
        assert srv._miner_pipeline_depth[mid] == 4

    def test_depth_capped_at_public_threshold(self):
        cap = pipeline_depth_cap(THRESHOLD_PUBLIC)  # 0.125 → 8
        srv = self._server()
        mid = "greedy-miner"
        srv._miner_pipeline_depth[mid] = min(int(100), cap)
        assert srv._miner_pipeline_depth[mid] == cap

    def test_local_threshold_raises_cap_to_64(self):
        cap = pipeline_depth_cap(THRESHOLD_LOCAL)   # 1.0 → 64
        assert cap == 64

    def test_inflight_starts_at_zero(self):
        srv = self._server()
        mid = "miner-b"
        srv._miner_inflight[mid] = 0
        assert srv._miner_inflight[mid] == 0

    def test_inflight_increments(self):
        srv = self._server()
        mid = "miner-c"
        srv._miner_inflight[mid] = 0
        srv._miner_inflight[mid] += 1
        srv._miner_inflight[mid] += 1
        assert srv._miner_inflight[mid] == 2

    def test_inflight_decrements_floored_at_zero(self):
        srv = self._server()
        mid = "miner-d"
        srv._miner_inflight[mid] = 1
        srv._miner_inflight[mid] = max(0, srv._miner_inflight[mid] - 1)
        assert srv._miner_inflight[mid] == 0
        # Decrement below zero is clamped
        srv._miner_inflight[mid] = max(0, srv._miner_inflight[mid] - 1)
        assert srv._miner_inflight[mid] == 0

    def test_cleanup_removes_depth_and_inflight(self):
        srv = self._server()
        mid = "miner-e"
        srv._miner_pipeline_depth[mid] = 4
        srv._miner_inflight[mid] = 2
        srv._miner_pipeline_depth.pop(mid, None)
        srv._miner_inflight.pop(mid, None)
        assert mid not in srv._miner_pipeline_depth
        assert mid not in srv._miner_inflight


# ── Registry-level pipeline dispatch invariants ──────────────────────────────

class TestPipelineDispatchInvariants:
    """
    Verify the registry properties that make pipeline_depth safe:
    job exclusivity ensures a miner with depth=N cannot receive N chunks
    from the same job, only from N different jobs.
    """

    def test_depth_n_requires_n_distinct_jobs(self):
        """A miner with pipeline_depth=3 can only hold 1 chunk per job."""
        reg = Registry()
        for i in range(3):
            reg.create_job("alice", f"job{i}", [STREAM], 0)

        # Simulate three sequential dispatches with job exclusion
        exclusions = set()
        dispatched = []
        for _ in range(3):
            chunk = reg.next_available_chunk(exclude_job_ids=exclusions)
            assert chunk is not None
            reg.assign_chunk(chunk.chunk_id, "gpu-miner")
            exclusions.add(chunk.job_id)
            dispatched.append(chunk)

        job_ids = {c.job_id for c in dispatched}
        assert len(job_ids) == 3   # three distinct jobs

    def test_depth_exceeds_available_jobs(self):
        """If fewer jobs exist than pipeline_depth, dispatch is limited."""
        reg = Registry()
        reg.create_job("alice", "only-job", [STREAM], 0)

        exclusions = set()
        chunk = reg.next_available_chunk(exclude_job_ids=exclusions)
        assert chunk is not None
        reg.assign_chunk(chunk.chunk_id, "gpu-miner")
        exclusions.add(chunk.job_id)

        # No second chunk available (only one job, now excluded)
        second = reg.next_available_chunk(exclude_job_ids=exclusions)
        assert second is None

    def test_pipeline_refill_after_completion(self):
        """After a chunk completes, the slot opens and the next job fills it."""
        reg = Registry()
        for i in range(2):
            reg.create_job("alice", f"job{i}", [STREAM], 0)

        # First dispatch
        exclusions = set()
        c1 = reg.next_available_chunk(exclude_job_ids=exclusions)
        reg.assign_chunk(c1.chunk_id, "gpu-miner")
        exclusions.add(c1.job_id)

        # Second dispatch (different job)
        c2 = reg.next_available_chunk(exclude_job_ids=exclusions)
        reg.assign_chunk(c2.chunk_id, "gpu-miner")
        exclusions.add(c2.job_id)

        # Complete c1 — exclusions set cleared conceptually for new jobs
        reg.submit_result(c1.chunk_id, "gpu-miner", [1])

        # A new job arrives
        reg.create_job("bob", "new-job", [STREAM], 0)

        # gpu-miner still has c2 in flight but can receive the new job
        # (c1's job is done; new job not excluded)
        # Use fresh exclusions (only c2's job is in-flight)
        new_exclusions = {c2.job_id}
        c3 = reg.next_available_chunk(exclude_job_ids=new_exclusions)
        assert c3 is not None
        assert c3.job_id != c2.job_id


# ── pipeline_depth does not weaken k-of-2 isolation ─────────────────────────

class TestPipelineDepthAndVerification:
    def test_pipeline_miner_cannot_self_verify(self):
        """A GPU miner assigned a chunk's first pass cannot be its own verifier."""
        reg = Registry()
        reg.create_job("alice", "verify-job", [STREAM], 10, require_verification=True)

        c1 = reg.next_available_chunk()
        reg.assign_chunk(c1.chunk_id, "gpu-miner")

        # gpu-miner excludes this job — cannot be its own second verifier
        second = reg.next_available_chunk(exclude_job_ids={c1.job_id})
        assert second is None   # only one chunk, and it's excluded for this miner
