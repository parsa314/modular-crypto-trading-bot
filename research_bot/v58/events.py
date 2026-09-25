from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
import json
import math

from .contracts import Direction, RegimeLabel, StrategyArm, require_aware_utc, require_finite_positive


def _iso(value: datetime) -> str:
    return require_aware_utc(value, name="timestamp").isoformat()


def stable_hash(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return sha256(raw).hexdigest()


def make_event_id(
    *,
    symbol: str,
    venue: str,
    strategy_arm: StrategyArm,
    direction: Direction,
    setup_timestamp: datetime,
    entry_reference: str,
    feature_snapshot_id: str,
    strategy_version: str,
) -> str:
    payload = {
        "symbol": symbol.strip().upper(),
        "venue": venue.strip().lower(),
        "strategy_arm": strategy_arm.value,
        "direction": direction.value,
        "setup_timestamp": _iso(setup_timestamp),
        "entry_reference": entry_reference.strip(),
        "feature_snapshot_id": feature_snapshot_id.strip(),
        "strategy_version": strategy_version.strip(),
    }
    if not all(str(v).strip() for v in payload.values()):
        raise ValueError("event identity fields must be non-empty")
    return stable_hash(payload)


def make_feature_snapshot_id(
    *,
    event_timestamp: datetime,
    feature_version: str,
    features: dict[str, float | int | None],
) -> str:
    normalized: dict[str, float | int | None] = {}
    for key in sorted(features):
        value = features[key]
        if value is None:
            normalized[key] = None
        elif isinstance(value, bool):
            normalized[key] = int(value)
        else:
            f = float(value)
            if not math.isfinite(f):
                raise ValueError(f"non-finite feature: {key}")
            normalized[key] = f
    return stable_hash(
        {
            "event_timestamp": _iso(event_timestamp),
            "feature_version": feature_version.strip(),
            "features": normalized,
        }
    )


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    symbol: str
    venue: str
    timestamp: datetime
    setup_timestamp: datetime
    strategy_arm: StrategyArm
    direction: Direction
    entry_reference: str
    entry_time: datetime
    entry_price: float
    stop_price: float
    target_price: float
    risk_R: float
    holding_horizon: int
    decision_timeframe: str
    regime: RegimeLabel
    regime_confidence: float
    feature_snapshot_id: str
    data_version: str
    code_version: str
    strategy_version: str
    available_at: datetime
    source_event_hash: str

    def __post_init__(self) -> None:
        timestamp = require_aware_utc(self.timestamp, name="timestamp")
        setup = require_aware_utc(self.setup_timestamp, name="setup_timestamp")
        entry = require_aware_utc(self.entry_time, name="entry_time")
        available = require_aware_utc(self.available_at, name="available_at")
        if setup > timestamp:
            raise ValueError("setup_timestamp cannot be after event timestamp")
        if available > timestamp:
            raise ValueError("available_at cannot be after event timestamp")
        if entry <= timestamp:
            raise ValueError("entry_time must be strictly after event timestamp")
        entry_price = require_finite_positive(self.entry_price, name="entry_price")
        stop = require_finite_positive(self.stop_price, name="stop_price")
        target = require_finite_positive(self.target_price, name="target_price")
        if not math.isfinite(float(self.risk_R)) or float(self.risk_R) <= 0:
            raise ValueError("risk_R must be finite and > 0")
        if int(self.holding_horizon) <= 0:
            raise ValueError("holding_horizon must be > 0")
        if not 0.0 <= float(self.regime_confidence) <= 1.0:
            raise ValueError("regime_confidence must be in [0,1]")
        for name in (
            "event_id", "symbol", "venue", "entry_reference", "decision_timeframe",
            "feature_snapshot_id", "data_version", "code_version", "strategy_version",
            "source_event_hash",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if self.direction is Direction.LONG and not (stop < entry_price < target):
            raise ValueError("LONG requires stop < entry < target")
        if self.direction is Direction.SHORT and not (target < entry_price < stop):
            raise ValueError("SHORT requires target < entry < stop")
        expected = make_event_id(
            symbol=self.symbol,
            venue=self.venue,
            strategy_arm=self.strategy_arm,
            direction=self.direction,
            setup_timestamp=setup,
            entry_reference=self.entry_reference,
            feature_snapshot_id=self.feature_snapshot_id,
            strategy_version=self.strategy_version,
        )
        if self.event_id != expected:
            raise ValueError("event_id does not match immutable identity fields")

    def canonical_payload(self) -> dict:
        payload = asdict(self)
        for key in ("timestamp", "setup_timestamp", "entry_time", "available_at"):
            payload[key] = _iso(payload[key])
        payload["strategy_arm"] = self.strategy_arm.value
        payload["direction"] = self.direction.value
        payload["regime"] = self.regime.value
        return payload


class EventRegistry:
    def __init__(self) -> None:
        self._events: dict[str, EventRecord] = {}

    def add(self, event: EventRecord) -> None:
        if event.event_id in self._events:
            raise RuntimeError(f"duplicate event_id: {event.event_id}")
        self._events[event.event_id] = event

    def get(self, event_id: str) -> EventRecord:
        return self._events[event_id]

    def __len__(self) -> int:
        return len(self._events)
