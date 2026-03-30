"""
Tests for the CID-in-frame binary protocol (server → miner wire format).

Frame layout (v2):
  wire_id (UTF-8)  \x00  job_token (8 bytes)
  cid_len (1 byte)  cid_bytes  payload

wire_id   — opaque UUID sent to the worker; maps back to internal chunk_id
            on the server side.  Worker cannot infer job_id or chunk index.
job_token — SHA256(job_id)[:8]; opaque stable key for local CID caching.
            Does not reveal the real job_id or total chunk count.
"""

import hashlib
import pytest
from unbound.uvm.encoding import encode, decode
from unbound.uvm.opcodes import HALT


def _build_frame(wire_id: str, job_token: bytes, cid: str, stream: list) -> bytes:
    """Replicate the server-side frame assembly."""
    cid_bytes = cid.encode() if cid else b""
    payload   = encode(stream)
    return (
        wire_id.encode()
        + b"\x00"
        + job_token                   # always 8 bytes
        + bytes([len(cid_bytes)])
        + cid_bytes
        + payload
    )


def _parse_frame(raw: bytes):
    """Replicate the miner-side frame parsing.
    Returns (wire_id, job_token_hex, cid_or_None, stream).
    """
    null_pos  = raw.index(b"\x00")
    wire_id   = raw[:null_pos].decode()
    rest      = raw[null_pos + 1:]

    job_token = rest[:8].hex()
    rest      = rest[8:]

    cid_len = rest[0]
    if cid_len > 0:
        cid     = rest[1:1 + cid_len].decode()
        payload = rest[1 + cid_len:]
    else:
        cid     = None
        payload = rest[1:]

    stream = decode(payload)
    return wire_id, job_token, cid, stream


def _job_token(job_id: str) -> bytes:
    """Same derivation used by the server."""
    return hashlib.sha256(job_id.encode()).digest()[:8]


# ── Round-trip ────────────────────────────────────────────────────────

def test_frame_with_cid_roundtrip():
    token = _job_token("job1")
    stream = [1, 2, 3, 4]
    raw = _build_frame("wire-uuid-001", token, "QmTestCIDabc123", stream)
    wire_id, tok_hex, cid, parsed = _parse_frame(raw)
    assert wire_id  == "wire-uuid-001"
    assert tok_hex  == token.hex()
    assert cid      == "QmTestCIDabc123"
    assert parsed   == stream


def test_frame_without_cid_roundtrip():
    token = _job_token("job2")
    stream = [10, 20, 30]
    raw = _build_frame("wire-uuid-002", token, "", stream)
    wire_id, tok_hex, cid, parsed = _parse_frame(raw)
    assert wire_id == "wire-uuid-002"
    assert cid     is None
    assert parsed  == stream


def test_frame_empty_stream_with_cid():
    token = _job_token("j")
    raw = _build_frame("w0", token, "QmX", [])
    wire_id, _, cid, stream = _parse_frame(raw)
    assert wire_id == "w0"
    assert cid     == "QmX"
    assert stream  == []


def test_frame_empty_stream_no_cid():
    token = _job_token("j")
    raw = _build_frame("w0", token, "", [])
    _, _, cid, stream = _parse_frame(raw)
    assert cid    is None
    assert stream == []


# ── job_token is always 8 bytes ───────────────────────────────────────

def test_job_token_length():
    raw = _build_frame("w", _job_token("any-job"), "Qm", [HALT])
    null_pos = raw.index(b"\x00")
    rest = raw[null_pos + 1:]
    assert len(rest[:8]) == 8


def test_job_token_is_deterministic():
    """Same job_id → same token every time."""
    t1 = _job_token("abc-123")
    t2 = _job_token("abc-123")
    assert t1 == t2


def test_different_jobs_have_different_tokens():
    assert _job_token("job-A") != _job_token("job-B")


def test_token_does_not_contain_job_id():
    """The token must not be a plain encoding of the job_id string."""
    job_id = "secret-job-id"
    token  = _job_token(job_id)
    assert job_id.encode() not in token   # token is opaque


# ── CID length boundary ───────────────────────────────────────────────

def test_cid_len_byte_is_accurate():
    cid = "QmSomeCID"
    raw = _build_frame("x", _job_token("j"), cid, [HALT])
    null_pos = raw.index(b"\x00")
    # skip 8-byte job_token, then cid_len byte
    cid_len_byte = raw[null_pos + 1 + 8]
    assert cid_len_byte == len(cid.encode())


def test_no_cid_len_byte_is_zero():
    raw = _build_frame("x", _job_token("j"), "", [HALT])
    null_pos = raw.index(b"\x00")
    assert raw[null_pos + 1 + 8] == 0


# ── Real IPFS CID (base58, 46 chars) ─────────────────────────────────

def test_real_ipfs_cid_v0():
    cid    = "QmYwAPJzv5CZsnA625s3Xf2nemtYgPpHdWEz79ojWnPbdG"
    stream = [100, 200, 300]
    token  = _job_token("ipfs-job")
    raw    = _build_frame("wire-ipfs", token, cid, stream)
    _, _, parsed_cid, parsed_stream = _parse_frame(raw)
    assert parsed_cid    == cid
    assert parsed_stream == stream


# ── Wire ID is opaque (UUID-like, no job structure) ───────────────────

def test_wire_id_roundtrips_unchanged():
    """Wire ID is returned verbatim — server maps it back internally."""
    import uuid
    wire_id = str(uuid.uuid4())
    raw = _build_frame(wire_id, _job_token("j"), "", [HALT])
    parsed_wire_id, _, _, _ = _parse_frame(raw)
    assert parsed_wire_id == wire_id
