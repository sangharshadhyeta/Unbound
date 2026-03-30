"""
Tests for per-job chunk timeout in the Registry.

Verifies that:
  - chunk_timeout is stored per-job (not global)
  - timed-out chunks are reassigned when the timeout for that specific job fires
  - two concurrent jobs with different timeouts expire independently
  - chunks that have not timed out are not reassigned
  - the default timeout (35 s) is applied when none is specified
"""

import time
import pytest

from unbound.registry.registry import Registry, ChunkStatus, JobStatus
from unbound.uvm.opcodes import PUSH, OUTPUT, HALT


# ── Helper ────────────────────────────────────────────────────────────────────

STREAM = [PUSH, 1, OUTPUT, HALT]


def make_job(reg, timeout=35.0, miner="alice"):
    job = reg.create_job("sub", "test", [STREAM], 0, chunk_timeout=timeout)
    chunk = reg.next_available_chunk()
    reg.assign_chunk(chunk.chunk_id, miner)
    return job, chunk


# ── Timeout storage ───────────────────────────────────────────────────────────

class TestTimeoutStorage:
    def test_default_timeout(self):
        reg = Registry()
        job = reg.create_job("sub", "test", [STREAM], 0)
        assert job.chunk_timeout == Registry.DEFAULT_CHUNK_TIMEOUT

    def test_custom_timeout_stored(self):
        reg = Registry()
        job = reg.create_job("sub", "test", [STREAM], 0, chunk_timeout=120.0)
        assert job.chunk_timeout == 120.0

    def test_short_timeout_stored(self):
        reg = Registry()
        job = reg.create_job("sub", "test", [STREAM], 0, chunk_timeout=0.1)
        assert job.chunk_timeout == 0.1


# ── Reassignment after timeout ────────────────────────────────────────────────

class TestTimeoutReassignment:
    def test_expired_chunk_is_reassigned(self):
        """A chunk whose timeout has elapsed becomes PENDING again."""
        reg = Registry()
        job, chunk = make_job(reg, timeout=0.01)  # 10 ms timeout

        assert chunk.status == ChunkStatus.ASSIGNED
        time.sleep(0.05)  # wait longer than the timeout

        # Calling next_available_chunk triggers the expiry scan
        recovered = reg.next_available_chunk()
        assert recovered is not None
        assert recovered.chunk_id == chunk.chunk_id

    def test_chunk_not_yet_expired_stays_assigned(self):
        """A chunk well within its timeout must not be reassigned."""
        reg = Registry()
        _, chunk = make_job(reg, timeout=60.0)

        assert chunk.status == ChunkStatus.ASSIGNED
        # Immediately ask for another chunk — the assigned one should not appear
        result = reg.next_available_chunk()
        assert result is None  # no other chunks available

    def test_status_returns_to_pending_on_expiry(self):
        """Chunk status is PENDING (not ASSIGNED) after timeout expires."""
        reg = Registry()
        _, chunk = make_job(reg, timeout=0.01)
        time.sleep(0.05)

        # Trigger the expiry scan
        reg.next_available_chunk()
        assert chunk.status == ChunkStatus.PENDING
        assert chunk.assigned_miner is None
        assert chunk.assigned_at is None

    def test_completed_chunk_is_not_reassigned(self):
        """A completed chunk must never be returned, even if timeout has elapsed."""
        reg = Registry()
        _, chunk = make_job(reg, timeout=0.01)
        # Complete it before the timeout check
        reg.submit_result(chunk.chunk_id, "alice", [1])
        time.sleep(0.05)

        result = reg.next_available_chunk()
        assert result is None  # nothing to dispatch — chunk is COMPLETED


# ── Per-job independent timeouts ──────────────────────────────────────────────

class TestIndependentTimeouts:
    def test_short_timeout_job_expires_first(self):
        """
        Two jobs: one with a short timeout, one with a long timeout.
        After the short timeout elapses, only the short-timeout chunk
        is available for re-dispatch.
        """
        reg = Registry()

        # Job A: very short timeout
        job_a = reg.create_job("sub", "a", [STREAM], 0, chunk_timeout=0.01)
        chunk_a = reg.next_available_chunk()
        reg.assign_chunk(chunk_a.chunk_id, "miner1")

        # Job B: long timeout
        job_b = reg.create_job("sub", "b", [STREAM], 0, chunk_timeout=120.0)
        chunk_b = reg.next_available_chunk()
        reg.assign_chunk(chunk_b.chunk_id, "miner2")

        assert chunk_a.status == ChunkStatus.ASSIGNED
        assert chunk_b.status == ChunkStatus.ASSIGNED

        time.sleep(0.05)  # only job_a's timeout has elapsed

        recovered = reg.next_available_chunk()
        assert recovered is not None
        assert recovered.chunk_id == chunk_a.chunk_id  # job A's chunk re-dispatched
        assert chunk_b.status == ChunkStatus.ASSIGNED   # job B's chunk still held

    def test_both_jobs_expire_independently(self):
        """Two short-timeout jobs — both chunks become available after their timeouts."""
        reg = Registry()

        job_a = reg.create_job("sub", "a", [STREAM], 0, chunk_timeout=0.02)
        chunk_a = reg.next_available_chunk()
        reg.assign_chunk(chunk_a.chunk_id, "miner1")

        job_b = reg.create_job("sub", "b", [STREAM], 0, chunk_timeout=0.02)
        chunk_b = reg.next_available_chunk()
        reg.assign_chunk(chunk_b.chunk_id, "miner2")

        time.sleep(0.06)

        # Both should be available for re-dispatch
        first = reg.next_available_chunk()
        assert first is not None
        reg.assign_chunk(first.chunk_id, "miner3")

        second = reg.next_available_chunk()
        assert second is not None
        reg.assign_chunk(second.chunk_id, "miner4")

        ids = {first.chunk_id, second.chunk_id}
        assert chunk_a.chunk_id in ids
        assert chunk_b.chunk_id in ids

    def test_different_timeouts_each_job(self):
        """Verify chunk_timeout stored on each job is used, not a shared value."""
        reg = Registry()
        t1 = 0.05
        t2 = 120.0
        j1 = reg.create_job("sub", "j1", [STREAM], 0, chunk_timeout=t1)
        j2 = reg.create_job("sub", "j2", [STREAM], 0, chunk_timeout=t2)
        assert reg.get_job(j1.job_id).chunk_timeout == t1
        assert reg.get_job(j2.job_id).chunk_timeout == t2
        # They must be stored independently
        assert reg.get_job(j1.job_id).chunk_timeout != reg.get_job(j2.job_id).chunk_timeout


# ── Multi-attempt tracking ─────────────────────────────────────────────────────

class TestAttemptTracking:
    def test_attempts_increment_on_reassignment(self):
        """Each time a chunk is assigned, its attempt counter increments."""
        reg = Registry()
        _, chunk = make_job(reg, timeout=0.01)
        assert chunk.attempts == 1

        time.sleep(0.05)
        recovered = reg.next_available_chunk()
        reg.assign_chunk(recovered.chunk_id, "miner2")
        assert recovered.attempts == 2
