from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Iterable

from .contracts import Direction, TargetClass, require_aware_utc, require_finite_positive
from .events import EventRecord


@dataclass(frozen=True)
class OHLCBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float

    def __post_init__(self) -> None:
        require_aware_utc(self.timestamp, name="bar.timestamp")
        o = require_finite_positive(self.open, name="open")
        h = require_finite_positive(self.high, name="high")
        l = require_finite_positive(self.low, name="low")
        c = require_finite_positive(self.close, name="close")
        if l > min(o, c) or h < max(o, c) or h < l:
            raise ValueError("invalid OHLC geometry")


@dataclass(frozen=True)
class TargetResolution:
    target_class: TargetClass
    time_to_event_bars: int
    resolved_at: datetime
    intrabar_ambiguity: bool
    ambiguity_policy: str = "STOP_FIRST"


def _touches(event: EventRecord, bar: OHLCBar) -> tuple[bool, bool]:
    if event.direction is Direction.LONG:
        return bar.high >= event.target_price, bar.low <= event.stop_price
    return bar.low <= event.target_price, bar.high >= event.stop_price


def resolve_target(event: EventRecord, bars: Iterable[OHLCBar]) -> TargetResolution:
    ordered = list(bars)
    if not ordered:
        raise ValueError("at least one post-entry bar is required")
    if len(ordered) < event.holding_horizon:
        raise ValueError("insufficient bars to resolve frozen holding horizon")
    prior = None
    for i, bar in enumerate(ordered[: event.holding_horizon], start=1):
        ts = require_aware_utc(bar.timestamp, name="bar.timestamp")
        if ts < require_aware_utc(event.entry_time, name="entry_time"):
            raise ValueError("target path contains a pre-entry bar")
        if prior is not None and ts <= prior:
            raise ValueError("bars must be strictly chronological")
        prior = ts
        tp, sl = _touches(event, bar)
        if tp and sl:
            return TargetResolution(TargetClass.SL, i, ts, True, "STOP_FIRST")
        if sl:
            return TargetResolution(TargetClass.SL, i, ts, False, "STOP_FIRST")
        if tp:
            return TargetResolution(TargetClass.TP, i, ts, False, "STOP_FIRST")
    final_ts = require_aware_utc(ordered[event.holding_horizon - 1].timestamp, name="bar.timestamp")
    return TargetResolution(TargetClass.TIMEOUT, event.holding_horizon, final_ts, False, "STOP_FIRST")
