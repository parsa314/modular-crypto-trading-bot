from __future__ import annotations

"""Append-only experience ledger for DEMO/shadow learning.

This module records model decisions and later outcomes without mutating the
currently deployed/champion model. Training rows are exposed only after the
corresponding outcome is causally available as of the requested cutoff.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping


class DemoExperienceError(RuntimeError):
    pass


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class DemoDecisionExperience:
    event_id: str
    decision_at: datetime
    canonical_symbol: str
    venue: str
    venue_symbol: str
    execution_domain: str
    strategy_version: str
    model_version: str
    feature_snapshot_id: str
    side: str
    expected_edge: float
    model_confidence: float
    regime: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _utc(self.decision_at, "decision_at")
        required = (
            self.event_id,
            self.canonical_symbol,
            self.venue,
            self.venue_symbol,
            self.execution_domain,
            self.strategy_version,
            self.model_version,
            self.feature_snapshot_id,
            self.side,
        )
        if not all(str(x).strip() for x in required):
            raise ValueError("decision experience contains an empty required field")
        if not math.isfinite(float(self.expected_edge)):
            raise ValueError("expected_edge must be finite")
        if not math.isfinite(float(self.model_confidence)):
            raise ValueError("model_confidence must be finite")
        if not 0.0 <= float(self.model_confidence) <= 1.0:
            raise ValueError("model_confidence must be in [0, 1]")


@dataclass(frozen=True)
class DemoOutcomeExperience:
    event_id: str
    outcome_at: datetime
    outcome: str
    realized_r: float
    realized_pnl: float
    realized_cost: float
    exit_price: float
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _utc(self.outcome_at, "outcome_at")
        if not str(self.event_id).strip():
            raise ValueError("event_id is required")
        if str(self.outcome).upper() not in {"TP", "SL", "TIMEOUT", "EXIT"}:
            raise ValueError("unsupported demo outcome")
        values = (
            self.realized_r,
            self.realized_pnl,
            self.realized_cost,
            self.exit_price,
        )
        if not all(math.isfinite(float(x)) for x in values):
            raise ValueError("outcome economics must be finite")
        if float(self.realized_cost) < 0.0:
            raise ValueError("realized_cost must be non-negative")
        if float(self.exit_price) <= 0.0:
            raise ValueError("exit_price must be positive")


class AppendOnlyDemoExperienceLedger:
    """JSONL ledger with decision/outcome causality and duplicate protection."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                raw = line.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise DemoExperienceError(
                        f"invalid JSONL at line {line_no}"
                    ) from exc
                if not isinstance(row, dict):
                    raise DemoExperienceError(
                        f"ledger row {line_no} is not an object"
                    )
                rows.append(row)
        return rows

    @staticmethod
    def _key(row: Mapping[str, Any]) -> tuple[str, str]:
        return str(row.get("event_id", "")), str(row.get("record_type", ""))

    def _append(self, row: dict[str, Any]) -> None:
        key = self._key(row)
        if not all(key):
            raise DemoExperienceError("ledger row key is incomplete")
        if any(self._key(existing) == key for existing in self._records()):
            raise DemoExperienceError(
                f"duplicate experience record event_id={key[0]} type={key[1]}"
            )

        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            row,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def record_decision(self, item: DemoDecisionExperience) -> None:
        row = asdict(item)
        row["record_type"] = "DECISION"
        row["decision_at"] = _utc(item.decision_at, "decision_at").isoformat()
        row["metadata"] = dict(item.metadata)
        self._append(row)

    def record_outcome(self, item: DemoOutcomeExperience) -> None:
        records = self._records()
        decisions = {
            str(row.get("event_id")): row
            for row in records
            if row.get("record_type") == "DECISION"
        }
        decision = decisions.get(item.event_id)
        if decision is None:
            raise DemoExperienceError(
                "cannot record an outcome before its decision exists"
            )
        decision_at = datetime.fromisoformat(str(decision["decision_at"]))
        outcome_at = _utc(item.outcome_at, "outcome_at")
        if outcome_at <= _utc(decision_at, "decision_at"):
            raise DemoExperienceError(
                "outcome_at must be strictly after decision_at"
            )

        row = asdict(item)
        row["record_type"] = "OUTCOME"
        row["outcome"] = str(item.outcome).upper()
        row["outcome_at"] = outcome_at.isoformat()
        row["metadata"] = dict(item.metadata)
        self._append(row)

    def training_rows(self, *, as_of: datetime) -> list[dict[str, Any]]:
        """Return only fully observed decision/outcome pairs available by cutoff."""

        cutoff = _utc(as_of, "as_of")
        records = self._records()
        decisions: dict[str, dict[str, Any]] = {}
        outcomes: dict[str, dict[str, Any]] = {}

        for row in records:
            event_id = str(row.get("event_id", ""))
            record_type = row.get("record_type")
            if record_type == "DECISION":
                decision_at = datetime.fromisoformat(str(row["decision_at"]))
                if _utc(decision_at, "decision_at") <= cutoff:
                    decisions[event_id] = row
            elif record_type == "OUTCOME":
                outcome_at = datetime.fromisoformat(str(row["outcome_at"]))
                if _utc(outcome_at, "outcome_at") <= cutoff:
                    outcomes[event_id] = row

        joined: list[dict[str, Any]] = []
        for event_id, decision in decisions.items():
            outcome = outcomes.get(event_id)
            if outcome is None:
                continue
            decision_at = _utc(
                datetime.fromisoformat(str(decision["decision_at"])),
                "decision_at",
            )
            outcome_at = _utc(
                datetime.fromisoformat(str(outcome["outcome_at"])),
                "outcome_at",
            )
            if outcome_at <= decision_at:
                raise DemoExperienceError(
                    f"non-causal experience pair: {event_id}"
                )
            joined.append(
                {
                    "event_id": event_id,
                    "decision": dict(decision),
                    "outcome": dict(outcome),
                }
            )

        joined.sort(
            key=lambda row: (
                row["decision"]["decision_at"],
                row["event_id"],
            )
        )
        return joined
