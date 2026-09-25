from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import math
from typing import Iterable

from .contracts import Direction, StrategyArm, TargetClass, require_aware_utc, require_finite_positive
from .events import stable_hash
from .targets import OHLCBar


class OutcomeState(str, Enum):
    RESOLVED = "RESOLVED"
    RIGHT_CENSORED = "RIGHT_CENSORED"


@dataclass(frozen=True)
class BarrierPolicy:
    atr_multiple: float = 1.0
    minimum_distance_fraction: float = 0.0
    reward_r: float = 1.5
    holding_horizon_bars: int = 12
    ambiguity_policy: str = "STOP_FIRST"

    def __post_init__(self) -> None:
        for name in ("atr_multiple", "minimum_distance_fraction", "reward_r"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                if name == "minimum_distance_fraction" and value == 0:
                    continue
                raise ValueError(f"{name} must be finite and > 0")
        if isinstance(self.holding_horizon_bars, bool) or not isinstance(self.holding_horizon_bars, int):
            raise ValueError("holding_horizon_bars must be an integer")
        if self.holding_horizon_bars <= 0:
            raise ValueError("holding_horizon_bars must be > 0")
        if self.ambiguity_policy != "STOP_FIRST":
            raise ValueError("V58 primary policy must be STOP_FIRST")


@dataclass(frozen=True)
class DecisionEvent:
    event_id: str
    symbol: str
    venue: str
    strategy_arm: StrategyArm
    direction: Direction
    decision_time: datetime
    decision_atr: float
    feature_snapshot_id: str
    data_version: str
    code_version: str
    strategy_version: str

    def __post_init__(self) -> None:
        require_aware_utc(self.decision_time, name="decision_time")
        require_finite_positive(self.decision_atr, name="decision_atr")
        for name in (
            "event_id", "symbol", "venue", "feature_snapshot_id", "data_version",
            "code_version", "strategy_version",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")


@dataclass(frozen=True)
class EnteredEvent:
    entry_id: str
    event_id: str
    entry_time: datetime
    entry_price: float
    stop_price: float
    target_price: float
    risk_distance: float
    policy_hash: str


@dataclass(frozen=True)
class BarrierOutcome:
    state: OutcomeState
    target_class: TargetClass | None
    observed_bars: int
    resolved_at: datetime | None
    exit_price: float | None
    exit_reason: str
    intrabar_ambiguity: bool
    gross_return: float | None = None
    gross_return_r: float | None = None

    def after_cost(self, round_trip_cost_bps: float) -> float | None:
        if not math.isfinite(float(round_trip_cost_bps)) or round_trip_cost_bps < 0:
            raise ValueError("round_trip_cost_bps must be finite and non-negative")
        if self.gross_return is None:
            return None
        return self.gross_return - float(round_trip_cost_bps) / 10_000.0


def barrier_policy_hash(policy: BarrierPolicy) -> str:
    return stable_hash({
        "atr_multiple": policy.atr_multiple,
        "minimum_distance_fraction": policy.minimum_distance_fraction,
        "reward_r": policy.reward_r,
        "holding_horizon_bars": policy.holding_horizon_bars,
        "ambiguity_policy": policy.ambiguity_policy,
    })


def materialize_entry(
    event: DecisionEvent,
    *,
    entry_bar: OHLCBar,
    policy: BarrierPolicy,
) -> EnteredEvent:
    entry_time = require_aware_utc(entry_bar.timestamp, name="entry_bar.timestamp")
    if entry_time <= require_aware_utc(event.decision_time, name="decision_time"):
        raise ValueError("entry bar must be strictly after decision time")
    entry = require_finite_positive(entry_bar.open, name="entry_bar.open")
    distance = max(policy.atr_multiple * event.decision_atr, policy.minimum_distance_fraction * entry)
    if event.direction is Direction.LONG:
        stop = entry - distance
        target = entry + policy.reward_r * distance
    else:
        stop = entry + distance
        target = entry - policy.reward_r * distance
    require_finite_positive(stop, name="stop_price")
    require_finite_positive(target, name="target_price")
    policy_hash = barrier_policy_hash(policy)
    entry_id = stable_hash({
        "event_id": event.event_id,
        "entry_time": entry_time.isoformat(),
        "entry_price": entry,
        "policy_hash": policy_hash,
    })
    return EnteredEvent(entry_id, event.event_id, entry_time, entry, stop, target, distance, policy_hash)


def resolve_barriers(
    entered: EnteredEvent,
    *,
    direction: Direction,
    bars: Iterable[OHLCBar],
    policy: BarrierPolicy,
) -> BarrierOutcome:
    ordered = list(bars)
    if not ordered:
        return BarrierOutcome(OutcomeState.RIGHT_CENSORED, None, 0, None, None, "NO_FOLLOWUP", False)

    def resolved(label: TargetClass, n: int, ts: datetime, price: float, reason: str, ambiguous: bool = False) -> BarrierOutcome:
        sign = 1.0 if direction is Direction.LONG else -1.0
        gross = sign * (price / entered.entry_price - 1.0)
        gross_r = sign * (price - entered.entry_price) / entered.risk_distance
        return BarrierOutcome(OutcomeState.RESOLVED, label, n, ts, price, reason, ambiguous, gross, gross_r)
    previous: datetime | None = None
    for i, bar in enumerate(ordered[: policy.holding_horizon_bars], start=1):
        timestamp = require_aware_utc(bar.timestamp, name="bar.timestamp")
        if timestamp < entered.entry_time:
            raise ValueError("follow-up contains a pre-entry bar")
        if previous is not None and timestamp <= previous:
            raise ValueError("follow-up bars must be strictly chronological")
        previous = timestamp
        if direction is Direction.LONG:
            if bar.open <= entered.stop_price:
                return resolved(TargetClass.SL, i, timestamp, bar.open, "GAP_STOP")
            if bar.open >= entered.target_price:
                return resolved(TargetClass.TP, i, timestamp, entered.target_price, "GAP_TARGET")
            tp, sl = bar.high >= entered.target_price, bar.low <= entered.stop_price
        else:
            if bar.open >= entered.stop_price:
                return resolved(TargetClass.SL, i, timestamp, bar.open, "GAP_STOP")
            if bar.open <= entered.target_price:
                return resolved(TargetClass.TP, i, timestamp, entered.target_price, "GAP_TARGET")
            tp, sl = bar.low <= entered.target_price, bar.high >= entered.stop_price
        if sl:
            return resolved(TargetClass.SL, i, timestamp, entered.stop_price, "STOP", tp)
        if tp:
            return resolved(TargetClass.TP, i, timestamp, entered.target_price, "TARGET")
        if i == policy.holding_horizon_bars:
            return resolved(TargetClass.TIMEOUT, i, timestamp, bar.close, "TIMEOUT")
    return BarrierOutcome(OutcomeState.RIGHT_CENSORED, None, len(ordered), None, None, "DATA_END", False)
