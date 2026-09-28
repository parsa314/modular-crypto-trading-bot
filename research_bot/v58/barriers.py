from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import math
from typing import Iterable

from .contracts import Direction, StrategyArm, TargetClass, require_aware_utc, require_finite_positive
from .events import stable_hash
from .targets import OHLCBar


class OutcomeState(str, Enum):
    RESOLVED = "RESOLVED"
    RIGHT_CENSORED = "RIGHT_CENSORED"


def _validate_execution_contract(direction: Direction, bar_duration_seconds: int, market_type: str) -> None:
    if not isinstance(direction, Direction):
        raise ValueError("direction must be a Direction enum")
    if isinstance(bar_duration_seconds, bool) or not isinstance(bar_duration_seconds, int) or bar_duration_seconds <= 0:
        raise ValueError("bar_duration_seconds must be a positive integer")
    if market_type != "spot":
        raise ValueError("V58 currently authorizes only spot research entries")


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
    """Decision time is the closed signal bar's end, also the next bar's open."""

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
    bar_duration_seconds: int = 14_400
    market_type: str = "spot"

    def __post_init__(self) -> None:
        _validate_execution_contract(self.direction, self.bar_duration_seconds, self.market_type)
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
    direction: Direction = Direction.LONG
    bar_duration_seconds: int = 14_400
    market_type: str = "spot"

    def __post_init__(self) -> None:
        _validate_execution_contract(self.direction, self.bar_duration_seconds, self.market_type)
        if self.direction is Direction.SHORT:
            raise ValueError("spot short entries are not authorized")
        require_aware_utc(self.entry_time, name="entry_time")
        for name in ("entry_price", "stop_price", "target_price", "risk_distance"):
            require_finite_positive(getattr(self, name), name=name)
        if not self.stop_price < self.entry_price < self.target_price:
            raise ValueError("LONG requires stop < entry < target")


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
    if event.direction is Direction.SHORT:
        raise ValueError("spot short entries are not authorized")
    entry_time = require_aware_utc(entry_bar.timestamp, name="entry_bar.timestamp")
    # The historical close-to-next-open model has zero latency. A later bar
    # cannot stand in for a missing immediate open.
    if entry_time != require_aware_utc(event.decision_time, name="decision_time"):
        raise ValueError("entry must use the immediate next bar open at decision time")
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
        "direction": event.direction.value,
        "bar_duration_seconds": event.bar_duration_seconds,
        "market_type": event.market_type,
    })
    return EnteredEvent(entry_id, event.event_id, entry_time, entry, stop, target, distance, policy_hash,
                        event.direction, event.bar_duration_seconds, event.market_type)


def resolve_barriers(
    entered: EnteredEvent,
    *,
    direction: Direction,
    bars: Iterable[OHLCBar],
    policy: BarrierPolicy,
) -> BarrierOutcome:
    """Resolve a contiguous path; resolved_at is when the outcome is knowable.

    Gap fills are known at the open. With OHLC alone an intrabar touch has no
    exact execution timestamp, so its label is available only at bar close.
    """
    if not isinstance(direction, Direction) or direction is not entered.direction:
        raise ValueError("direction does not match the entered event")
    if entered.policy_hash != barrier_policy_hash(policy):
        raise ValueError("policy does not match the materialized entry")
    if not math.isclose(entered.entry_price - entered.stop_price, entered.risk_distance, rel_tol=1e-10):
        raise ValueError("entry stop does not match risk_distance")
    if not math.isclose(entered.target_price - entered.entry_price,
                        policy.reward_r * entered.risk_distance, rel_tol=1e-10):
        raise ValueError("entry target does not match policy reward_r")
    duration = timedelta(seconds=entered.bar_duration_seconds)
    entry_time = require_aware_utc(entered.entry_time, name="entry_time")
    ordered = list(bars)
    if not ordered:
        return BarrierOutcome(OutcomeState.RIGHT_CENSORED, None, 0, None, None, "NO_FOLLOWUP", False)

    def resolved(label: TargetClass, n: int, ts: datetime, price: float, reason: str, ambiguous: bool = False) -> BarrierOutcome:
        sign = 1.0 if direction is Direction.LONG else -1.0
        gross = sign * (price / entered.entry_price - 1.0)
        gross_r = sign * (price - entered.entry_price) / entered.risk_distance
        return BarrierOutcome(OutcomeState.RESOLVED, label, n, ts, price, reason, ambiguous, gross, gross_r)
    for i, bar in enumerate(ordered[: policy.holding_horizon_bars], start=1):
        timestamp = require_aware_utc(bar.timestamp, name="bar.timestamp")
        if timestamp != entry_time + (i - 1) * duration:
            raise ValueError("follow-up bars must be contiguous starting at entry")
        if i == 1 and bar.open != entered.entry_price:
            raise ValueError("entry bar open does not match materialized entry price")
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
            return resolved(TargetClass.SL, i, timestamp + duration, entered.stop_price, "STOP", tp)
        if tp:
            return resolved(TargetClass.TP, i, timestamp + duration, entered.target_price, "TARGET")
        if i == policy.holding_horizon_bars:
            return resolved(TargetClass.TIMEOUT, i, timestamp + duration, bar.close, "TIMEOUT")
    return BarrierOutcome(OutcomeState.RIGHT_CENSORED, None, len(ordered), None, None, "DATA_END", False)
