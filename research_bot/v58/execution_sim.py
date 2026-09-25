from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Iterable

from .contracts import Direction, require_aware_utc, require_finite_positive
from .targets import OHLCBar


@dataclass(frozen=True)
class EntryFill:
    entry_time: datetime
    reference_open: float
    fill_price: float
    fee_bps: float
    spread_bps: float
    slippage_bps: float


def next_bar_open_entry(
    *,
    decision_at: datetime,
    bars: Iterable[OHLCBar],
    direction: Direction,
    fee_bps: float = 0.0,
    spread_bps: float = 0.0,
    slippage_bps: float = 0.0,
) -> EntryFill:
    decision = require_aware_utc(decision_at, name="decision_at")
    for value, name in ((fee_bps, "fee_bps"), (spread_bps, "spread_bps"), (slippage_bps, "slippage_bps")):
        if not math.isfinite(float(value)) or float(value) < 0:
            raise ValueError(f"{name} must be finite and non-negative")
    candidates = sorted(list(bars), key=lambda b: b.timestamp)
    bar = next((b for b in candidates if require_aware_utc(b.timestamp, name="bar.timestamp") > decision), None)
    if bar is None:
        raise ValueError("no executable next bar")
    reference = require_finite_positive(bar.open, name="bar.open")
    adverse_bps = float(spread_bps) / 2.0 + float(slippage_bps)
    sign = 1.0 if direction is Direction.LONG else -1.0
    fill = reference * (1.0 + sign * adverse_bps / 10_000.0)
    return EntryFill(
        entry_time=require_aware_utc(bar.timestamp, name="bar.timestamp"),
        reference_open=reference,
        fill_price=float(fill),
        fee_bps=float(fee_bps),
        spread_bps=float(spread_bps),
        slippage_bps=float(slippage_bps),
    )


def round_trip_cost_fraction(*, fee_bps: float, spread_bps: float, slippage_bps: float) -> float:
    values = (fee_bps, spread_bps, slippage_bps)
    if not all(math.isfinite(float(v)) and float(v) >= 0 for v in values):
        raise ValueError("cost inputs must be finite and non-negative")
    return 2.0 * (float(fee_bps) + float(spread_bps) / 2.0 + float(slippage_bps)) / 10_000.0
