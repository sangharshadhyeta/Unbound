"""Tests for the CID-in-frame binary protocol (server → miner wire format)."""

import pytest
from unbound.uvm.encoding import encode, decode


def _build_frame(chunk_id: str, cid: str, stream: list) -> bytes:
    """Replicate the server-side frame assembly."""
    cid_bytes = cid.encode() if cid else b""
    payload = encode(stream)
    return (
        chunk_id.encode()
        + b"\x00"
        + bytes([len(cid_bytes)])
        + cid_bytes
        + payload
    )


def _parse_frame(raw: bytes):
    """Replicate the miner-side frame parsing. Returns (chunk_id, cid_or_None, stream)."""
    null_pos = raw.index(b"\x00")
    chunk_id = raw[:null_pos].decode()
    rest = raw[null_pos + 1:]
    cid_len = rest[0]
    if cid_len > 0:
        cid = rest[1:1 + cid_len].decode()
        payload = rest[1 + cid_len:]
    else:
        cid = None
        payload = rest[1:]
    stream = decode(payload)
    return chunk_id, cid, stream


# ── Round-trip ────────────────────────────────────────────────────────

def test_frame_with_cid_roundtrip():
    stream = [1, 2, 3, 4]
    raw = _build_frame("job1:0", "QmTestCIDabc123", stream)
    chunk_id, cid, parsed = _parse_frame(raw)
    assert chunk_id == "job1:0"
    assert cid == "QmTestCIDabc123"
    assert parsed == stream


def test_frame_without_cid_roundtrip():
    stream = [10, 20, 30]
    raw = _build_frame("job2:1", "", stream)
    chunk_id, cid, parsed = _parse_frame(raw)
    assert chunk_id == "job2:1"
    assert cid is None
    assert parsed == stream


def test_frame_empty_stream_with_cid():
    raw = _build_frame("j:0", "QmX", [])
    chunk_id, cid, stream = _parse_frame(raw)
    assert chunk_id == "j:0"
    assert cid == "QmX"
    assert stream == []


def test_frame_empty_stream_no_cid():
    raw = _build_frame("j:0", "", [])
    chunk_id, cid, stream = _parse_frame(raw)
    assert cid is None
    assert stream == []


# ── CID length boundary ───────────────────────────────────────────────

def test_cid_len_byte_is_accurate():
    from unbound.uvm.opcodes import HALT
    cid = "QmSomeCID"
    raw = _build_frame("x:0", cid, [HALT])
    null_pos = raw.index(b"\x00")
    cid_len_byte = raw[null_pos + 1]
    assert cid_len_byte == len(cid.encode())


def test_no_cid_len_byte_is_zero():
    from unbound.uvm.opcodes import HALT
    raw = _build_frame("x:0", "", [HALT])
    null_pos = raw.index(b"\x00")
    assert raw[null_pos + 1] == 0


# ── Real IPFS CID (base58, 46 chars) ─────────────────────────────────

def test_real_ipfs_cid_v0():
    cid = "QmYwAPJzv5CZsnA625s3Xf2nemtYgPpHdWEz79ojWnPbdG"
    stream = [100, 200, 300]
    raw = _build_frame("ipfs:0", cid, stream)
    _, parsed_cid, parsed_stream = _parse_frame(raw)
    assert parsed_cid == cid
    assert parsed_stream == stream
