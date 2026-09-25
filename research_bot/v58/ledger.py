from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path


GENESIS_HASH = "0" * 64


def _hash_payload(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return sha256(raw).hexdigest()


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
            "payload": payload,
            "previous_hash": previous,
        }
        entry = LedgerEntry(**body, entry_hash=_hash_payload(body))
        self._entries.append(entry)
        return entry

    @property
    def entries(self) -> tuple[LedgerEntry, ...]:
        return tuple(self._entries)

    def verify(self) -> bool:
        previous = GENESIS_HASH
        for i, entry in enumerate(self._entries):
            if entry.sequence != i or entry.previous_hash != previous:
                return False
            body = asdict(entry)
            given = body.pop("entry_hash")
            if _hash_payload(body) != given:
                return False
            previous = entry.entry_hash
        return True

    def write_jsonl(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as fh:
            for entry in self._entries:
                fh.write(json.dumps(asdict(entry), sort_keys=True, ensure_ascii=False) + "\n")
