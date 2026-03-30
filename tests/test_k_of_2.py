"""
Tests for k-of-2 agreement: two independent miners must return identical
results before a chunk is marked COMPLETED.

Covers:
  - Single-miner mode (require_verification=False): chunk completes immediately
  - k-of-2 mode: first result stores but does not complete
  - k-of-2 mode: agreement → COMPLETED
  - k-of-2 mode: disagreement → FAILED (server can slash and reassign)
  - Second miner eligibility: job exclusion prevents same miner verifying itself
  - Pass-4 dispatch: ASSIGNED chunks offered to second verifier
  - Float tolerance agreement (epsilon)
  - results_agree() function directly
"""

import pytest

from unbound.registry.registry import Registry, ChunkStatus, JobStatus
from unbound.uvm.opcodes import PUSH, OUTPUT, HALT
from unbound.verifier.verifier import results_agree


STREAM = [PUSH, 1, OUTPUT, HALT]


# ── results_agree ─────────────────────────────────────────────────────────────

class TestResultsAgree:
    def test_equal_integers(self):
        assert results_agree([1, 2, 3], [1, 2, 3])

    def test_unequal_integers(self):
        assert not results_agree([1, 2, 3], [1, 2, 4])

    def test_different_lengths(self):
        assert not results_agree([1, 2], [1, 2, 3])

    def test_empty_lists_agree(self):
        assert results_agree([], [])

    def test_float_mode_within_tolerance(self):
        assert results_agree([1.000001], [1.000002], float_mode=True, epsilon=1e-4)

    def test_float_mode_abs_floor(self):
        # Very small values — rel_tol of 0 still passes through 1e-9 abs floor
        assert results_agree([0.0], [0.0], float_mode=True, epsilon=0.0)

    def test_float_mode_outside_tolerance(self):
        assert not results_agree([1.0], [1.1], float_mode=True, epsilon=1e-4)

    def test_integer_mode_ignores_epsilon(self):
        assert not results_agree([1], [2], float_mode=False, epsilon=100.0)


# ── Single-miner mode (no verification) ──────────────────────────────────────

class TestSingleMinerMode:
    def test_completes_on_first_result(self):
        reg = Registry()
        job = reg.create_job("sub", "test", [STREAM], 0, require_verification=False)
        chunk = reg.next_available_chunk()
        reg.assign_chunk(chunk.chunk_id, "miner1")
        chunk = reg.submit_result(chunk.chunk_id, "miner1", [1])
        assert chunk.status == ChunkStatus.COMPLETED

    def test_default_no_verification(self):
        """create_job without require_verification defaults to False."""
        reg = Registry()
        job = reg.create_job("sub", "test", [STREAM], 0)
        assert not job.require_verification


# ── k-of-2 first submission ───────────────────────────────────────────────────

class TestKOf2FirstSubmission:
    def _setup(self):
        reg = Registry()
        reg.create_job("sub", "test", [STREAM], 10, require_verification=True)
        chunk = reg.next_available_chunk()
        reg.assign_chunk(chunk.chunk_id, "miner1")
        return reg, chunk

    def test_first_result_stays_assigned(self):
        reg, chunk = self._setup()
        chunk = reg.submit_result(chunk.chunk_id, "miner1", [1])
        assert chunk.status == ChunkStatus.ASSIGNED

    def test_first_result_stored(self):
        reg, chunk = self._setup()
        chunk = reg.submit_result(chunk.chunk_id, "miner1", [42])
        assert chunk.result == [42]
        assert chunk.first_result_pending is True

    def test_job_not_completed_after_first(self):
        reg, chunk = self._setup()
        reg.submit_result(chunk.chunk_id, "miner1", [1])
        job = reg.get_job(chunk.job_id)
        assert job.status == JobStatus.RUNNING


# ── k-of-2 second assignment (Pass 4) ────────────────────────────────────────

class TestKOf2SecondAssignment:
    def _setup_with_first_miner(self):
        reg = Registry()
        reg.create_job("sub", "test", [STREAM], 10, require_verification=True)
        chunk = reg.next_available_chunk()
        reg.assign_chunk(chunk.chunk_id, "miner1")
        return reg, chunk

    def test_second_miner_offered_chunk(self):
        reg, chunk = self._setup_with_first_miner()
        # miner2 has not excluded this job — should receive it as second verifier
        second = reg.next_available_chunk()
        assert second is not None
        assert second.chunk_id == chunk.chunk_id

    def test_second_miner_assigned(self):
        reg, chunk = self._setup_with_first_miner()
        second = reg.next_available_chunk()
        reg.assign_chunk(second.chunk_id, "miner2")
        assert chunk.second_miner == "miner2"

    def test_same_miner_cannot_be_second(self):
        """Job exclusion prevents miner1 from also being the second verifier."""
        reg, chunk = self._setup_with_first_miner()
        # miner1 already has this job excluded — should not receive it again
        second = reg.next_available_chunk(exclude_job_ids={chunk.job_id})
        assert second is None  # no other chunks, and miner1 is excluded


# ── k-of-2 agreement → COMPLETED ─────────────────────────────────────────────

class TestKOf2Agreement:
    def _setup_two_miners(self):
        reg = Registry()
        reg.create_job("sub", "test", [STREAM], 10, require_verification=True)
        chunk = reg.next_available_chunk()
        reg.assign_chunk(chunk.chunk_id, "miner1")
        second = reg.next_available_chunk()
        reg.assign_chunk(second.chunk_id, "miner2")
        return reg, chunk

    def test_agreement_completes_chunk(self):
        reg, chunk = self._setup_two_miners()
        reg.submit_result(chunk.chunk_id, "miner1", [42])
        chunk = reg.submit_result(chunk.chunk_id, "miner2", [42])
        assert chunk.status == ChunkStatus.COMPLETED

    def test_job_completed_after_agreement(self):
        reg, chunk = self._setup_two_miners()
        reg.submit_result(chunk.chunk_id, "miner1", [1])
        reg.submit_result(chunk.chunk_id, "miner2", [1])
        job = reg.get_job(chunk.job_id)
        assert job.status == JobStatus.COMPLETED

    def test_result_preserved_on_completion(self):
        reg, chunk = self._setup_two_miners()
        reg.submit_result(chunk.chunk_id, "miner1", [99])
        chunk = reg.submit_result(chunk.chunk_id, "miner2", [99])
        assert chunk.result == [99]

    def test_multi_value_agreement(self):
        reg, chunk = self._setup_two_miners()
        reg.submit_result(chunk.chunk_id, "miner1", [1, 2, 3])
        chunk = reg.submit_result(chunk.chunk_id, "miner2", [1, 2, 3])
        assert chunk.status == ChunkStatus.COMPLETED


# ── k-of-2 disagreement → FAILED ─────────────────────────────────────────────

class TestKOf2Disagreement:
    def _setup_two_miners(self):
        reg = Registry()
        reg.create_job("sub", "test", [STREAM], 10, require_verification=True)
        chunk = reg.next_available_chunk()
        reg.assign_chunk(chunk.chunk_id, "miner1")
        second = reg.next_available_chunk()
        reg.assign_chunk(second.chunk_id, "miner2")
        return reg, chunk

    def test_disagreement_marks_failed(self):
        reg, chunk = self._setup_two_miners()
        reg.submit_result(chunk.chunk_id, "miner1", [1])
        chunk = reg.submit_result(chunk.chunk_id, "miner2", [2])
        assert chunk.status == ChunkStatus.FAILED

    def test_disagreed_miners_preserved_in_failed_state(self):
        """Server needs to know which miners to slash."""
        reg, chunk = self._setup_two_miners()
        reg.submit_result(chunk.chunk_id, "miner1", [1])
        chunk = reg.submit_result(chunk.chunk_id, "miner2", [9])
        assert chunk.assigned_miner == "miner1"
        assert chunk.second_miner   == "miner2"

    def test_job_not_completed_on_disagreement(self):
        reg, chunk = self._setup_two_miners()
        reg.submit_result(chunk.chunk_id, "miner1", [1])
        reg.submit_result(chunk.chunk_id, "miner2", [2])
        job = reg.get_job(chunk.job_id)
        assert job.status == JobStatus.RUNNING

    def test_disagreement_then_requeue_and_complete(self):
        """After server resets a FAILED chunk, it can complete with fresh miners."""
        reg, chunk = self._setup_two_miners()
        reg.submit_result(chunk.chunk_id, "miner1", [1])
        chunk = reg.submit_result(chunk.chunk_id, "miner2", [9])
        assert chunk.status == ChunkStatus.FAILED

        # Simulate server resetting chunk after slashing
        chunk.status = ChunkStatus.PENDING
        chunk.assigned_miner = None
        chunk.second_miner   = None
        chunk.assigned_at    = None
        chunk.result         = None
        chunk.result_hash    = None
        chunk.first_result_pending = False

        # Fresh dispatch — new miners agree
        c1 = reg.next_available_chunk()
        reg.assign_chunk(c1.chunk_id, "miner3")
        c2 = reg.next_available_chunk()
        reg.assign_chunk(c2.chunk_id, "miner4")
        reg.submit_result(c1.chunk_id, "miner3", [7])
        final = reg.submit_result(c1.chunk_id, "miner4", [7])
        assert final.status == ChunkStatus.COMPLETED


# ── k-of-2 with float tolerance ──────────────────────────────────────────────

class TestKOf2FloatTolerance:
    def test_float_agreement_within_epsilon(self):
        reg = Registry()
        reg.create_job(
            "sub", "float_test", [STREAM], 10,
            require_verification=True,
            float_mode=True,
            epsilon=1e-3,
        )
        # float jobs auto-add "float" requirement
        float_caps = ["float"]
        chunk = reg.next_available_chunk(capabilities=float_caps)
        reg.assign_chunk(chunk.chunk_id, "miner1")
        second = reg.next_available_chunk(capabilities=float_caps)
        reg.assign_chunk(second.chunk_id, "miner2")

        reg.submit_result(chunk.chunk_id, "miner1", [1.000001])
        chunk = reg.submit_result(chunk.chunk_id, "miner2", [1.000002])
        assert chunk.status == ChunkStatus.COMPLETED

    def test_float_disagreement_outside_epsilon(self):
        reg = Registry()
        reg.create_job(
            "sub", "float_test", [STREAM], 10,
            require_verification=True,
            float_mode=True,
            epsilon=1e-6,
        )
        float_caps = ["float"]
        chunk = reg.next_available_chunk(capabilities=float_caps)
        reg.assign_chunk(chunk.chunk_id, "miner1")
        second = reg.next_available_chunk(capabilities=float_caps)
        reg.assign_chunk(second.chunk_id, "miner2")

        reg.submit_result(chunk.chunk_id, "miner1", [1.0])
        chunk = reg.submit_result(chunk.chunk_id, "miner2", [1.1])
        assert chunk.status == ChunkStatus.FAILED


# ── Backwards compatibility: no-verification jobs ────────────────────────────

class TestBackwardsCompat:
    def test_cluster_mode_job_completes_immediately(self):
        """cluster mode (payment=0, no verification) unchanged."""
        reg = Registry()
        reg.create_job("local", "cluster job", [STREAM], 0)
        chunk = reg.next_available_chunk()
        reg.assign_chunk(chunk.chunk_id, "worker1")
        chunk = reg.submit_result(chunk.chunk_id, "worker1", [45])
        assert chunk.status == ChunkStatus.COMPLETED

    def test_no_pass4_without_verification(self):
        """ASSIGNED chunks without require_verification are NOT re-offered."""
        reg = Registry()
        reg.create_job("sub", "test", [STREAM], 0, require_verification=False)
        chunk = reg.next_available_chunk()
        reg.assign_chunk(chunk.chunk_id, "miner1")
        # Should return None — no PENDING chunks and no k-of-2 pass for this job
        result = reg.next_available_chunk()
        assert result is None
