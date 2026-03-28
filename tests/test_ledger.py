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
