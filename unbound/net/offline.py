"""
Offline batch mode — operate with no network connection.

Jobs are exported to a signed bundle file (.ubatch), executed on any
machine with no connectivity, and results imported back the moment any
channel reopens.

Transfer the bundle by any means: USB drive, SD card, QR code, email,
radio (WSPR/JS8Call encodes bytes over HF), or physical courier.

Usage:
    from unbound.net.offline import export_batch, run_batch, import_results

    # On the coordinator (before going offline):
    bundle = export_batch(registry, job_ids, private_key, node_id)
    Path("jobs.ubatch").write_bytes(bundle)

    # On the offline miner (no network needed):
    results = run_batch(Path("jobs.ubatch").read_bytes())
    Path("jobs.uresult").write_bytes(results)

    # Back on the coordinator (when any channel reopens):
    import_results(registry, Path("jobs.uresult").read_bytes())

File formats
------------
.ubatch  — gzip(JSON): exported jobs + exporter signature
.uresult — gzip(JSON): computed results + miner signature

Both formats are binary but gzip.decompress() + json.loads() is enough
to inspect them with standard tools.
"""

import gzip
import hashlib
import json
import time
from pathlib import Path
from typing import List, Optional

from .identity import sign, verify, node_id_from_pubkey_hex, pubkey_hex as _pubkey_hex


# ── Export ────────────────────────────────────────────────────────────────────

def export_batch(registry, job_ids: List[str], private_key, node_id: str) -> bytes:
    """
    Serialize the given jobs from the registry into a signed .ubatch bundle.

    registry  : Registry instance
    job_ids   : list of job IDs to include (must exist in registry)
    private_key: Ed25519 private key of the exporting node
    node_id   : hex node ID of the exporting node

    Returns raw bytes ready to write to a .ubatch file.
    Raises ValueError if a job_id is not found.
    """
    from ..uvm.encoding import encode

    jobs_payload = []
    for job_id in job_ids:
        job = registry.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found in registry")

        chunks = sorted(
            registry.chunks_for_job(job_id),
            key=lambda c: c.index,
        )
        jobs_payload.append({
            "job_id":       job_id,
            "requirements": list({req for c in chunks for req in c.requirements}),
            "payment":      job.payment,
            "chunks": [
                {
                    "index":   c.index,
                    "payload": encode(c.stream).hex(),
                }
                for c in chunks
            ],
        })

    body = json.dumps(jobs_payload, separators=(",", ":")).encode()
    bundle = {
        "version":     1,
        "exported_at": int(time.time()),
        "node_id":     node_id,
        "node_pubkey": _pubkey_hex(private_key),
        "jobs":        jobs_payload,
        "sig":         sign(private_key, hashlib.sha256(body).digest()),
    }
    return gzip.compress(json.dumps(bundle, separators=(",", ":")).encode())


# ── Run offline ───────────────────────────────────────────────────────────────

def run_batch(
    bundle_bytes: bytes,
    private_key=None,
    node_id: Optional[str] = None,
    identity_path: Optional[Path] = None,
) -> bytes:
    """
    Execute all chunks in a .ubatch bundle and return a signed .uresult bundle.

    Runs entirely offline — no network connection needed.
    private_key / node_id: miner's identity. Auto-loaded from identity_path
                           (default ~/.unbound/identity.key) if not provided.
    """
    from ..uvm.vm import UVM, VMError
    from ..uvm.encoding import decode
    from . import identity as _id

    if private_key is None:
        private_key, node_id = _id.load_or_create(
            identity_path or _id.DEFAULT_PATH
        )

    bundle = json.loads(gzip.decompress(bundle_bytes))
    _verify_bundle_sig(bundle)

    results = []
    for job in bundle["jobs"]:
        job_id = job["job_id"]
        for chunk in job["chunks"]:
            payload = bytes.fromhex(chunk["payload"])
            stream  = decode(payload)
            try:
                result = UVM().execute(stream)
            except VMError:
                result = []
            results.append({
                "job_id":      job_id,
                "chunk_index": chunk["index"],
                "result":      result,
            })

    results_body = json.dumps(results, separators=(",", ":")).encode()
    output = {
        "version":        1,
        "batch_node_id":  bundle["node_id"],
        "miner_node_id":  node_id,
        "miner_pubkey":   _pubkey_hex(private_key),
        "results":        results,
        "sig":            sign(private_key, hashlib.sha256(results_body).digest()),
    }
    return gzip.compress(json.dumps(output, separators=(",", ":")).encode())


# ── Import ────────────────────────────────────────────────────────────────────

def import_results(registry, result_bytes: bytes) -> int:
    """
    Import a .uresult bundle into the registry.

    Verifies the miner's signature, then submits each result to the
    registry using (job_id, chunk_index) to locate the chunk.

    Returns the number of chunks successfully recorded.
    """
    output = json.loads(gzip.decompress(result_bytes))

    # Verify miner signature
    miner_pubkey = output["miner_pubkey"]
    miner_id     = node_id_from_pubkey_hex(miner_pubkey)
    results_body = json.dumps(output["results"], separators=(",", ":")).encode()
    if not verify(miner_pubkey, hashlib.sha256(results_body).digest(), output["sig"]):
        raise ValueError("Invalid miner signature on result bundle")

    recorded = 0
    for entry in output["results"]:
        job_id      = entry["job_id"]
        chunk_index = entry["chunk_index"]
        result      = entry["result"]

        chunk = registry.chunk_by_index(job_id, chunk_index)
        if chunk is None:
            continue
        # Assign to the offline miner and submit result
        registry.assign_chunk(chunk.chunk_id, miner_id)
        registry.submit_result(chunk.chunk_id, miner_id, result)
        recorded += 1

    return recorded


# ── Helpers ───────────────────────────────────────────────────────────────────

def _verify_bundle_sig(bundle: dict):
    """Raise ValueError if the bundle's exporter signature is invalid."""
    jobs_body = json.dumps(bundle["jobs"], separators=(",", ":")).encode()
    pubkey    = bundle["node_pubkey"]
    sig       = bundle["sig"]
    if not verify(pubkey, hashlib.sha256(jobs_body).digest(), sig):
        raise ValueError("Bundle signature verification failed — bundle may be tampered")
    # Cross-check: node_id must match pubkey
    expected_id = node_id_from_pubkey_hex(pubkey)
    if bundle["node_id"] != expected_id:
        raise ValueError("Bundle node_id does not match node_pubkey")
