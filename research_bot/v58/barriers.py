from __future__ import annotations

from dataclasses import dataclass
import math

from .contracts import Direction, require_finite_positive


@dataclass(frozen=True)
class BarrierSpec:
    """Frozen V58 primary H4 barrier contract inherited from v0.19."""

    timeframe: str = "4h"
    atr_period: int = 14
    stop_atr: float = 1.5
    min_stop_fraction: float = 0.0005
    reward_r: float = 3.0
    max_hold_bars_after_entry: int = 30
    entry_rule: str = "NEXT_EXECUTABLE_BAR_OPEN"
    same_bar_policy: str = "STOP_FIRST"
    timeout_exit_rule: str = "CLOSE_AT_ENTRY_PLUS_30_BARS"
    source_protocol: str = "v0.19 multitimeframe frozen H4 defaults"

    def __post_init__(self) -> None:
        if self.timeframe != "4h":
            raise ValueError("V58 primary barrier timeframe is frozen to 4h")
        if self.atr_period != 14:
            raise ValueError("V58 ATR period is frozen to 14")
        if not math.isclose(self.stop_atr, 1.5, rel_tol=0.0, abs_tol=0.0):
            raise ValueError("V58 stop_atr is frozen to 1.5")
        if not math.isclose(self.min_stop_fraction, 0.0005, rel_tol=0.0, abs_tol=0.0):
            raise ValueError("V58 min_stop_fraction is frozen to 0.0005")
        if not math.isclose(self.reward_r, 3.0, rel_tol=0.0, abs_tol=0.0):
            raise ValueError("V58 reward_r is frozen to 3.0")
        if self.max_hold_bars_after_entry != 30:
            raise ValueError("V58 max_hold_bars_after_entry is frozen to 30")
        if self.entry_rule != "NEXT_EXECUTABLE_BAR_OPEN":
            raise ValueError("V58 entry rule is frozen")
        if self.same_bar_policy != "STOP_FIRST":
            raise ValueError("V58 same-bar policy is frozen")


PRIMARY_H4_BARRIER = BarrierSpec()


@dataclass(frozen=True)
class BarrierPrices:
    entry_price: float
    atr_at_signal: float
    stop_distance: float
    stop_price: float
    target_price: float
    reward_r: float


def build_barrier_prices(
    *,
    entry_price: float,
    atr_at_signal: float,
    direction: Direction,
    spec: BarrierSpec = PRIMARY_H4_BARRIER,
) -> BarrierPrices:
    entry = require_finite_positive(entry_price, name="entry_price")
    atr = require_finite_positive(atr_at_signal, name="atr_at_signal")
    stop_distance = max(spec.stop_atr * atr, entry * spec.min_stop_fraction)
    side = 1.0 if direction is Direction.LONG else -1.0
    stop = entry - side * stop_distance
    target = entry + side * spec.reward_r * stop_distance
    require_finite_positive(stop, name="stop_price")
    require_finite_positive(target, name="target_price")
    return BarrierPrices(
        entry_price=entry,
        atr_at_signal=atr,
        stop_distance=float(stop_distance),
        stop_price=float(stop),
        target_price=float(target),
        reward_r=float(spec.reward_r),
    )
