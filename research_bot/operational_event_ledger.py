"""Operational research JSONL events, independent of scientific milestones.

Single-host cooperating writers use an advisory lock, reload before appending,
flush and fsync. A SHA-256 chain detects content/reordering corruption, not
authenticity or removal of a valid suffix without an external checkpoint.
No event in this ledger promotes scientific status or supplies prospective data.
"""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path

from .hypothesis import canonical_json, utc_text


SCHEMA = "jamalian-operational-events-v3"
KINDS = {"HYPOTHESIS", "DECISION", "ARBITRATION", "SIMULATOR"}


class OperationalResearchEventLedger:
    def __init__(self, experiment_id: str, path: Path | None = None):
        if not isinstance(experiment_id, str) or not experiment_id.strip():
            raise ValueError("independent experiment_id required")
        if experiment_id.startswith(("v0.50", "v0.51", "overlap_arbitration_v51")):
            raise ValueError("frozen experiment namespaces are not permitted")
        self.experiment_id = experiment_id
        self.path = Path(path) if path is not None else None
        self._rows: list[dict] = []
        if self.path is not None and self.path.exists():
            import fcntl
            with self.path.open("r", encoding="utf-8") as stream:
                fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
                self._rows = self._read(stream)

    def _read(self, stream) -> list[dict]:
        rows, seen, previous, last_time = [], set(), "0" * 64, None
        for sequence, line in enumerate(stream, 1):
            if not line.endswith("\n"):
                raise ValueError("incomplete event line; manual recovery required")
            row = json.loads(line)
            digest = row.pop("event_sha256", None)
            if (row.get("schema") != SCHEMA or row.get("experiment_id") != self.experiment_id
                    or row.get("sequence") != sequence or row.get("previous_sha256") != previous
                    or row.get("kind") not in KINDS or not isinstance(row.get("payload"), dict)
                    or not isinstance(row.get("event_id"), str) or not row["event_id"].strip()
                    or row["event_id"] in seen
                    or digest != sha256(canonical_json(row).encode()).hexdigest()):
                raise ValueError("event ledger integrity or namespace failure")
            at = datetime.fromisoformat(row["timestamp"])
            utc_text(at)
            if last_time is not None and at < last_time:
                raise ValueError("event time moved backwards")
            row["event_sha256"] = digest
            rows.append(row)
            seen.add(row["event_id"])
            previous, last_time = digest, at
        return rows

    def append(self, *, event_id: str, kind: str, timestamp: datetime, payload: dict) -> dict:
        if not isinstance(event_id, str) or not event_id.strip() or kind not in KINDS:
            raise ValueError("event identity and permitted kind required")
        if not isinstance(payload, dict):
            raise ValueError("event payload must be a JSON object")
        # Detach and validate before touching a file.
        payload = json.loads(canonical_json(payload))
        ts = utc_text(timestamp)

        def append_to(rows, stream=None):
            existing = next((r for r in rows if r["event_id"] == event_id), None)
            if existing is not None:
                if (existing["kind"], existing["timestamp"], existing["payload"]) != (kind, ts, payload):
                    raise ValueError("event identity reused with different content")
                return json.loads(canonical_json(existing))
            if rows and timestamp < datetime.fromisoformat(rows[-1]["timestamp"]):
                raise ValueError("event time moved backwards")
            row = {"schema": SCHEMA, "experiment_id": self.experiment_id,
                "sequence": len(rows) + 1, "event_id": event_id, "kind": kind,
                "timestamp": ts, "payload": payload,
                "previous_sha256": rows[-1]["event_sha256"] if rows else "0" * 64}
            row["event_sha256"] = sha256(canonical_json(row).encode()).hexdigest()
            if stream is not None:
                stream.seek(0, os.SEEK_END)
                stream.write(canonical_json(row) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            rows.append(row)
            return json.loads(canonical_json(row))

        if self.path is None:
            return append_to(self._rows)
        import fcntl  # Persistence is supported on Unix; in-memory mode is portable.
        with self.path.open("a+", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            stream.seek(0)
            self._rows = self._read(stream)
            return append_to(self._rows, stream)

    def export(self) -> dict:
        if self.path is not None and self.path.exists():
            import fcntl
            with self.path.open("r", encoding="utf-8") as stream:
                fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
                self._rows = self._read(stream)
        return json.loads(canonical_json({"schema": SCHEMA, "experiment_id": self.experiment_id,
            "persistence": "JSONL_FSYNC" if self.path is not None else "RUN_LOCAL_MEMORY",
            "execution_authorized": False, "scientific_promotion": False,
            "records": self._rows}))
