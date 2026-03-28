"""
Job Registry

Stores jobs and their chunks. Tracks chunk assignment, completion,
and failure. Auto-reassigns failed chunks.
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
    result: Optional[List[int]] = None
    result_hash: Optional[str] = None
    attempts: int = 0


@dataclass
class JobRecord:
    job_id: str
    submitter: str
    description: str            # optional label from submitter (not program source)
    total_chunks: int
    payment: int                # total UBD locked in escrow
    status: JobStatus = JobStatus.PENDING
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    schema_json: str = ""       # JSON-serialised schema (user keeps separately)


class Registry:
    CHUNK_TIMEOUT = 35.0  # seconds before a chunk is considered failed
    # Slightly above the miner's RECV_TIMEOUT (30s) so a stalled miner always
    # reconnects and re-requests before the server reassigns the chunk.

    def __init__(self):
        self._jobs: Dict[str, JobRecord] = {}
        self._chunks: Dict[str, ChunkRecord] = {}  # chunk_id → record

    # ── Jobs ─────────────────────────────────────────────────────────

    def create_job(
        self,
        submitter: str,
        description: str,
        chunks: List[List[int]],
        payment: int,
        schema_json: str = "",
    ) -> JobRecord:
        job_id = str(uuid.uuid4())
        total = len(chunks)
        reward_per_chunk = max(1, payment // total)

        job = JobRecord(
            job_id=job_id,
            submitter=submitter,
            description=description,
            total_chunks=total,
            payment=payment,
            schema_json=schema_json,
        )
        self._jobs[job_id] = job

        for idx, stream in enumerate(chunks):
            chunk_id = f"{job_id}:{idx}"
            self._chunks[chunk_id] = ChunkRecord(
                chunk_id=chunk_id,
                job_id=job_id,
                index=idx,
                total=total,
                stream=stream,
                reward=reward_per_chunk,
            )

        job.status = JobStatus.RUNNING
        return job

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        return self._jobs.get(job_id)

    # ── Chunk dispatch ───────────────────────────────────────────────

    def next_available_chunk(self) -> Optional[ChunkRecord]:
        """Return the next chunk that needs to be executed."""
        now = time.time()
        for chunk in self._chunks.values():
            if chunk.status == ChunkStatus.PENDING:
                return chunk
            # Re-queue timed-out assignments
            if (
                chunk.status == ChunkStatus.ASSIGNED
                and chunk.assigned_at is not None
                and now - chunk.assigned_at > self.CHUNK_TIMEOUT
            ):
                chunk.status = ChunkStatus.PENDING
                chunk.assigned_miner = None
                chunk.assigned_at = None
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
        result: List[int],
    ) -> ChunkRecord:
        """
        Record a miner's result for a chunk.
        Validates: result must be a non-empty list of integers.
        """
        chunk = self._chunks.get(chunk_id)
        if chunk is None:
            raise ValueError(f"Unknown chunk: {chunk_id}")
        if chunk.assigned_miner != miner_id:
            raise ValueError(f"Chunk {chunk_id} not assigned to {miner_id}")
        if not isinstance(result, list) or not all(isinstance(v, int) for v in result):
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

    def get_job_results(self, job_id: str) -> Optional[List[List[int]]]:
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
