"""
UBD Token Ledger

Tracks balances, escrow, and transactions in SQLite.
Tokens only move when real work completes — no empty-block rewards.
"""

import sqlite3
import threading
from pathlib import Path
from typing import Optional


class LedgerError(Exception):
    pass


class Ledger:
    def __init__(self, db_path: str = ":memory:"):
        self._db_path = db_path
        self._local = threading.local()
        self._init_db()

    @property
    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self):
        with self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS balances (
                    address TEXT PRIMARY KEY,
                    balance INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS escrow (
                    escrow_id TEXT PRIMARY KEY,
                    owner     TEXT NOT NULL,
                    amount    INTEGER NOT NULL,
                    released  INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS stakes (
                    miner_id TEXT PRIMARY KEY,
                    amount   INTEGER NOT NULL,
                    slashed  INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS transactions (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts        REAL NOT NULL DEFAULT (unixepoch('now', 'subsec')),
                    from_addr TEXT,
                    to_addr   TEXT,
                    amount    INTEGER NOT NULL,
                    memo      TEXT
                );
            """)

    # ── Balances ─────────────────────────────────────────────────────

    def balance(self, address: str) -> int:
        row = self._conn.execute(
            "SELECT balance FROM balances WHERE address = ?", (address,)
        ).fetchone()
        return row["balance"] if row else 0

    def credit(self, address: str, amount: int, memo: str = ""):
        if amount <= 0:
            raise LedgerError("Credit amount must be positive")
        with self._conn:
            self._conn.execute(
                "INSERT INTO balances(address, balance) VALUES(?,?) "
                "ON CONFLICT(address) DO UPDATE SET balance = balance + ?",
                (address, amount, amount),
            )
            self._conn.execute(
                "INSERT INTO transactions(from_addr, to_addr, amount, memo) VALUES(?,?,?,?)",
                (None, address, amount, memo),
            )

    def transfer(self, from_addr: str, to_addr: str, amount: int, memo: str = ""):
        if amount <= 0:
            raise LedgerError("Transfer amount must be positive")
        with self._conn:
            bal = self.balance(from_addr)
            if bal < amount:
                raise LedgerError(
                    f"Insufficient balance: {from_addr} has {bal}, needs {amount}"
                )
            self._conn.execute(
                "UPDATE balances SET balance = balance - ? WHERE address = ?",
                (amount, from_addr),
            )
            self._conn.execute(
                "INSERT INTO balances(address, balance) VALUES(?,?) "
                "ON CONFLICT(address) DO UPDATE SET balance = balance + ?",
                (to_addr, amount, amount),
            )
            self._conn.execute(
                "INSERT INTO transactions(from_addr, to_addr, amount, memo) VALUES(?,?,?,?)",
                (from_addr, to_addr, amount, memo),
            )

    # ── Escrow ───────────────────────────────────────────────────────

    def lock_escrow(self, escrow_id: str, owner: str, amount: int):
        """Lock amount from owner into escrow for a job."""
        if amount <= 0:
            raise LedgerError("Escrow amount must be positive")
        with self._conn:
            bal = self.balance(owner)
            if bal < amount:
                raise LedgerError(
                    f"Insufficient balance for escrow: {owner} has {bal}, needs {amount}"
                )
            self._conn.execute(
                "UPDATE balances SET balance = balance - ? WHERE address = ?",
                (amount, owner),
            )
            self._conn.execute(
                "INSERT INTO escrow(escrow_id, owner, amount) VALUES(?,?,?)",
                (escrow_id, owner, amount),
            )
            self._conn.execute(
                "INSERT INTO transactions(from_addr, to_addr, amount, memo) VALUES(?,?,?,?)",
                (owner, f"escrow:{escrow_id}", amount, "job escrow lock"),
            )

    def release_escrow(self, escrow_id: str, to_addr: str, amount: int):
        """Release part of escrow to a miner upon chunk completion."""
        with self._conn:
            row = self._conn.execute(
                "SELECT * FROM escrow WHERE escrow_id = ?", (escrow_id,)
            ).fetchone()
            if not row:
                raise LedgerError(f"Escrow not found: {escrow_id}")
            available = row["amount"] - row["released"]
            if amount > available:
                raise LedgerError(
                    f"Escrow release exceeds available: {available} < {amount}"
                )
            self._conn.execute(
                "UPDATE escrow SET released = released + ? WHERE escrow_id = ?",
                (amount, escrow_id),
            )
            self._conn.execute(
                "INSERT INTO balances(address, balance) VALUES(?,?) "
                "ON CONFLICT(address) DO UPDATE SET balance = balance + ?",
                (to_addr, amount, amount),
            )
            self._conn.execute(
                "INSERT INTO transactions(from_addr, to_addr, amount, memo) VALUES(?,?,?,?)",
                (f"escrow:{escrow_id}", to_addr, amount, "chunk reward"),
            )

    def refund_escrow(self, escrow_id: str):
        """Refund all unreleased escrow back to owner."""
        with self._conn:
            row = self._conn.execute(
                "SELECT * FROM escrow WHERE escrow_id = ?", (escrow_id,)
            ).fetchone()
            if not row:
                raise LedgerError(f"Escrow not found: {escrow_id}")
            refund = row["amount"] - row["released"]
            if refund > 0:
                owner = row["owner"]
                self._conn.execute(
                    "INSERT INTO balances(address, balance) VALUES(?,?) "
                    "ON CONFLICT(address) DO UPDATE SET balance = balance + ?",
                    (owner, refund, refund),
                )
                self._conn.execute(
                    "INSERT INTO transactions(from_addr, to_addr, amount, memo) VALUES(?,?,?,?)",
                    (f"escrow:{escrow_id}", owner, refund, "escrow refund"),
                )
                self._conn.execute(
                    "UPDATE escrow SET released = amount WHERE escrow_id = ?",
                    (escrow_id,),
                )

    # ── Stakes ───────────────────────────────────────────────────────

    def lock_stake(self, miner_id: str, amount: int):
        """Lock amount from miner's balance as stake. Paid volunteers only."""
        if amount <= 0:
            raise LedgerError("Stake amount must be positive")
        with self._conn:
            bal = self.balance(miner_id)
            if bal < amount:
                raise LedgerError(
                    f"Insufficient balance to stake: {miner_id} has {bal}, needs {amount}"
                )
            self._conn.execute(
                "UPDATE balances SET balance = balance - ? WHERE address = ?",
                (amount, miner_id),
            )
            self._conn.execute(
                "INSERT INTO stakes(miner_id, amount) VALUES(?,?) "
                "ON CONFLICT(miner_id) DO UPDATE SET amount = amount + ?",
                (miner_id, amount, amount),
            )
            self._conn.execute(
                "INSERT INTO transactions(from_addr, to_addr, amount, memo) VALUES(?,?,?,?)",
                (miner_id, f"stake:{miner_id}", amount, "stake lock"),
            )

    def release_stake(self, miner_id: str):
        """Return un-slashed stake to miner's balance on clean disconnect."""
        with self._conn:
            row = self._conn.execute(
                "SELECT amount, slashed FROM stakes WHERE miner_id = ?", (miner_id,)
            ).fetchone()
            if not row:
                return
            returnable = row["amount"] - row["slashed"]
            if returnable > 0:
                self._conn.execute(
                    "INSERT INTO balances(address, balance) VALUES(?,?) "
                    "ON CONFLICT(address) DO UPDATE SET balance = balance + ?",
                    (miner_id, returnable, returnable),
                )
                self._conn.execute(
                    "INSERT INTO transactions(from_addr, to_addr, amount, memo) VALUES(?,?,?,?)",
                    (f"stake:{miner_id}", miner_id, returnable, "stake release"),
                )
            self._conn.execute("DELETE FROM stakes WHERE miner_id = ?", (miner_id,))

    def slash_stake(self, miner_id: str, amount: int) -> int:
        """Burn amount from miner's stake. Returns actual amount slashed.

        Slashed UBD is destroyed — it leaves the supply. This makes
        submitting invalid results directly costly for paid volunteers.
        """
        with self._conn:
            row = self._conn.execute(
                "SELECT amount, slashed FROM stakes WHERE miner_id = ?", (miner_id,)
            ).fetchone()
            if not row:
                return 0
            available = row["amount"] - row["slashed"]
            actual = min(amount, available)
            if actual <= 0:
                return 0
            self._conn.execute(
                "UPDATE stakes SET slashed = slashed + ? WHERE miner_id = ?",
                (actual, miner_id),
            )
            self._conn.execute(
                "INSERT INTO transactions(from_addr, to_addr, amount, memo) VALUES(?,?,?,?)",
                (f"stake:{miner_id}", "burned", actual, "slash — invalid result"),
            )
            return actual

    def get_stake(self, miner_id: str) -> int:
        """Return miner's current un-slashed stake."""
        row = self._conn.execute(
            "SELECT amount, slashed FROM stakes WHERE miner_id = ?", (miner_id,)
        ).fetchone()
        return (row["amount"] - row["slashed"]) if row else 0
