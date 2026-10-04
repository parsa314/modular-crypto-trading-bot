"""Durable local order reservations, risk state and immutable closed-bar history.

An order claim commits ``SUBMITTING`` before its caller contacts an exchange.
Every claimed ID remains reserved, including after a crash or a terminal result.
The controller must hold ``session_lock()`` across its entire read/decide/network/
persist cycle. SQLite transactions also make individual reservations atomic.

This module stores public identifiers and normalized execution data only. It
rejects credential fields recursively; callers must never put credentials or
raw authenticated network responses inside otherwise harmless string fields.
It neither reads credentials nor contacts an exchange or enables execution.
The journal must reside on a local filesystem supporting SQLite and flock.
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import math
from numbers import Integral, Real
import os
from pathlib import Path
import sqlite3
import stat
import threading


_STATES = frozenset({"SUBMITTING", "UNKNOWN", "OPEN", "PARTIAL", "FILLED", "CANCELLED", "REJECTED"})
_PENDING = ("SUBMITTING", "UNKNOWN", "OPEN", "PARTIAL")
_TRANSITIONS = {
    "SUBMITTING": _STATES,
    "UNKNOWN": _STATES - {"SUBMITTING"},
    "OPEN": frozenset({"OPEN", "UNKNOWN", "PARTIAL", "FILLED", "CANCELLED", "REJECTED"}),
    "PARTIAL": frozenset({"PARTIAL", "UNKNOWN", "FILLED", "CANCELLED"}),
    "FILLED": frozenset({"FILLED"}),
    "CANCELLED": frozenset({"CANCELLED"}),
    "REJECTED": frozenset({"REJECTED"}),
}
_CREDENTIAL_FIELDS = frozenset({
    "apikey", "xapikey", "apisecret", "secret", "secretkey", "password", "passphrase",
    "authorization", "accesstoken", "refreshtoken", "token", "privatekey",
    "credentials", "credential", "cookie", "setcookie", "signature",
})
_TABLES = {"live_metadata", "live_decisions", "live_orders", "live_state", "live_candles"}
_CANDLE_FIELDS = ("timestamp_ms", "open", "high", "low", "close", "volume")


def _public_json(value: dict) -> str:
    if not isinstance(value, dict):
        raise ValueError("ledger metadata must be a dictionary")
    seen = set()

    def check(item):
        if isinstance(item, (dict, list, tuple)):
            if id(item) in seen:
                raise ValueError("ledger metadata cannot contain cycles")
            seen.add(id(item))
            if isinstance(item, dict):
                for key, child in item.items():
                    if not isinstance(key, str):
                        raise ValueError("ledger metadata keys must be strings")
                    normalized = "".join(c.lower() for c in key if c.isalnum())
                    if normalized in _CREDENTIAL_FIELDS:
                        raise ValueError("credentials must not be persisted in the ledger")
                    check(child)
            else:
                for child in item:
                    check(child)
            seen.remove(id(item))

    try:
        check(value)
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, RecursionError, OverflowError) as exc:
        raise ValueError("ledger metadata must be finite, public JSON data") from exc


def _identifier(value: str, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a nonempty string without surrounding whitespace")
    return value


def _number(value, name: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name} must be finite and >= {minimum}")
    return float(value)


def _positive_int(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return int(value)


class LiveLedger:
    """SQLite journal pinned to one exact public account/model/mode identity.

    ``get_order`` and ``pending_orders`` return ``id``, ``client_order_id``,
    ``intent``, ``state`` and ``result``. ``result['filled']`` is the cumulative
    nonnegative filled quantity. Updates merge result fields so a reconciliation
    timeout retains any known exchange ID and fill quantity.

    Opening a journal does not grant an execution lock: use ``session_lock``.
    Separate instances may read data or make atomic claims; only one runner may
    hold the complete controller-cycle lock. Instances cannot be shared after
    a process fork; each process must open its own instance.
    """

    def __init__(self, path, identity: dict):
        identity_json = _public_json(identity)
        if not identity:
            raise ValueError("ledger identity must not be empty")
        if os.fspath(path) == ":memory:":
            raise ValueError("a persistent filesystem ledger path is required")
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        except FileExistsError:
            if not self.path.is_file():
                raise ValueError("ledger path must identify a regular file")
        else:
            os.close(descriptor)
        file_stat = self.path.stat()
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
            raise ValueError("ledger must be a regular file without hardlink aliases")
        self._inode = (file_stat.st_dev, file_stat.st_ino)
        self._guard = threading.RLock()
        self._pid = os.getpid()
        self._session_fd = None
        self._closed = False
        self._connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        try:
            # Reject foreign files or an account/model mismatch before changing
            # their persistent journal mode. Recheck inside the initialization
            # transaction as concurrent constructors can initialize a new file.
            existing_tables = {row[0] for row in self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )}
            if existing_tables:
                if existing_tables != _TABLES:
                    raise ValueError("existing file has an incompatible ledger schema")
                existing = self._connection.execute("SELECT schema_version,identity_json FROM live_metadata WHERE id=1").fetchone()
                if existing is None or existing["schema_version"] != 1 or existing["identity_json"] != identity_json:
                    raise ValueError("ledger identity or schema version does not match")
            journal_mode = self._connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if journal_mode.lower() != "wal":
                raise RuntimeError("ledger requires SQLite WAL support on this filesystem")
            self._connection.execute("PRAGMA synchronous=FULL")
            with self._transaction() as connection:
                tables = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )}
                if tables and tables != _TABLES:
                    raise ValueError("existing file has an incompatible ledger schema")
                connection.execute("CREATE TABLE IF NOT EXISTS live_metadata (id INTEGER PRIMARY KEY CHECK(id=1), schema_version INTEGER NOT NULL, identity_json TEXT NOT NULL, timeframe_ms INTEGER)")
                connection.execute("CREATE TABLE IF NOT EXISTS live_decisions (decision_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)")
                connection.execute("CREATE TABLE IF NOT EXISTS live_orders (client_order_id TEXT PRIMARY KEY, intent_json TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('SUBMITTING','UNKNOWN','OPEN','PARTIAL','FILLED','CANCELLED','REJECTED')), result_json TEXT NOT NULL)")
                connection.execute("CREATE TABLE IF NOT EXISTS live_state (id INTEGER PRIMARY KEY CHECK(id=1), state_json TEXT NOT NULL)")
                connection.execute("CREATE TABLE IF NOT EXISTS live_candles (timestamp_ms INTEGER PRIMARY KEY, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL)")
                metadata = connection.execute("SELECT schema_version, identity_json FROM live_metadata WHERE id=1").fetchone()
                if metadata is None:
                    if tables:
                        raise ValueError("existing ledger is missing its identity")
                    connection.execute("INSERT INTO live_metadata(id,schema_version,identity_json) VALUES(1,1,?)", (identity_json,))
                elif metadata["schema_version"] != 1 or metadata["identity_json"] != identity_json:
                    raise ValueError("ledger identity or schema version does not match")
        except BaseException:
            self.close()
            raise

    def _ensure_open(self):
        if os.getpid() != self._pid:
            raise RuntimeError("a ledger instance cannot be shared after a process fork")
        if self._closed:
            raise RuntimeError("ledger is closed")
        try:
            file_stat = self.path.stat()
        except OSError as exc:
            raise RuntimeError("ledger file was removed or replaced") from exc
        if (file_stat.st_dev, file_stat.st_ino) != self._inode or file_stat.st_nlink != 1:
            raise RuntimeError("ledger file was replaced or has hardlink aliases")

    @contextmanager
    def _transaction(self):
        with self._guard:
            self._ensure_open()
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
                self._connection.commit()
            except BaseException:
                try:
                    self._connection.rollback()
                except BaseException:
                    # A failed rollback must never leave a reusable connection
                    # holding changes whose durability is unknown.
                    self.close()
                raise

    @contextmanager
    def session_lock(self):
        """Take a nonblocking exclusive flock on the actual database inode."""
        with self._guard:
            self._ensure_open()
            if self._session_fd is not None:
                raise RuntimeError("ledger session lock is already held")
            descriptor = os.open(self.path, os.O_RDWR)
            try:
                file_stat = os.fstat(descriptor)
                if (file_stat.st_dev, file_stat.st_ino) != self._inode or file_stat.st_nlink != 1:
                    raise RuntimeError("ledger file was replaced or has hardlink aliases")
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                os.close(descriptor)
                raise RuntimeError("another runner holds the ledger session lock") from exc
            except BaseException:
                os.close(descriptor)
                raise
            self._session_fd = descriptor
        try:
            yield self
        finally:
            with self._guard:
                if self._session_fd == descriptor:
                    self._release_lock()

    def _release_lock(self):
        descriptor = self._session_fd
        self._session_fd = None
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def close(self):
        with self._guard:
            if os.getpid() != self._pid:
                raise RuntimeError("a ledger instance cannot be shared after a process fork")
            if not self._closed:
                self._closed = True
                try:
                    self._connection.close()
                finally:
                    self._release_lock()

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, exception_type, exception, traceback):
        self.close()

    def claim_decision(self, decision_id: str, payload: dict) -> bool:
        decision_id = _identifier(decision_id, "decision_id")
        encoded = _public_json(payload)
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO live_decisions(decision_id,payload_json) VALUES(?,?) ON CONFLICT(decision_id) DO NOTHING",
                (decision_id, encoded),
            )
            claimed = cursor.rowcount == 1
        return claimed

    def claim_order(self, client_order_id: str, intent: dict) -> bool:
        client_order_id = _identifier(client_order_id, "client_order_id")
        encoded = _public_json(intent)
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO live_orders(client_order_id,intent_json,state,result_json) VALUES(?,?,'SUBMITTING',?) ON CONFLICT(client_order_id) DO NOTHING",
                (client_order_id, encoded, _public_json({"filled": 0.0})),
            )
            claimed = cursor.rowcount == 1
        return claimed

    @staticmethod
    def _order(row):
        if row is None:
            return None
        return {"id": row["client_order_id"], "client_order_id": row["client_order_id"],
                "intent": json.loads(row["intent_json"]), "state": row["state"],
                "result": json.loads(row["result_json"])}

    def get_order(self, client_order_id: str) -> dict | None:
        client_order_id = _identifier(client_order_id, "client_order_id")
        with self._guard:
            self._ensure_open()
            return self._order(self._connection.execute(
                "SELECT * FROM live_orders WHERE client_order_id=?", (client_order_id,)
            ).fetchone())

    def pending_orders(self) -> list[dict]:
        with self._guard:
            self._ensure_open()
            rows = self._connection.execute(
                "SELECT * FROM live_orders WHERE state IN (?,?,?,?) ORDER BY rowid", _PENDING
            ).fetchall()
            return [self._order(row) for row in rows]

    def update_order(self, client_order_id: str, status: str, result: dict):
        client_order_id = _identifier(client_order_id, "client_order_id")
        if not isinstance(status, str) or status not in _STATES:
            raise ValueError("unknown order status")
        # Validate before opening a transaction, including nested credentials.
        normalized = json.loads(_public_json(result))
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM live_orders WHERE client_order_id=?", (client_order_id,)).fetchone()
            if row is None:
                raise KeyError("order ID has not been claimed")
            if status not in _TRANSITIONS[row["state"]]:
                raise ValueError(f"invalid order transition {row['state']} -> {status}")
            previous = json.loads(row["result_json"])
            old_filled = _number(previous.get("filled", 0.0), "filled")
            filled = _number(normalized.get("filled", old_filled), "filled")
            if filled < old_filled:
                raise ValueError("filled quantity cannot decrease")
            if status in ("SUBMITTING", "OPEN", "REJECTED") and filled > 0:
                raise ValueError("order status is inconsistent with positive filled quantity")
            if status in ("PARTIAL", "FILLED") and filled <= 0:
                raise ValueError("PARTIAL and FILLED orders require positive filled quantity")
            merged = {**previous, **normalized, "filled": filled}
            connection.execute("UPDATE live_orders SET state=?,result_json=? WHERE client_order_id=?",
                               (status, _public_json(merged), client_order_id))

    def get_state(self) -> dict:
        with self._guard:
            self._ensure_open()
            row = self._connection.execute("SELECT state_json FROM live_state WHERE id=1").fetchone()
            return {} if row is None else json.loads(row["state_json"])

    def set_state(self, state: dict):
        encoded = _public_json(state)
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO live_state(id,state_json) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET state_json=excluded.state_json",
                (encoded,),
            )

    @staticmethod
    def _candle(row: dict) -> dict:
        if not isinstance(row, dict) or set(row) != set(_CANDLE_FIELDS):
            raise ValueError("candle rows must contain exactly timestamp_ms, open, high, low, close and volume")
        timestamp = row["timestamp_ms"]
        if isinstance(timestamp, bool) or not isinstance(timestamp, Integral) or timestamp < 0:
            raise ValueError("candle timestamp_ms must be a nonnegative integer")
        result = {"timestamp_ms": int(timestamp)}
        for name in _CANDLE_FIELDS[1:]:
            result[name] = _number(row[name], f"candle {name}")
        if any(result[name] <= 0 for name in ("open", "high", "low", "close")):
            raise ValueError("candle prices must be positive")
        if result["high"] < max(result["open"], result["close"], result["low"]) or result["low"] > min(result["open"], result["close"], result["high"]):
            raise ValueError("candle OHLC bounds are inconsistent")
        return result

    def append_candles(self, rows: list[dict], timeframe_ms: int):
        """Atomically append regular closed bars without revising known history.

        A matching previously stored prefix is idempotent. Input duplicate or
        reversed timestamps, gaps, older prepends, revised OHLCV and a changed
        timeframe fail closed. Whether exchange bars are closed is established
        by the controller before it supplies these records.
        """
        timeframe_ms = _positive_int(timeframe_ms, "timeframe_ms")
        if not isinstance(rows, list):
            raise ValueError("candles must be supplied as a list")
        normalized = [self._candle(row) for row in rows]
        if any(right["timestamp_ms"] - left["timestamp_ms"] != timeframe_ms
               for left, right in zip(normalized, normalized[1:])):
            raise ValueError("candle batch must have unique sorted regular timestamps")
        with self._transaction() as connection:
            stored_timeframe = connection.execute("SELECT timeframe_ms FROM live_metadata WHERE id=1").fetchone()[0]
            if stored_timeframe is not None and stored_timeframe != timeframe_ms:
                raise ValueError("candle timeframe does not match this ledger")
            if not normalized:
                return
            first, last = connection.execute("SELECT MIN(timestamp_ms),MAX(timestamp_ms) FROM live_candles").fetchone()
            for candle in normalized:
                timestamp = candle["timestamp_ms"]
                existing = connection.execute("SELECT * FROM live_candles WHERE timestamp_ms=?", (timestamp,)).fetchone()
                if existing is not None:
                    if dict(existing) != candle:
                        raise ValueError("previously observed candle was revised")
                    continue
                if first is not None and timestamp < first:
                    raise ValueError("older candle history cannot be prepended")
                if last is not None and timestamp != last + timeframe_ms:
                    raise ValueError("new candle must be consecutive with existing history; gaps are forbidden")
                connection.execute(
                    "INSERT INTO live_candles(timestamp_ms,open,high,low,close,volume) VALUES(?,?,?,?,?,?)",
                    tuple(candle[name] for name in _CANDLE_FIELDS),
                )
                first = timestamp if first is None else first
                last = timestamp
            if stored_timeframe is None:
                connection.execute("UPDATE live_metadata SET timeframe_ms=? WHERE id=1", (timeframe_ms,))

    def candles(self) -> list[dict]:
        with self._guard:
            self._ensure_open()
            rows = self._connection.execute("SELECT * FROM live_candles ORDER BY timestamp_ms").fetchall()
            return [dict(row) for row in rows]
