"""Tests for the UBD token ledger."""

import pytest
from unbound.ledger.ledger import Ledger, LedgerError


def make_ledger():
    l = Ledger()
    l.credit("alice", 1000, "genesis")
    l.credit("bob", 500, "genesis")
    return l


def test_initial_balance():
    l = make_ledger()
    assert l.balance("alice") == 1000
    assert l.balance("bob") == 500
    assert l.balance("nobody") == 0


def test_transfer():
    l = make_ledger()
    l.transfer("alice", "bob", 200)
    assert l.balance("alice") == 800
    assert l.balance("bob") == 700


def test_transfer_insufficient():
    l = make_ledger()
    with pytest.raises(LedgerError, match="Insufficient"):
        l.transfer("bob", "alice", 1000)


def test_escrow_lock_and_release():
    l = make_ledger()
    l.lock_escrow("job1", "alice", 300)
    assert l.balance("alice") == 700
    l.release_escrow("job1", "miner1", 100)
    assert l.balance("miner1") == 100
    l.release_escrow("job1", "miner2", 200)
    assert l.balance("miner2") == 200


def test_escrow_over_release():
    l = make_ledger()
    l.lock_escrow("job2", "alice", 100)
    l.release_escrow("job2", "miner1", 100)
    with pytest.raises(LedgerError, match="exceeds available"):
        l.release_escrow("job2", "miner1", 1)


def test_escrow_refund():
    l = make_ledger()
    l.lock_escrow("job3", "alice", 200)
    l.release_escrow("job3", "miner1", 50)
    l.refund_escrow("job3")
    assert l.balance("alice") == 1000 - 200 + 150  # 950


def test_escrow_insufficient_balance():
    l = make_ledger()
    with pytest.raises(LedgerError, match="Insufficient"):
        l.lock_escrow("job4", "alice", 9999)


# ── Stakes ────────────────────────────────────────────────────────────

def test_lock_stake_deducts_balance():
    l = make_ledger()
    l.lock_stake("alice", 100)
    assert l.balance("alice") == 900
    assert l.get_stake("alice") == 100


def test_release_stake_returns_balance():
    l = make_ledger()
    l.lock_stake("alice", 100)
    l.release_stake("alice")
    assert l.balance("alice") == 1000
    assert l.get_stake("alice") == 0


def test_slash_reduces_stake():
    l = make_ledger()
    l.lock_stake("alice", 100)
    slashed = l.slash_stake("alice", 10)
    assert slashed == 10
    assert l.get_stake("alice") == 90


def test_release_after_slash_returns_remainder():
    l = make_ledger()
    l.lock_stake("alice", 100)
    l.slash_stake("alice", 30)
    l.release_stake("alice")
    assert l.balance("alice") == 970   # 1000 - 30 burned


def test_slash_capped_at_available_stake():
    l = make_ledger()
    l.lock_stake("alice", 10)
    slashed = l.slash_stake("alice", 999)
    assert slashed == 10
    assert l.get_stake("alice") == 0


def test_slash_nonexistent_miner_returns_zero():
    l = make_ledger()
    assert l.slash_stake("nobody", 10) == 0


def test_lock_stake_insufficient_balance():
    l = make_ledger()
    with pytest.raises(LedgerError, match="Insufficient"):
        l.lock_stake("alice", 9999)


def test_release_nonexistent_stake_is_noop():
    l = make_ledger()
    l.release_stake("nobody")   # must not raise
    assert l.balance("alice") == 1000


# ── Stake-gated chunk dispatch ────────────────────────────────────────

from unbound.registry.registry import Registry as Reg
from unbound.uvm.opcodes import ADD, OUTPUT, HALT


def test_unstaked_miner_receives_open_job():
    r = Reg()
    r.create_job("alice", "", [[ADD, OUTPUT, HALT]], 0, min_miner_stake=0)
    assert r.next_available_chunk(miner_stake=0) is not None


def test_unstaked_miner_blocked_from_staked_job():
    r = Reg()
    r.create_job("alice", "", [[ADD, OUTPUT, HALT]], 0, min_miner_stake=50)
    assert r.next_available_chunk(miner_stake=0) is None


def test_staked_miner_receives_staked_job():
    r = Reg()
    r.create_job("alice", "", [[ADD, OUTPUT, HALT]], 0, min_miner_stake=50)
    assert r.next_available_chunk(miner_stake=50) is not None


def test_staked_miner_receives_open_job_too():
    r = Reg()
    r.create_job("alice", "", [[ADD, OUTPUT, HALT]], 0, min_miner_stake=0)
    assert r.next_available_chunk(miner_stake=100) is not None


def test_partial_stake_blocked():
    r = Reg()
    r.create_job("alice", "", [[ADD, OUTPUT, HALT]], 0, min_miner_stake=100)
    assert r.next_available_chunk(miner_stake=99) is None
