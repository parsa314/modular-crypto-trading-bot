from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .hashing import canonical_json, stable_hash


class EvidenceLedger:
    """Append-only decision ledger with chained hashes.

    The class does not mutate previous records. Each record contains the hash of
    its predecessor so accidental reordering or deletion is detectable.
    """

    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []

    def append(self, *, record_type: str, payload: Any, recorded_at: datetime) -> dict[str, Any]:
        if not isinstance(record_type, str) or not record_type.strip():
            raise ValueError("record_type is required")
        if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
            raise ValueError("recorded_at must be timezone-aware")
        normalized_payload = asdict(payload) if is_dataclass(payload) else payload
        previous_hash = self._records[-1]["record_hash"] if self._records else "GENESIS"
        envelope = {
            "sequence": len(self._records),
            "record_type": record_type,
            "recorded_at": recorded_at.astimezone(timezone.utc).isoformat(),
            "previous_hash": previous_hash,
            "payload": normalized_payload,
        }
        envelope["record_hash"] = stable_hash(envelope)
        self._records.append(envelope)
        return dict(envelope)

    @property
    def records(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(row) for row in self._records)

    @property
    def ledger_hash(self) -> str:
        return stable_hash(self._records)

    def verify(self) -> bool:
        previous = "GENESIS"
        for index, row in enumerate(self._records):
            if row["sequence"] != index or row["previous_hash"] != previous:
                return False
            copy = dict(row)
            observed = copy.pop("record_hash")
            if stable_hash(copy) != observed:
                return False
            previous = observed
        return True

    def write_immutable(self, path: Path) -> None:
        if path.exists():
            raise FileExistsError(f"refusing to overwrite evidence ledger: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "records": self._records,
            "ledger_hash": self.ledger_hash,
        }
        path.write_bytes(canonical_json(payload) + b"\n")
