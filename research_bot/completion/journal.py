"""Shared BACKTEST/PAPER SQLite journal and transactional restart checkpoints.

Completion tables are additive. Unknown demo schemas are never rewritten.
The JournalPort is the seam for integrating an externally supplied TradeJournal.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Iterator, Protocol

from ..hypothesis import canonical_json, utc_text
from ..contracts import EvidenceStamp


class JournalPort(Protocol):
    def append(self, event_id: str, kind: str, at: datetime, payload: dict) -> dict: ...
    def checkpoint(self, name: str, payload: dict) -> None: ...
    def restore(self, name: str) -> dict | None: ...


class TradeJournal:
    def __init__(self, path: str | Path, experiment_id: str) -> None:
        if not experiment_id.strip() or experiment_id.startswith(("v0.50", "v0.51")):
            raise ValueError("independent experiment namespace required")
        self.experiment_id = experiment_id
        self.connection = sqlite3.connect(str(path), isolation_level=None)
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS completion_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id TEXT NOT NULL, event_id TEXT NOT NULL,
                kind TEXT NOT NULL, timestamp TEXT NOT NULL, payload TEXT NOT NULL,
                digest TEXT NOT NULL, UNIQUE(experiment_id,event_id));
            CREATE TABLE IF NOT EXISTS completion_checkpoints (
                experiment_id TEXT NOT NULL, name TEXT NOT NULL, payload TEXT NOT NULL,
                digest TEXT NOT NULL, PRIMARY KEY(experiment_id,name));
            CREATE TABLE IF NOT EXISTS completion_first_seen (
                experiment_id TEXT NOT NULL, snapshot_key TEXT NOT NULL, digest TEXT NOT NULL,
                observed_at TEXT NOT NULL, first_seen_at TEXT NOT NULL, data_class TEXT NOT NULL,
                clean INTEGER NOT NULL, PRIMARY KEY(experiment_id,snapshot_key));
        """)
        self._transaction_depth = 0

    @contextmanager
    def atomic(self) -> Iterator[None]:
        outer = self._transaction_depth == 0
        savepoint = f"completion_nested_{self._transaction_depth}"
        if outer:
            self.connection.execute("BEGIN IMMEDIATE")
        else:
            self.connection.execute("SAVEPOINT " + savepoint)
        self._transaction_depth += 1
        try:
            yield
            if outer:
                self.connection.execute("COMMIT")
            else:
                self.connection.execute("RELEASE SAVEPOINT " + savepoint)
        except BaseException:
            if outer:
                self.connection.execute("ROLLBACK")
            else:
                self.connection.execute("ROLLBACK TO SAVEPOINT " + savepoint)
                self.connection.execute("RELEASE SAVEPOINT " + savepoint)
            raise
        finally:
            self._transaction_depth -= 1

    def append(self, event_id: str, kind: str, at: datetime, payload: dict) -> dict:
        if not event_id.strip() or not kind.strip():
            raise ValueError("event identity/kind required")
        encoded, timestamp = canonical_json(payload), utc_text(at)
        digest = sha256(canonical_json([kind, timestamp, json.loads(encoded)]).encode()).hexdigest()
        with self.atomic():
            row = self.connection.execute("SELECT kind,timestamp,payload,digest FROM completion_events WHERE experiment_id=? AND event_id=?",
                (self.experiment_id, event_id)).fetchone()
            if row is not None:
                if row != (kind, timestamp, encoded, digest):
                    raise ValueError("event idempotency conflict")
            else:
                self.connection.execute("INSERT INTO completion_events(experiment_id,event_id,kind,timestamp,payload,digest) VALUES(?,?,?,?,?,?)",
                    (self.experiment_id, event_id, kind, timestamp, encoded, digest))
        return {"event_id": event_id, "kind": kind, "timestamp": timestamp,
                "payload": json.loads(encoded), "digest": digest}

    def checkpoint(self, name: str, payload: dict) -> None:
        encoded = canonical_json(payload)
        digest = sha256(encoded.encode()).hexdigest()
        with self.atomic():
            self.connection.execute("INSERT INTO completion_checkpoints VALUES(?,?,?,?) ON CONFLICT(experiment_id,name) DO UPDATE SET payload=excluded.payload,digest=excluded.digest",
                (self.experiment_id, name, encoded, digest))

    def restore(self, name: str) -> dict | None:
        row = self.connection.execute("SELECT payload,digest FROM completion_checkpoints WHERE experiment_id=? AND name=?",
            (self.experiment_id, name)).fetchone()
        if row is None:
            return None
        if sha256(row[0].encode()).hexdigest() != row[1]:
            raise ValueError("checkpoint integrity failure")
        return json.loads(row[0])

    def events(self, kind: str | None = None) -> tuple[dict, ...]:
        query = "SELECT event_id,kind,timestamp,payload,digest FROM completion_events WHERE experiment_id=?"
        params: tuple = (self.experiment_id,)
        if kind is not None:
            query += " AND kind=?"
            params += (kind,)
        rows = self.connection.execute(query + " ORDER BY sequence", params).fetchall()
        result = []
        for identity, event_kind, timestamp, payload, digest in rows:
            data = json.loads(payload)
            if sha256(canonical_json([event_kind, timestamp, data]).encode()).hexdigest() != digest:
                raise ValueError("event integrity failure")
            result.append({"event_id": identity, "kind": event_kind, "timestamp": timestamp,
                "payload": data, "digest": digest})
        return tuple(result)

    def close(self) -> None:
        self.connection.close()


class FirstSeenRegistry:
    def __init__(self, journal: TradeJournal, max_lag: timedelta = timedelta(seconds=60)) -> None:
        if max_lag <= timedelta(0):
            raise ValueError("positive first-seen lag required")
        self.journal, self.max_lag = journal, max_lag

    def register(self, snapshot_key: str, stamp: EvidenceStamp, payload: dict,
                 received_at: datetime, data_class: str) -> dict:
        if data_class not in {"SYNTHETIC", "HISTORICAL", "PROSPECTIVE"}:
            raise ValueError("explicit data class required")
        utc_text(received_at)
        if not snapshot_key.strip() or received_at < stamp.available_at:
            raise ValueError("snapshot identity or receipt time invalid")
        digest = sha256(canonical_json(payload).encode()).hexdigest()
        clean = (data_class == "PROSPECTIVE" and received_at - stamp.observed_at <= self.max_lag
                 and len(stamp.lineage) == 64 and stamp.reliability_tier == "EXCHANGE_VERIFIED")
        with self.journal.atomic():
            row = self.journal.connection.execute("SELECT digest,observed_at,first_seen_at,data_class,clean FROM completion_first_seen WHERE experiment_id=? AND snapshot_key=?",
                (self.journal.experiment_id, snapshot_key)).fetchone()
            if row is not None:
                if row[0] != digest or row[1] != utc_text(stamp.observed_at) or row[3] != data_class:
                    raise ValueError("snapshot revision or historical relabeling forbidden")
            else:
                row = (digest, utc_text(stamp.observed_at), utc_text(received_at), data_class, int(clean))
                self.journal.connection.execute("INSERT INTO completion_first_seen VALUES(?,?,?,?,?,?,?)",
                    (self.journal.experiment_id, snapshot_key, *row))
            record = {"snapshot_key": snapshot_key, "digest": row[0], "observed_at": row[1],
                "first_seen_at": row[2], "data_class": row[3], "prospective_clean": bool(row[4])}
            self.journal.append("first_seen:" + snapshot_key, "FIRST_SEEN", datetime.fromisoformat(row[2]), record)
        return record

    def report(self) -> dict:
        rows = self.journal.connection.execute("SELECT data_class,clean,COUNT(*) FROM completion_first_seen WHERE experiment_id=? GROUP BY data_class,clean",
            (self.journal.experiment_id,)).fetchall()
        total = sum(r[2] for r in rows)
        observed = [r[0] for r in self.journal.connection.execute("SELECT DISTINCT observed_at FROM completion_first_seen WHERE experiment_id=? ORDER BY observed_at", (self.journal.experiment_id,))]
        return {"count": total, "prospective_clean": total > 0 and all(r[0] == "PROSPECTIVE" and r[1] == 1 for r in rows),
            "observed_times": observed,
            "classes": {r[0] + ("_CLEAN" if r[1] else "_CONTEXT_ONLY"): r[2] for r in rows}}
