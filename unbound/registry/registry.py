"""
Job Registry

Stores jobs and their chunks. Tracks chunk assignment, completion,
and failure. Auto-reassigns failed chunks. Routes chunks to workers
based on capability requirements.
"""

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


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
    ) -> JobRecord:
        job_id = str(uuid.uuid4())
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
            )

        job.status = JobStatus.RUNNING
        return job

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        return self._jobs.get(job_id)

    # ── Chunk dispatch ───────────────────────────────────────────────

    def next_available_chunk(
        self,
        capabilities: List[str] = None,
    ) -> Optional[ChunkRecord]:
        """
        Return the next chunk whose requirements are satisfied by the
        worker's capabilities. Pass capabilities=None (or []) to match
        only chunks with no requirements.

        Requirements are matched as a subset check:
          all(r in worker_caps for r in chunk.requirements)
        """
        caps = set(capabilities or [])
        now = time.time()

        for chunk in self._chunks.values():
            # Re-queue timed-out assignments
            if (
                chunk.status == ChunkStatus.ASSIGNED
                and chunk.assigned_at is not None
            ):
                timeout = self._jobs[chunk.job_id].chunk_timeout
                if now - chunk.assigned_at > timeout:
                    chunk.status = ChunkStatus.PENDING
                    chunk.assigned_miner = None
                    chunk.assigned_at = None

            if chunk.status != ChunkStatus.PENDING:
                continue

            # Check capability requirements
            if all(r in caps for r in chunk.requirements):
                return chunk

        return None

    def assign_chunk(self, chunk_id: str, miner_id: str) -> ChunkRecord:
        chunk = self._chunks[chunk_id]
        chunk.status = ChunkStatus.ASSIGNED
        chunk.assigned_miner = miner_id
        chunk.assigned_at = time.time()
        chunk.attempts += 1
        return chunk

    def submit_result(
        self,
        chunk_id: str,
        miner_id: str,
        result: List,
    ) -> ChunkRecord:
        """Record a worker's result. Validates: non-empty list."""
        chunk = self._chunks.get(chunk_id)
        if chunk is None:
            raise ValueError(f"Unknown chunk: {chunk_id}")
        if chunk.assigned_miner != miner_id:
            raise ValueError(f"Chunk {chunk_id} not assigned to {miner_id}")
        if not isinstance(result, list) or len(result) == 0:
            chunk.status = ChunkStatus.FAILED
            return chunk

        result_hash = hashlib.sha256(
            json.dumps(result, separators=(",", ":")).encode()
        ).hexdigest()

        chunk.result = result
        chunk.result_hash = result_hash
        chunk.status = ChunkStatus.COMPLETED

        self._check_job_complete(chunk.job_id)
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
