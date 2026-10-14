"""Persistent same-host DEMO order admission circuit breaker.

The SQLite WAL DB *must* be on a single locally lockable shared path for all
workers of one MT5 account. An EXCLUSIVE write transaction is held across one
broker order submission; this serializes admission between cooperating local
processes. This is NOT a distributed or cross-machine governor and it cannot
guarantee loss limits against slippage, gaps, crashes, untracked brokers or
external orders. BROKER DEMO account validation happens in the caller.

A terminally blocked account does not auto-reset. Manual independent review
and an audited recovery process are required (none implemented here).
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3
from typing import Iterator


class GlobalDemoHalt(RuntimeError):
    """A mandatory P0 safety check vetoed any new MT5 demo order."""


def _positive(value: object) -> Decimal:
    try:
        x = Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise GlobalDemoHalt("INVALID_BROKER_EQUITY_OR_ANCHOR") from exc
    if not x.is_finite() or x <= 0:
        raise GlobalDemoHalt("INVALID_BROKER_EQUITY_OR_ANCHOR")
    return x


@dataclass(frozen=True)
class GlobalDemoState:
    account_key: str
    day_utc: str
    equity_anchor: str
    consecutive_execution_errors: int
    halted: bool
    reason: str


class _Admission:
    def __init__(self, cx: sqlite3.Connection, key: str):
        self.cx = cx
        self.key = key
        self.completed = False

    def _update(self, *, errors: int, halted: bool, reason: str) -> None:
        if self.completed:
            raise RuntimeError("execution outcome already finalized")
        self.cx.execute(
            "UPDATE demo_kill_state SET consecutive_errors=?, halted=?, reason=? WHERE account_key=?",
            (errors, int(halted), reason, self.key),
        )
        self.completed = True

    def execution_success(self) -> None:
        """Only a confirmed full demo fill resets the consecutive error count."""
        self._update(errors=0, halted=False, reason="")

    def execution_error(self) -> None:
        """Failures are cumulative across cooperating workers and process restarts."""
        row = self.cx.execute(
            "SELECT consecutive_errors FROM demo_kill_state WHERE account_key=?",
            (self.key,),
        ).fetchone()
        if row is None:
            raise RuntimeError("missing persistent risk row")
        errors = int(row[0]) + 1
        self._update(
            errors=errors,
            halted=errors >= 3,
            reason="THREE_CONSECUTIVE_EXECUTION_ERRORS" if errors >= 3 else "",
        )

    def execution_uncertain(self) -> None:
        """Unknown/partial acknowledgement requires manual broker reconciliation."""
        row = self.cx.execute(
            "SELECT consecutive_errors FROM demo_kill_state WHERE account_key=?",
            (self.key,),
        ).fetchone()
        self._update(errors=int(row[0]), halted=True, reason="EXECUTION_OUTCOME_UNCERTAIN")


class PersistentMT5DemoKillSwitch:
    """Single-host cooperative, persistent and account-keyed new-entry veto.

    Both errors and equity kill-state are retained across restarts. No
    self-serve reset or automatic next-day unhalt API is exposed.
    """

    def __init__(self, path: str | Path):
        s = str(path).strip()
        if not s or s == ":memory:" or s.startswith("file:"):
            raise GlobalDemoHalt("NONPERSISTENT_OR_INVALID_RISK_DATABASE")
        self.path = Path(s).expanduser().resolve()

    def _connect(self) -> sqlite3.Connection:
        # A missing directory/file is NOT silently repaired for operator safety.
        # The agent is responsible for provisioning this durable DB location.
        if not self.path.parent.is_dir():
            raise GlobalDemoHalt("MISSING_DURABLE_RISK_DIRECTORY")
        try:
            cx = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
            cx.execute("PRAGMA busy_timeout=10000")
            cx.execute("PRAGMA journal_mode=WAL")
            cx.execute(
                "CREATE TABLE IF NOT EXISTS demo_kill_state ("
                "account_key TEXT PRIMARY KEY, day_utc TEXT NOT NULL, "
                "anchor TEXT NOT NULL, consecutive_errors INTEGER NOT NULL DEFAULT 0,"
                "halted INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '')"
            )
            return cx
        except sqlite3.Error as exc:
            raise GlobalDemoHalt("DURABLE_RISK_STORE_UNAVAILABLE") from exc

    @contextmanager
    def admission(
        self, *, account_key: str, equity: object, day_start_equity: object,
        day_start_date_utc: str, now: datetime,
    ) -> Iterator[_Admission]:
        """Serialize risk admission and one DEMO order on this SQLite DB.

        Do not send an order without first entering this context. If execution
        succeeds, call `execution_success`; if broker raises call
        `execution_error`; if order state is not a confirmed FULL fill,
        call `execution_uncertain`. All operations are transactionally durable.
        """
        if not account_key or ":" not in account_key or len(account_key) > 200:
            raise GlobalDemoHalt("INVALID_BROKER_ACCOUNT_IDENTITY")
        if now.tzinfo is None or now.utcoffset() is None:
            raise GlobalDemoHalt("NAIVE_DECISION_TIMESTAMP")
        today = now.astimezone(timezone.utc).date().isoformat()
        if day_start_date_utc != today:
            raise GlobalDemoHalt("MISSING_OR_STALE_DAY_START_EQUITY")
        eq, anchor = _positive(equity), _positive(day_start_equity)
        cx = self._connect()
        begun = False
        try:
            cx.execute("BEGIN IMMEDIATE")
            begun = True
            row = cx.execute(
                "SELECT day_utc, anchor, consecutive_errors, halted, reason "
                "FROM demo_kill_state WHERE account_key=?",
                (account_key,),
            ).fetchone()
            if row is None:
                cx.execute(
                    "INSERT INTO demo_kill_state(account_key,day_utc,anchor) VALUES(?,?,?)",
                    (account_key, today, str(anchor)),
                )
            else:
                if bool(row[3]):
                    raise GlobalDemoHalt("GLOBAL_DEMO_KILL_LATCHED:" + str(row[4]))
                if row[0] == today and Decimal(row[1]) != anchor:
                    # Mid-day baseline tampering must not silently raise risk budget.
                    cx.execute(
                        "UPDATE demo_kill_state SET halted=1,reason=? WHERE account_key=?",
                        ("INCONSISTENT_DAILY_EQUITY_ANCHOR", account_key),
                    )
                    cx.execute("COMMIT")
                    begun = False
                    raise GlobalDemoHalt("INCONSISTENT_DAILY_EQUITY_ANCHOR")
                if row[0] != today:
                    # Consecutive broker failures persist over day changes.
                    cx.execute(
                        "UPDATE demo_kill_state SET day_utc=?,anchor=? WHERE account_key=?",
                        (today, str(anchor), account_key),
                    )
            if eq <= anchor * Decimal("0.90"):
                cx.execute(
                    "UPDATE demo_kill_state SET halted=1,reason=? WHERE account_key=?",
                    ("DAILY_EQUITY_DRAWDOWN_10_PERCENT", account_key),
                )
                cx.execute("COMMIT")
                begun = False
                raise GlobalDemoHalt("DAILY_EQUITY_DRAWDOWN_10_PERCENT")
            lease = _Admission(cx, account_key)
            yield lease
            if not lease.completed:
                # Attempt to submit without a recorded outcome = fail closed.
                cx.execute(
                    "UPDATE demo_kill_state SET halted=1,reason=? WHERE account_key=?",
                    ("MISSING_BROKER_EXECUTION_OUTCOME", account_key),
                )
            cx.execute("COMMIT")
            begun = False
        except GlobalDemoHalt:
            if begun:
                cx.execute("ROLLBACK")
            raise
        except (sqlite3.Error, Exception) as exc:
            if begun:
                try:
                    cx.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            # Unknown DB error always rejects future new entries at caller.
            if isinstance(exc, sqlite3.Error):
                raise GlobalDemoHalt("DURABLE_RISK_STORE_FAILURE") from exc
            raise
        finally:
            cx.close()

    def snapshot(self, *, account_key: str) -> GlobalDemoState | None:
        cx = self._connect()
        try:
            row = cx.execute(
                "SELECT account_key,day_utc,anchor,consecutive_errors,halted,reason "
                "FROM demo_kill_state WHERE account_key=?",
                (account_key,),
            ).fetchone()
            if row is None:
                return None
            return GlobalDemoState(str(row[0]), str(row[1]), str(row[2]),
                                   int(row[3]), bool(row[4]), str(row[5]))
        finally:
            cx.close()
