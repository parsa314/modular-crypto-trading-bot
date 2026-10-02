from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .hashing import canonical_json, stable_hash


class EvidenceLedger:
    """Append-only decision ledger with chained hashes.

    Payloads are deep-copied at ingress and egress so callers cannot mutate the
    internal chain through shared references. Existing evidence files are never
    overwritten.
    """

    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []

    def append(self, *, record_type: str, payload: Any, recorded_at: datetime) -> dict[str, Any]:
        if not isinstance(record_type, str) or not record_type.strip():
            raise ValueError("record_type is required")
        if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
            raise ValueError("recorded_at must be timezone-aware")
        normalized_payload = asdict(payload) if is_dataclass(payload) else deepcopy(payload)
        previous_hash = self._records[-1]["record_hash"] if self._records else "GENESIS"
        envelope = {
            "sequence": len(self._records),
            "record_type": record_type,
            "recorded_at": recorded_at.astimezone(timezone.utc).isoformat(),
            "previous_hash": previous_hash,
            "payload": deepcopy(normalized_payload),
        }
        envelope["record_hash"] = stable_hash(envelope)
        self._records.append(deepcopy(envelope))
        return deepcopy(envelope)

    @property
    def records(self) -> tuple[dict[str, Any], ...]:
        return tuple(deepcopy(row) for row in self._records)

    @property
    def ledger_hash(self) -> str:
        return stable_hash(self._records)

    def verify(self) -> bool:
        previous = "GENESIS"
        for index, row in enumerate(self._records):
            if row["sequence"] != index or row["previous_hash"] != previous:
                return False
            copy = deepcopy(row)
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
            "records": deepcopy(self._records),
            "ledger_hash": self.ledger_hash,
        }
        path.write_bytes(canonical_json(payload) + b"\n")
