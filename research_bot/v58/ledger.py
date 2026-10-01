from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path


GENESIS_HASH = "0" * 64


def _hash_payload(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode("utf-8")
    return sha256(raw).hexdigest()


def _detached_payload(payload: dict) -> dict:
    """Normalize and detach the JSON value before it enters the hash chain."""
    if not isinstance(payload, dict):
        raise ValueError("ledger payload must be a finite JSON object")
    try:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False)
        return json.loads(raw)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("ledger payload must be a finite JSON object") from exc


def _entry_copy(entry: "LedgerEntry") -> "LedgerEntry":
    return replace(entry, payload=_detached_payload(entry.payload))


@dataclass(frozen=True)
class LedgerEntry:
    sequence: int
    timestamp: str
    event_id: str
    stage: str
    status: str
    payload: dict
    previous_hash: str
    entry_hash: str


class EvidenceLedger:
    def __init__(self) -> None:
        self._entries: list[LedgerEntry] = []

    def append(self, *, event_id: str, stage: str, status: str, payload: dict, timestamp: datetime | None = None) -> LedgerEntry:
        if not event_id.strip() or not stage.strip() or not status.strip():
            raise ValueError("event_id, stage and status are required")
        ts = (timestamp or datetime.now(timezone.utc))
        if ts.tzinfo is None or ts.utcoffset() is None:
            raise ValueError("ledger timestamp must be timezone-aware")
        ts_iso = ts.astimezone(timezone.utc).isoformat()
        previous = self._entries[-1].entry_hash if self._entries else GENESIS_HASH
        body = {
            "sequence": len(self._entries),
            "timestamp": ts_iso,
            "event_id": event_id,
            "stage": stage,
            "status": status,
            "payload": _detached_payload(payload),
            "previous_hash": previous,
        }
        entry = LedgerEntry(**body, entry_hash=_hash_payload(body))
        self._entries.append(entry)
        # Callers can inspect or edit their copy without modifying evidence.
        return _entry_copy(entry)

    @property
    def entries(self) -> tuple[LedgerEntry, ...]:
        return tuple(_entry_copy(entry) for entry in self._entries)

    def verify(self) -> bool:
        previous = GENESIS_HASH
        for i, entry in enumerate(self._entries):
            if entry.sequence != i or entry.previous_hash != previous:
                return False
            try:
                body = asdict(entry)
                given = body.pop("entry_hash")
                matches = _hash_payload(body) == given
            except (TypeError, ValueError, OverflowError, RecursionError):
                return False
            if not matches:
                return False
            previous = entry.entry_hash
        return True

    def write_jsonl(self, path: str | Path) -> None:
        if not self.verify():
            raise ValueError("ledger integrity verification failed; export refused")
        lines = "".join(
            json.dumps(asdict(entry), sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
            for entry in self._entries
        )
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(lines, encoding="utf-8")
