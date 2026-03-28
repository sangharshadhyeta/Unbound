"""
Unbound Node API

Language-agnostic compute interface. Products submit pre-compiled binary
chunks (LEB128-encoded UVM bytecode) and get raw integer results back.
The compiler is the caller's concern — Unbound just runs the bytes.

Endpoints:
  POST /compile          convenience: Python source → binary chunks + schema
  POST /jobs             submit binary chunks, lock UBD escrow, get job_id
  GET  /jobs/{id}        poll status, retrieve raw results when complete
  GET  /balance/{addr}   UBD balance
  GET  /health
"""

import base64
import json
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ..registry.registry import Registry, JobStatus
from ..ledger.ledger import Ledger, LedgerError
from ..uvm.encoding import decode as leb128_decode

app = FastAPI(title="Unbound Node API")

# Injected at startup by the node runner
_registry: Optional[Registry] = None
_ledger: Optional[Ledger] = None


def init(registry: Registry, ledger: Ledger):
    global _registry, _ledger
    _registry = registry
    _ledger = ledger


# ── Models ───────────────────────────────────────────────────────────

class CompileRequest(BaseModel):
    source: str


class CompileResponse(BaseModel):
    chunks: list[str]   # base64-encoded LEB128 binary, one entry per chunk
    schema: dict        # variable map + output positions — caller keeps this
    stream_length: int


class SubmitJobRequest(BaseModel):
    submitter: str
    chunks: list[str]   # base64-encoded LEB128 binary chunks
    payment: int
    description: str = ""


class SubmitJobResponse(BaseModel):
    job_id: str
    total_chunks: int
    payment_locked: int


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    total_chunks: int
    completed_chunks: int
    results: Optional[list] = None   # flat list of ints when complete


class BalanceResponse(BaseModel):
    address: str
    balance: int


# ── Endpoints ────────────────────────────────────────────────────────

@app.post("/compile", response_model=CompileResponse)
def compile_source(req: CompileRequest):
    """
    Convenience endpoint: compile Python source to binary chunks.
    The returned schema is private — store it yourself, never send it
    to the node or to miners.
    """
    from ..compiler.compiler import compile_source as _compile, CompileError
    from ..compiler.chunker import split_stream
    from ..uvm.encoding import encode

    try:
        stream, schema = _compile(req.source)
    except CompileError as e:
        raise HTTPException(status_code=400, detail=f"Compile error: {e}")

    # Single program runs as one atomic chunk (control flow can't be split)
    raw_chunks = split_stream("tmp", stream, chunk_size=len(stream))
    chunks_b64 = [
        base64.b64encode(encode(c.stream)).decode()
        for c in raw_chunks
    ]

    return CompileResponse(
        chunks=chunks_b64,
        schema={
            "variables": schema.variables,
            "output_positions": schema.output_positions,
        },
        stream_length=schema.stream_length,
    )


@app.post("/jobs", response_model=SubmitJobResponse)
def submit_job(req: SubmitJobRequest):
    """
    Submit pre-compiled binary chunks to the network.
    chunks: list of base64-encoded LEB128 UVM bytecode blobs.
    Any compiler that targets the UVM instruction set can produce these.
    """
    if not req.chunks:
        raise HTTPException(status_code=400, detail="chunks must be non-empty")

    # Decode base64 → bytes → UVM integer streams
    try:
        chunk_streams = [
            leb128_decode(base64.b64decode(b64))
            for b64 in req.chunks
        ]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid chunk encoding: {e}")

    # Lock escrow before creating the job
    try:
        _ledger.lock_escrow(
            escrow_id="__pending__",
            owner=req.submitter,
            amount=req.payment,
        )
    except LedgerError as e:
        raise HTTPException(status_code=402, detail=str(e))

    job = _registry.create_job(
        submitter=req.submitter,
        description=req.description,
        chunks=chunk_streams,
        payment=req.payment,
    )

    # Re-key escrow to the real job_id
    with _ledger._conn:
        _ledger._conn.execute(
            "UPDATE escrow SET escrow_id = ? WHERE escrow_id = ?",
            (job.job_id, "__pending__"),
        )

    return SubmitJobResponse(
        job_id=job.job_id,
        total_chunks=job.total_chunks,
        payment_locked=req.payment,
    )


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
def job_status(job_id: str):
    job = _registry.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    chunks = [c for c in _registry._chunks.values() if c.job_id == job_id]
    completed = sum(1 for c in chunks if c.status.value == "completed")

    results = None
    if job.status == JobStatus.COMPLETED:
        raw = _registry.get_job_results(job_id)
        if raw:
            # Flatten chunk results into one ordered list
            results = [v for chunk_result in raw for v in (chunk_result or [])]

    return JobStatusResponse(
        job_id=job_id,
        status=job.status.value,
        total_chunks=job.total_chunks,
        completed_chunks=completed,
        results=results,
    )


@app.get("/balance/{address}", response_model=BalanceResponse)
def get_balance(address: str):
    return BalanceResponse(
        address=address,
        balance=_ledger.balance(address),
    )


@app.get("/health")
def health():
    return {"status": "ok"}
