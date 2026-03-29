"""Tests for CID-aware chunk routing."""

import pytest
from unbound.registry.registry import Registry
from unbound.uvm.opcodes import ADD, OUTPUT, HALT


def _make_reg(data_cid=None):
    r = Registry()
    r.create_job("alice", "", [[ADD, OUTPUT, HALT]], 0, data_cid=data_cid)
    return r


# ── Pass-1: miner has the CID ────────────────────────────────────────

def test_miner_with_cid_gets_cid_job():
    r = _make_reg(data_cid="QmABC")
    chunk = r.next_available_chunk(miner_cids=["QmABC"])
    assert chunk is not None


def test_miner_without_cid_still_gets_cid_job_pass3():
    """Miner without the CID still receives the job — just at lower priority."""
    r = _make_reg(data_cid="QmABC")
    chunk = r.next_available_chunk(miner_cids=[])
    assert chunk is not None


def test_miner_with_cid_preferred_over_no_cid():
    """When two jobs exist — one with matching CID, one without —
    the miner that has the CID should receive the matching job first."""
    r = Registry()
    job_cid = r.create_job("alice", "", [[ADD, OUTPUT, HALT]], 0, data_cid="QmABC")
    job_plain = r.create_job("alice", "", [[ADD, OUTPUT, HALT]], 0, data_cid=None)

    chunk = r.next_available_chunk(miner_cids=["QmABC"])
    assert chunk is not None
    assert chunk.job_id == job_cid.job_id


def test_miner_without_cid_gets_plain_job_first():
    """Miner with no cached CIDs should be routed to the no-CID job in pass 2."""
    r = Registry()
    _cid_job = r.create_job("alice", "", [[ADD, OUTPUT, HALT]], 0, data_cid="QmABC")
    plain_job = r.create_job("alice", "", [[ADD, OUTPUT, HALT]], 0, data_cid=None)

    chunk = r.next_available_chunk(miner_cids=[])
    assert chunk is not None
    assert chunk.job_id == plain_job.job_id


# ── No-CID jobs ───────────────────────────────────────────────────────

def test_no_cid_job_routed_to_any_miner():
    r = _make_reg(data_cid=None)
    assert r.next_available_chunk(miner_cids=[]) is not None
    assert r.next_available_chunk(miner_cids=["QmXYZ"]) is not None


# ── Multiple cached CIDs ──────────────────────────────────────────────

def test_miner_with_multiple_cids_matches_any():
    r = _make_reg(data_cid="QmDEF")
    chunk = r.next_available_chunk(miner_cids=["QmABC", "QmDEF", "QmGHI"])
    assert chunk is not None


def test_miner_cid_no_match_falls_to_pass3():
    """Miner has CIDs but none match the job's CID — chunk still dispatched."""
    r = _make_reg(data_cid="QmABC")
    chunk = r.next_available_chunk(miner_cids=["QmXXX", "QmYYY"])
    assert chunk is not None


# ── data_cid stored on JobRecord ─────────────────────────────────────

def test_data_cid_stored_on_job():
    r = Registry()
    job = r.create_job("alice", "", [[ADD, OUTPUT, HALT]], 0, data_cid="QmSTORED")
    assert job.data_cid == "QmSTORED"


def test_no_data_cid_is_none():
    r = Registry()
    job = r.create_job("alice", "", [[ADD, OUTPUT, HALT]], 0)
    assert job.data_cid is None


# ── CID routing combined with stake and capability gating ────────────

def test_cid_priority_respects_capability_gate():
    """Pass-1 match must still satisfy capability requirements."""
    r = Registry()
    r.create_job("alice", "", [[ADD, OUTPUT, HALT]], 0,
                 data_cid="QmABC", requirements=["gpu"])
    # Miner has the CID but not the required capability
    chunk = r.next_available_chunk(capabilities=[], miner_cids=["QmABC"])
    assert chunk is None


def test_cid_priority_respects_stake_gate():
    """Pass-1 match must still satisfy stake requirement."""
    r = Registry()
    r.create_job("alice", "", [[ADD, OUTPUT, HALT]], 0,
                 data_cid="QmABC", min_miner_stake=100)
    # Miner has the CID but insufficient stake
    chunk = r.next_available_chunk(miner_cids=["QmABC"], miner_stake=50)
    assert chunk is None


def test_cid_priority_with_sufficient_stake():
    r = Registry()
    r.create_job("alice", "", [[ADD, OUTPUT, HALT]], 0,
                 data_cid="QmABC", min_miner_stake=100)
    chunk = r.next_available_chunk(miner_cids=["QmABC"], miner_stake=100)
    assert chunk is not None
