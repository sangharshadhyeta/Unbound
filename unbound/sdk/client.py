"""
Unbound SDK — client interface for the miner network or a private cluster.

Two modes:

  Payment mode (public network):
    from unbound.sdk import UnboundClient
    client = UnboundClient("http://localhost:8000", address="alice")
    job_id = client.submit(chunks, payment=100)
    results = client.wait(job_id)

  Cluster mode (private, no payment):
    from unbound.sdk import ClusterClient
    client = ClusterClient("http://localhost:8000")
    job_id = client.submit(chunks)
    results = client.wait(job_id)

ClusterClient is a thin wrapper around UnboundClient that omits the payment
and address requirements — suitable for private HPC clusters where you want
compute aggregation without a cryptocurrency layer.
"""

import base64
import time
from dataclasses import dataclass
from typing import Optional

import requests


class UnboundError(Exception):
    pass


class JobNotFound(UnboundError):
    pass


class InsufficientBalance(UnboundError):
    pass


class CompileError(UnboundError):
    pass


@dataclass
class JobResult:
    job_id: str
    status: str
    total_chunks: int
    completed_chunks: int
    results: Optional[list[int]]

    @property
    def is_complete(self) -> bool:
        return self.status == "completed"

    @property
    def is_failed(self) -> bool:
        return self.status == "failed"


class UnboundClient:
    """
    HTTP client for the Unbound node API.

    The client is language-agnostic: you can submit raw LEB128 binary chunks
    produced by any compiler targeting the UVM instruction set.
    The built-in `compile()` and `run()` methods are convenience wrappers
    around the node's Python compiler endpoint.
    """

    def __init__(self, base_url: str, address: str, timeout: float = 30.0):
        """
        base_url : URL of the Unbound node (e.g. "http://localhost:8000")
        address  : your UBD wallet address (used for payment and balance queries)
        timeout  : HTTP request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.address = address
        self._timeout = timeout
        self._session = requests.Session()

    # ── Core interface ───────────────────────────────────────────────

    def submit(
        self,
        chunks: list[bytes],
        payment: int,
        description: str = "",
        requirements: list = None,
        chunk_timeout: float = 35.0,
    ) -> str:
        """
        Submit pre-compiled binary chunks to the miner network.

        chunks      : list of LEB128-encoded UVM bytecode blobs (raw bytes)
        payment     : UBD to lock in escrow (released per completed chunk)
        description : optional human-readable label (not sent to miners)

        Returns the job_id.
        """
        chunks_b64 = [base64.b64encode(c).decode() for c in chunks]
        resp = self._post("/jobs", {
            "submitter": self.address,
            "chunks": chunks_b64,
            "payment": payment,
            "description": description,
            "requirements": requirements or [],
            "chunk_timeout": chunk_timeout,
        })
        return resp["job_id"]

    def poll(self, job_id: str) -> JobResult:
        """Return the current status of a job."""
        data = self._get(f"/jobs/{job_id}")
        return JobResult(
            job_id=data["job_id"],
            status=data["status"],
            total_chunks=data["total_chunks"],
            completed_chunks=data["completed_chunks"],
            results=data.get("results"),
        )

    def wait(
        self,
        job_id: str,
        timeout: float = 120.0,
        poll_interval: float = 0.5,
    ) -> list[int]:
        """
        Block until the job completes and return results.
        Raises UnboundError on timeout or job failure.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self.poll(job_id)
            if result.is_complete:
                return result.results or []
            if result.is_failed:
                raise UnboundError(f"Job {job_id} failed")
            time.sleep(poll_interval)
        raise UnboundError(f"Job {job_id} timed out after {timeout}s")

    def balance(self) -> int:
        """Return the UBD balance for this client's address."""
        data = self._get(f"/balance/{self.address}")
        return data["balance"]

    # ── Convenience (Python compiler) ───────────────────────────────

    def compile(self, source: str) -> tuple[list[bytes], dict]:
        """
        Compile Python source via the node's compiler endpoint.

        Returns (chunks, schema):
          chunks : list of LEB128 binary blobs ready to pass to submit()
          schema : dict mapping variable names and output positions
                   — keep this yourself, never send to the node or miners
        """
        data = self._post("/compile", {"source": source})
        chunks = [base64.b64decode(b64) for b64 in data["chunks"]]
        return chunks, data["schema"]

    def run(self, source: str, payment: int) -> list[int]:
        """
        One-shot: compile Python source, submit to the network, wait for result.
        Returns the flat list of output integers.
        """
        chunks, _schema = self.compile(source)
        job_id = self.submit(chunks, payment=payment, description=source[:80])
        return self.wait(job_id)

    def run_job(self, job) -> object:
        """
        Run a SearchJob (or any job with build_chunks / aggregate / payment).

        The job builds its own chunks (one per candidate), the client submits
        them, waits for all results, then delegates aggregation back to the job.

        Returns whatever job.aggregate() returns — a list of (candidate, score)
        pairs, a filtered list, a (best, score) tuple, a gradient vector, etc.
        """
        chunks = job.build_chunks()
        job_id = self.submit(
            chunks,
            payment=job.payment,
            description=getattr(job, "description", ""),
            requirements=getattr(job, "requirements", []),
            chunk_timeout=getattr(job, "chunk_timeout", 35.0),
        )
        raw = self.wait(job_id)
        return job.aggregate(raw)

    # ── HTTP helpers ─────────────────────────────────────────────────

    def _post(self, path: str, body: dict) -> dict:
        try:
            resp = self._session.post(
                self.base_url + path, json=body, timeout=self._timeout
            )
        except requests.RequestException as e:
            raise UnboundError(f"Request failed: {e}") from e
        return self._handle(resp)

    def _get(self, path: str) -> dict:
        try:
            resp = self._session.get(
                self.base_url + path, timeout=self._timeout
            )
        except requests.RequestException as e:
            raise UnboundError(f"Request failed: {e}") from e
        return self._handle(resp)

    def _handle(self, resp: requests.Response) -> dict:
        if resp.status_code == 404:
            raise JobNotFound(resp.json().get("detail", "not found"))
        if resp.status_code == 402:
            raise InsufficientBalance(resp.json().get("detail", "insufficient balance"))
        if resp.status_code == 400:
            raise CompileError(resp.json().get("detail", "bad request"))
        if not resp.ok:
            raise UnboundError(f"HTTP {resp.status_code}: {resp.text}")
        return resp.json()


class ClusterClient:
    """
    Client for a private Unbound cluster — no payment, no wallet address.

    Use this when you want to distribute computation across your own machines
    without a cryptocurrency layer. Start the cluster with:

        unbound cluster node
        unbound cluster mine   # on each worker machine

    Then submit jobs from your code:

        from unbound.sdk import ClusterClient

        client = ClusterClient("http://coordinator:8000")
        results = client.run("print(sum(range(10)))")

    All SearchJob types work identically — just omit the payment argument.

        job = MinimizeJob(eval_body=..., candidates=..., payment=0)
        best = client.run_job(job)
    """

    def __init__(self, base_url: str, timeout: float = 300.0):
        """
        base_url : URL of the cluster coordinator (e.g. "http://10.0.0.1:8000")
        timeout  : how long to wait for a job to complete, in seconds.
                   Set higher for expensive per-chunk workloads.
        """
        self._client = UnboundClient(base_url, address="local", timeout=30.0)
        self._timeout = timeout

    def submit(self, chunks: list[bytes], description: str = "") -> str:
        """Submit pre-compiled binary chunks. Returns job_id."""
        return self._client.submit(chunks, payment=0, description=description)

    def poll(self, job_id: str) -> JobResult:
        return self._client.poll(job_id)

    def wait(self, job_id: str) -> list[int]:
        return self._client.wait(job_id, timeout=self._timeout)

    def compile(self, source: str) -> tuple[list[bytes], dict]:
        return self._client.compile(source)

    def run(self, source: str) -> list[int]:
        """One-shot: compile, submit, wait, return results."""
        chunks, _schema = self.compile(source)
        job_id = self.submit(chunks, description=source[:80])
        return self.wait(job_id)

    def run_job(self, job) -> object:
        """Run a SearchJob. Payment is ignored."""
        chunks = job.build_chunks()
        job_id = self.submit(chunks, description=getattr(job, "description", ""))
        raw = self.wait(job_id)
        return job.aggregate(raw)
