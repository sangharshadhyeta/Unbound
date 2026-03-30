"""
Job Registry

Stores jobs and their chunks. Tracks chunk assignment, completion,
and failure. Auto-reassigns failed chunks. Routes chunks to workers
based on capability requirements.
"""

import hashlib
import json
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set


class ChunkStatus(str, Enum):
    PENDING   = "pending"
    ASSIGNED  = "assigned"
    COMPLETED = "completed"
    FAILED    = "failed"


class JobStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"


@dataclass
class ChunkRecord:
    chunk_id: str
    job_id: str
    index: int
    total: int
    stream: List[int]
    reward: int
    status: ChunkStatus = ChunkStatus.PENDING
    assigned_miner: Optional[str] = None
    assigned_at: Optional[float] = None
    result: Optional[List] = None
    result_hash: Optional[str] = None
    attempts: int = 0
    requirements: List[str] = field(default_factory=list)
    # e.g. ["gpu"], ["cuda12", "vram:8192"], ["high-memory"], []
    min_miner_stake: int = 0             # minimum stake a miner must hold to receive this chunk
    # k-of-2 verification fields
    second_miner: Optional[str] = None  # second verifier assigned to this chunk
    first_result_pending: bool = False  # True: first result stored, awaiting second miner


@dataclass
class JobRecord:
    job_id: str
    submitter: str
    description: str
    total_chunks: int
    payment: int
    chunk_timeout: float = 35.0          # seconds before chunk is reassigned
    status: JobStatus = JobStatus.PENDING
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    schema_json: str = ""
    float_mode: bool = False             # True when stream contains float opcodes
    epsilon: float = 0.0                 # rel_tol for float result comparison
    min_miner_stake: int = 0             # submitter-declared minimum miner stake
    data_cid: Optional[str] = None       # IPFS CID of (masked) dataset; None = no dataset
    require_verification: bool = False   # True: require k-of-2 agreement before COMPLETED


class Registry:
    DEFAULT_CHUNK_TIMEOUT = 35.0

    def __init__(self):
        self._jobs: Dict[str, JobRecord] = {}
        self._chunks: Dict[str, ChunkRecord] = {}

    # ── Jobs ─────────────────────────────────────────────────────────

    def create_job(
        self,
        submitter: str,
        description: str,
        chunks: List[List[int]],
        payment: int,
        schema_json: str = "",
        requirements: List[str] = None,
        chunk_timeout: float = DEFAULT_CHUNK_TIMEOUT,
        float_mode: bool = False,
        epsilon: float = 0.0,
        min_miner_stake: int = 0,
        data_cid: Optional[str] = None,
        require_verification: bool = False,
        job_id: Optional[str] = None,  # supply when replaying a gossip/batch job
    ) -> JobRecord:
        job_id = job_id or str(uuid.uuid4())
        total = len(chunks)
        reward_per_chunk = max(1, payment // total) if payment > 0 else 0
        reqs = requirements or []

        job = JobRecord(
            job_id=job_id,
            submitter=submitter,
            description=description,
            total_chunks=total,
            payment=payment,
            chunk_timeout=chunk_timeout,
            schema_json=schema_json,
            float_mode=float_mode,
            epsilon=epsilon,
            min_miner_stake=min_miner_stake,
            data_cid=data_cid,
            require_verification=require_verification,
        )
        self._jobs[job_id] = job

        # Float jobs require miners that self-declared float capability.
        # This keeps float chunks away from integer-only search-tier miners
        # (e.g. ASIC control boards, Raspberry Pi) that would timeout or
        # produce imprecise results on float-heavy workloads.
        chunk_reqs = list(reqs)
        if float_mode and "float" not in chunk_reqs:
            chunk_reqs.append("float")

        for idx, stream in enumerate(chunks):
            chunk_id = f"{job_id}:{idx}"
            self._chunks[chunk_id] = ChunkRecord(
                chunk_id=chunk_id,
                job_id=job_id,
                index=idx,
                total=total,
                stream=stream,
                reward=reward_per_chunk,
                requirements=chunk_reqs,
                min_miner_stake=min_miner_stake,
            )

        job.status = JobStatus.RUNNING
        return job

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        return self._jobs.get(job_id)

    def chunks_for_job(self, job_id: str) -> List[ChunkRecord]:
        """Return all ChunkRecords for a job, in any order."""
        return [c for c in self._chunks.values() if c.job_id == job_id]

    def chunk_by_index(self, job_id: str, index: int) -> Optional[ChunkRecord]:
        """Look up a chunk by its job and position index."""
        chunk_id = f"{job_id}:{index}"
        return self._chunks.get(chunk_id)

    # ── Chunk dispatch ───────────────────────────────────────────────

    def next_available_chunk(
        self,
        capabilities: List[str] = None,
        miner_stake: int = 0,
        miner_cids: List[str] = None,
        exclude_job_ids: Optional[Set[str]] = None,
    ) -> Optional[ChunkRecord]:
        """
        Return the next chunk whose requirements are satisfied by the worker.

        Matching rules:
          1. Capability tags: all(r in worker_caps for r in chunk.requirements)
          2. Stake threshold: miner_stake >= chunk.min_miner_stake
          3. Job exclusivity: chunk.job_id not in exclude_job_ids
             Prevents a single worker from receiving two chunks of the same job,
             making non-collusion a structural property of the dispatch protocol
             rather than an assumption (MPC simulation paradigm).

        Dispatch priority (when miner_cids is non-empty):
          Pass 1 — jobs whose data_cid is in the miner's local cache.
                   Keeps data transfer near-zero for already-cached datasets.
          Pass 2 — jobs with no data_cid (pure computation, no dataset).
          Pass 3 — all remaining eligible chunks (miner will need to fetch CID).

        Within each pass, eligible chunks are shuffled before selection.
        This provides positional anonymity: a worker cannot infer their
        chunk's index within the full job from dispatch timing or ordering
        (Shuffle Model — Erlingsson et al. 2019).
        """
        caps     = set(capabilities or [])
        cached   = set(miner_cids or [])
        excluded = exclude_job_ids or set()
        now      = time.time()

        # Expire timed-out assignments first (single pass)
        for chunk in self._chunks.values():
            if (
                chunk.status == ChunkStatus.ASSIGNED
                and chunk.assigned_at is not None
            ):
                timeout = self._jobs[chunk.job_id].chunk_timeout
                if now - chunk.assigned_at > timeout:
                    chunk.status = ChunkStatus.PENDING
                    chunk.assigned_miner = None
                    chunk.assigned_at = None

        def _eligible_pending(chunk: ChunkRecord) -> bool:
            if chunk.status != ChunkStatus.PENDING:
                return False
            if chunk.job_id in excluded:
                return False
            if not all(r in caps for r in chunk.requirements):
                return False
            if miner_stake < chunk.min_miner_stake:
                return False
            return True

        def _eligible_second(chunk: ChunkRecord) -> bool:
            """Eligibility for k-of-2 second assignment."""
            if chunk.status != ChunkStatus.ASSIGNED:
                return False
            if chunk.second_miner is not None:
                return False   # already has a second miner
            job = self._jobs.get(chunk.job_id)
            if job is None or not job.require_verification:
                return False
            if chunk.job_id in excluded:
                return False   # prevents assigned miner from also being second
            if not all(r in caps for r in chunk.requirements):
                return False
            if miner_stake < chunk.min_miner_stake:
                return False
            return True

        # Collect all eligible PENDING chunks and shuffle for positional anonymity.
        # Shuffle once; priority passes iterate over the same shuffled list.
        eligible = [c for c in self._chunks.values() if _eligible_pending(c)]
        random.shuffle(eligible)

        if cached:
            # Pass 1: prefer jobs whose dataset the miner already has locally.
            for chunk in eligible:
                job_cid = self._jobs[chunk.job_id].data_cid
                if job_cid and job_cid in cached:
                    return chunk

        # Pass 2: pure-compute jobs (no dataset CID).
        for chunk in eligible:
            if self._jobs[chunk.job_id].data_cid is None:
                return chunk

        # Pass 3: any eligible PENDING chunk — miner will need to fetch the dataset.
        if eligible:
            return eligible[0]

        # Pass 4 (k-of-2): offer ASSIGNED chunks that need a second verifier.
        second_eligible = [c for c in self._chunks.values() if _eligible_second(c)]
        random.shuffle(second_eligible)
        return second_eligible[0] if second_eligible else None

    def assign_chunk(self, chunk_id: str, miner_id: str) -> ChunkRecord:
        chunk = self._chunks[chunk_id]
        if chunk.status == ChunkStatus.PENDING:
            # Primary assignment
            chunk.status = ChunkStatus.ASSIGNED
            chunk.assigned_miner = miner_id
            chunk.assigned_at = time.time()
            chunk.attempts += 1
        else:
            # Secondary assignment for k-of-2 verification
            chunk.second_miner = miner_id
        return chunk

    def submit_result(
        self,
        chunk_id: str,
        miner_id: str,
        result: List,
    ) -> ChunkRecord:
        """
        Record a worker's result.

        Without verification (cluster mode / payment=0): single result completes chunk.
        With verification (require_verification=True):
          - First result stored, chunk stays ASSIGNED awaiting second miner.
          - Second result compared; agreement → COMPLETED, disagreement → FAILED.
            Server is responsible for slashing disagreed miners (chunk.assigned_miner
            and chunk.second_miner are preserved in FAILED state for the server to read).
        """
        chunk = self._chunks.get(chunk_id)
        if chunk is None:
            raise ValueError(f"Unknown chunk: {chunk_id}")
        if miner_id not in (chunk.assigned_miner, chunk.second_miner):
            raise ValueError(f"Chunk {chunk_id} not assigned to {miner_id}")
        if not isinstance(result, list) or len(result) == 0:
            chunk.status = ChunkStatus.FAILED
            return chunk

        result_hash = hashlib.sha256(
            json.dumps(result, separators=(",", ":")).encode()
        ).hexdigest()

        job = self._jobs[chunk.job_id]

        if not job.require_verification:
            # Single-miner completion
            chunk.result = result
            chunk.result_hash = result_hash
            chunk.status = ChunkStatus.COMPLETED
            self._check_job_complete(chunk.job_id)
            return chunk

        if not chunk.first_result_pending:
            # First submission: store result, wait for second miner
            chunk.result = result
            chunk.result_hash = result_hash
            chunk.first_result_pending = True
            return chunk  # status stays ASSIGNED

        # Second submission: compare with first
        from ..verifier.verifier import results_agree
        if results_agree(chunk.result, result, job.float_mode, job.epsilon):
            chunk.status = ChunkStatus.COMPLETED
            self._check_job_complete(chunk.job_id)
        else:
            # Disagreement: mark FAILED; server will slash both and reassign.
            # assigned_miner and second_miner are intentionally preserved so the
            # server can identify which miners disagreed before resetting.
            chunk.status = ChunkStatus.FAILED
        return chunk

    def _check_job_complete(self, job_id: str):
        job = self._jobs[job_id]
        chunks = [c for c in self._chunks.values() if c.job_id == job_id]
        if all(c.status == ChunkStatus.COMPLETED for c in chunks):
            job.status = JobStatus.COMPLETED
            job.completed_at = time.time()

    def get_job_results(self, job_id: str) -> Optional[List[List]]:
        """Return ordered list of chunk results if all completed."""
        job = self._jobs.get(job_id)
        if not job or job.status != JobStatus.COMPLETED:
            return None
        chunks = sorted(
            [c for c in self._chunks.values() if c.job_id == job_id],
            key=lambda c: c.index,
        )
        return [c.result for c in chunks]

    def pending_chunks(self, job_id: str) -> List[ChunkRecord]:
        return [
            c for c in self._chunks.values()
            if c.job_id == job_id and c.status == ChunkStatus.PENDING
        ]
