from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Iterable

import numpy as np
import pandas as pd

from .barriers import PRIMARY_H4_BARRIER, build_barrier_prices
from .contracts import Direction, RegimeLabel, StrategyArm
from .events import EventRecord, make_event_id, make_feature_snapshot_id, stable_hash
from .features import add_v58_continuous_features
from .regimes import add_causal_regime


class BrooksSetup(str, Enum):
    B1_TREND_PULLBACK = "BRK_B1_TREND_PULLBACK"
    B2_BREAKOUT_FOLLOW_THROUGH = "BRK_B2_BREAKOUT_FOLLOW_THROUGH"
    B3_FAILED_BREAKOUT_REVERSAL = "BRK_B3_FAILED_BREAKOUT_REVERSAL"
    B4_TRADING_RANGE_FADE = "BRK_B4_TRADING_RANGE_FADE"
    B5_SIGNAL_BAR_QUALITY_STUDY = "BRK_B5_SIGNAL_BAR_QUALITY_STUDY"


_SETUP_CODE = {
    BrooksSetup.B1_TREND_PULLBACK: 1.0,
    BrooksSetup.B2_BREAKOUT_FOLLOW_THROUGH: 2.0,
    BrooksSetup.B3_FAILED_BREAKOUT_REVERSAL: 3.0,
    BrooksSetup.B4_TRADING_RANGE_FADE: 4.0,
    BrooksSetup.B5_SIGNAL_BAR_QUALITY_STUDY: 5.0,
}


@dataclass(frozen=True)
class BrooksConfig:
    """Frozen mechanical interpretation of the Phase-1 Al Brooks lab.

    These thresholds are engineering research defaults, not claimed optima.
    They must not be tuned on the sealed final holdout.
    """

    timeframe: str = "4h"
    range_window: int = 20
    trend_strength_min_atr: float = 0.25
    prior_micro_channel_min: int = 2
    pullback_min_atr: float = 0.25
    pullback_max_atr: float = 2.00
    signal_body_fraction_min: float = 0.50
    signal_close_location_long_min: float = 0.65
    signal_close_location_short_max: float = 0.35
    breakout_strength_min_atr: float = 0.10
    follow_through_body_fraction_min: float = 0.40
    failed_breakout_lifetime_bars: int = 2
    range_trend_abs_max_atr: float = 0.20
    range_overlap_min: float = 0.45
    range_width_max_atr: float = 6.0
    range_min_boundary_touches: int = 2
    range_boundary_tolerance_fraction: float = 0.10
    range_fade_tolerance_atr: float = 0.25

    def __post_init__(self) -> None:
        if self.timeframe != "4h":
            raise ValueError("Brooks primary timeframe is frozen to 4h")
        if self.range_window < 10:
            raise ValueError("range_window must be >= 10")
        if self.prior_micro_channel_min < 1:
            raise ValueError("prior_micro_channel_min must be >= 1")
        if self.failed_breakout_lifetime_bars not in (1, 2, 3):
            raise ValueError("failed_breakout_lifetime_bars must be 1..3")
        for name in (
            "trend_strength_min_atr",
            "pullback_min_atr",
            "pullback_max_atr",
            "signal_body_fraction_min",
            "signal_close_location_long_min",
            "signal_close_location_short_max",
            "breakout_strength_min_atr",
            "follow_through_body_fraction_min",
            "range_trend_abs_max_atr",
            "range_overlap_min",
            "range_width_max_atr",
            "range_boundary_tolerance_fraction",
            "range_fade_tolerance_atr",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if not 0 < self.signal_body_fraction_min <= 1:
            raise ValueError("signal_body_fraction_min must be in (0,1]")
        if not 0 <= self.signal_close_location_short_max < self.signal_close_location_long_min <= 1:
            raise ValueError("signal close-location thresholds invalid")
        if not 0 < self.range_overlap_min <= 1:
            raise ValueError("range_overlap_min must be in (0,1]")
        if not 0 < self.range_boundary_tolerance_fraction < 0.5:
            raise ValueError("range_boundary_tolerance_fraction must be in (0,0.5)")
        if self.pullback_min_atr <= 0 or self.pullback_max_atr <= self.pullback_min_atr:
            raise ValueError("pullback bounds invalid")


PRIMARY_BROOKS_CONFIG = BrooksConfig()


@dataclass(frozen=True)
class BrooksEvent:
    setup: BrooksSetup
    signal_quality: float
    event: EventRecord


def _touch_count_near_extreme(values: np.ndarray, *, upper: bool, tolerance_fraction: float) -> float:
    if len(values) == 0 or np.isnan(values).any():
        return np.nan
    hi = float(np.max(values))
    lo = float(np.min(values))
    span = hi - lo
    if span <= 0:
        return float(len(values))
    tol = tolerance_fraction * span
    if upper:
        return float(np.sum(values >= hi - tol))
    return float(np.sum(values <= lo + tol))


def _validate_input(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"missing OHLCV columns: {sorted(missing)}")
    x = frame.copy()
    x["timestamp"] = pd.to_datetime(x["timestamp"], utc=True, errors="raise")
    x = x.sort_values("timestamp", kind="mergesort").reset_index(drop=True)
    if x["timestamp"].duplicated().any():
        raise ValueError("duplicate timestamps forbidden")
    return x


def add_brooks_atomic_features(
    frame: pd.DataFrame,
    config: BrooksConfig = PRIMARY_BROOKS_CONFIG,
) -> pd.DataFrame:
    """Build causal Brooks-style price-action geometry.

    All rolling references exclude the current bar where they define a prior
    range or state. No centered pivots or future-confirmed zones are used.
    """

    raw = _validate_input(frame)
    x = add_v58_continuous_features(raw)

    o = x["open"].astype(float)
    h = x["high"].astype(float)
    l = x["low"].astype(float)
    c = x["close"].astype(float)
    bar_range = (h - l).replace(0.0, np.nan)
    atr = (x["ATR_percent"].astype(float) * c).replace(0.0, np.nan)

    body = c - o
    abs_body = body.abs()
    x["brooks_body_fraction"] = abs_body / bar_range
    x["brooks_close_location"] = (c - l) / bar_range
    x["brooks_upper_tail_fraction"] = (h - pd.concat([o, c], axis=1).max(axis=1)) / bar_range
    x["brooks_lower_tail_fraction"] = (pd.concat([o, c], axis=1).min(axis=1) - l) / bar_range
    x["brooks_signed_body_fraction"] = np.sign(body) * x["brooks_body_fraction"]

    overlap_penalty = 1.0 - x["bar_overlap"].clip(lower=0.0, upper=1.0)
    x["brooks_bull_signal_quality"] = (
        x["brooks_body_fraction"]
        * x["brooks_close_location"].clip(0.0, 1.0)
        * overlap_penalty
    )
    x["brooks_bear_signal_quality"] = (
        x["brooks_body_fraction"]
        * (1.0 - x["brooks_close_location"].clip(0.0, 1.0))
        * overlap_penalty
    )

    prior_high = h.shift(1).rolling(config.range_window, min_periods=config.range_window).max()
    prior_low = l.shift(1).rolling(config.range_window, min_periods=config.range_window).min()
    prior_width = prior_high - prior_low

    x["brooks_prior_range_high"] = prior_high
    x["brooks_prior_range_low"] = prior_low
    x["brooks_range_width_atr"] = prior_width / atr
    x["brooks_prior_overlap_mean"] = (
        x["bar_overlap"].shift(1).rolling(config.range_window, min_periods=config.range_window).mean()
    )

    shifted_high = h.shift(1)
    shifted_low = l.shift(1)
    x["brooks_upper_boundary_touches"] = shifted_high.rolling(
        config.range_window, min_periods=config.range_window
    ).apply(
        lambda values: _touch_count_near_extreme(
            values,
            upper=True,
            tolerance_fraction=config.range_boundary_tolerance_fraction,
        ),
        raw=True,
    )
    x["brooks_lower_boundary_touches"] = shifted_low.rolling(
        config.range_window, min_periods=config.range_window
    ).apply(
        lambda values: _touch_count_near_extreme(
            values,
            upper=False,
            tolerance_fraction=config.range_boundary_tolerance_fraction,
        ),
        raw=True,
    )

    x["brooks_range_state"] = (
        x["trend_strength"].abs().le(config.range_trend_abs_max_atr)
        & x["brooks_prior_overlap_mean"].ge(config.range_overlap_min)
        & x["brooks_range_width_atr"].le(config.range_width_max_atr)
        & x["brooks_upper_boundary_touches"].ge(config.range_min_boundary_touches)
        & x["brooks_lower_boundary_touches"].ge(config.range_min_boundary_touches)
    )

    ema20 = c.ewm(span=20, adjust=False, min_periods=20).mean()
    x["brooks_ema20_distance_atr"] = (c - ema20) / atr

    prior_trend_up = (
        x["trend_strength"].shift(1).rolling(3, min_periods=1).max()
        >= config.trend_strength_min_atr
    )
    prior_trend_down = (
        x["trend_strength"].shift(1).rolling(3, min_periods=1).min()
        <= -config.trend_strength_min_atr
    )
    x["brooks_prior_trend_up"] = prior_trend_up
    x["brooks_prior_trend_down"] = prior_trend_down

    prior_micro = x["micro_channel_length"].shift(1)
    x["brooks_prior_micro_channel"] = prior_micro

    pullback_long = x["pullback_depth_atr"].between(
        config.pullback_min_atr,
        config.pullback_max_atr,
        inclusive="both",
    )
    pullback_short = x["pullback_depth_atr"].between(
        config.pullback_min_atr,
        config.pullback_max_atr,
        inclusive="both",
    )

    bull_signal = (
        body.gt(0)
        & x["brooks_body_fraction"].ge(config.signal_body_fraction_min)
        & x["brooks_close_location"].ge(config.signal_close_location_long_min)
    )
    bear_signal = (
        body.lt(0)
        & x["brooks_body_fraction"].ge(config.signal_body_fraction_min)
        & x["brooks_close_location"].le(config.signal_close_location_short_max)
    )
    x["brooks_bull_signal_bar"] = bull_signal
    x["brooks_bear_signal_bar"] = bear_signal

    # B1: trend -> pullback -> signal-bar continuation.
    prev_down = c.shift(1) < c.shift(2)
    prev_up = c.shift(1) > c.shift(2)
    b1_long = (
        prior_trend_up
        & (prior_micro.abs() >= config.prior_micro_channel_min)
        & pullback_long
        & prev_down
        & bull_signal
        & c.gt(c.shift(1))
    )
    b1_short = (
        prior_trend_down
        & (prior_micro.abs() >= config.prior_micro_channel_min)
        & pullback_short
        & prev_up
        & bear_signal
        & c.lt(c.shift(1))
    )

    # Breakout references exclude the current bar.
    breakout_up = c > prior_high + config.breakout_strength_min_atr * atr
    breakout_down = c < prior_low - config.breakout_strength_min_atr * atr
    x["brooks_breakout_up"] = breakout_up
    x["brooks_breakout_down"] = breakout_down

    # B2: a completed breakout bar, followed by a directional follow-through bar.
    b2_long = (
        breakout_up.shift(1).fillna(False)
        & body.gt(0)
        & x["brooks_body_fraction"].ge(config.follow_through_body_fraction_min)
        & c.ge(c.shift(1))
        & c.gt(prior_high.shift(1))
    )
    b2_short = (
        breakout_down.shift(1).fillna(False)
        & body.lt(0)
        & x["brooks_body_fraction"].ge(config.follow_through_body_fraction_min)
        & c.le(c.shift(1))
        & c.lt(prior_low.shift(1))
    )

    # B3: breakout failure within a fixed short lifetime and close back inside
    # the exact reference range that was broken.
    b3_long = pd.Series(False, index=x.index)
    b3_short = pd.Series(False, index=x.index)
    failure_magnitude = pd.Series(0.0, index=x.index, dtype=float)
    for lag in range(1, config.failed_breakout_lifetime_bars + 1):
        failed_down = (
            breakout_down.shift(lag).fillna(False)
            & c.gt(prior_low.shift(lag))
            & bull_signal
        )
        failed_up = (
            breakout_up.shift(lag).fillna(False)
            & c.lt(prior_high.shift(lag))
            & bear_signal
        )
        b3_long |= failed_down
        b3_short |= failed_up
        failure_magnitude = failure_magnitude.mask(
            failed_down,
            ((c - prior_low.shift(lag)) / atr).clip(lower=0.0),
        )
        failure_magnitude = failure_magnitude.mask(
            failed_up,
            -((prior_high.shift(lag) - c) / atr).clip(lower=0.0),
        )
    x["brooks_failure_reentry_atr"] = failure_magnitude

    # B4: fade a mechanically defined trading-range boundary.
    near_lower = l <= prior_low + config.range_fade_tolerance_atr * atr
    near_upper = h >= prior_high - config.range_fade_tolerance_atr * atr
    closes_inside_from_low = c > prior_low
    closes_inside_from_high = c < prior_high

    b4_long = x["brooks_range_state"] & near_lower & closes_inside_from_low & bull_signal
    b4_short = x["brooks_range_state"] & near_upper & closes_inside_from_high & bear_signal

    def direction_series(long_mask: pd.Series, short_mask: pd.Series) -> pd.Series:
        out = pd.Series(0, index=x.index, dtype="int8")
        out.loc[long_mask & ~short_mask] = 1
        out.loc[short_mask & ~long_mask] = -1
        return out

    x["BRK_B1_signal"] = direction_series(b1_long, b1_short)
    x["BRK_B2_signal"] = direction_series(b2_long, b2_short)
    x["BRK_B3_signal"] = direction_series(b3_long, b3_short)
    x["BRK_B4_signal"] = direction_series(b4_long, b4_short)
    x["BRK_B5_study_eligible"] = (
        atr.notna()
        & x["brooks_body_fraction"].notna()
        & x["brooks_close_location"].notna()
    )

    x["brooks_signal_quality"] = np.where(
        body >= 0,
        x["brooks_bull_signal_quality"],
        x["brooks_bear_signal_quality"],
    )

    return x.replace([np.inf, -np.inf], np.nan)


def detect_brooks_setups(
    frame: pd.DataFrame,
    config: BrooksConfig = PRIMARY_BROOKS_CONFIG,
) -> pd.DataFrame:
    """Return the full atomic-feature table plus four executable setup signals."""

    return add_brooks_atomic_features(frame, config=config)


def _snapshot_features(row: pd.Series, setup: BrooksSetup, direction: Direction) -> dict[str, float | int | None]:
    names = (
        "ATR_percent",
        "realized_volatility",
        "trend_strength",
        "bar_overlap",
        "breakout_strength",
        "follow_through_strength",
        "failed_breakout_score",
        "signal_bar_quality",
        "micro_channel_length",
        "pullback_depth_atr",
        "trading_range_position",
        "bars_since_breakout",
        "brooks_body_fraction",
        "brooks_close_location",
        "brooks_upper_tail_fraction",
        "brooks_lower_tail_fraction",
        "brooks_bull_signal_quality",
        "brooks_bear_signal_quality",
        "brooks_prior_range_high",
        "brooks_prior_range_low",
        "brooks_range_width_atr",
        "brooks_prior_overlap_mean",
        "brooks_upper_boundary_touches",
        "brooks_lower_boundary_touches",
        "brooks_ema20_distance_atr",
        "brooks_prior_micro_channel",
        "brooks_failure_reentry_atr",
    )
    out: dict[str, float | int | None] = {
        "brooks_setup_code": _SETUP_CODE[setup],
        "brooks_direction_code": 1.0 if direction is Direction.LONG else -1.0,
    }
    for name in names:
        value = row.get(name, np.nan)
        out[name] = None if pd.isna(value) else float(value)
    return out


def _signal_column(setup: BrooksSetup) -> str:
    mapping = {
        BrooksSetup.B1_TREND_PULLBACK: "BRK_B1_signal",
        BrooksSetup.B2_BREAKOUT_FOLLOW_THROUGH: "BRK_B2_signal",
        BrooksSetup.B3_FAILED_BREAKOUT_REVERSAL: "BRK_B3_signal",
        BrooksSetup.B4_TRADING_RANGE_FADE: "BRK_B4_signal",
    }
    if setup not in mapping:
        raise ValueError("B5 is study-only and cannot create an executable event")
    return mapping[setup]


def generate_brooks_events(
    frame: pd.DataFrame,
    *,
    symbol: str,
    venue: str,
    data_version: str,
    code_version: str,
    feature_version: str = "58.0-brooks-phase1",
    config: BrooksConfig = PRIMARY_BROOKS_CONFIG,
    setups: Iterable[BrooksSetup] = (
        BrooksSetup.B1_TREND_PULLBACK,
        BrooksSetup.B2_BREAKOUT_FOLLOW_THROUGH,
        BrooksSetup.B3_FAILED_BREAKOUT_REVERSAL,
        BrooksSetup.B4_TRADING_RANGE_FADE,
    ),
) -> list[BrooksEvent]:
    """Build immutable ARM_D event records with next-bar-open entry semantics."""

    raw = _validate_input(frame)
    x = detect_brooks_setups(raw, config=config)
    regimes = add_causal_regime(raw)[["timestamp", "regime", "regime_confidence"]]
    x = x.merge(regimes, on="timestamp", how="left", validate="one_to_one")

    output: list[BrooksEvent] = []
    setup_list = tuple(setups)
    if BrooksSetup.B5_SIGNAL_BAR_QUALITY_STUDY in setup_list:
        raise ValueError("B5 is a study, not an executable setup")

    for setup in setup_list:
        col = _signal_column(setup)
        for i in np.flatnonzero(x[col].to_numpy(dtype=int) != 0):
            if i + 1 >= len(x):
                continue
            row = x.iloc[i]
            next_row = x.iloc[i + 1]
            side = int(row[col])
            direction = Direction.LONG if side > 0 else Direction.SHORT
            atr_at_signal = float(row["ATR_percent"]) * float(row["close"])
            if not math.isfinite(atr_at_signal) or atr_at_signal <= 0:
                continue

            features = _snapshot_features(row, setup, direction)
            snapshot_id = make_feature_snapshot_id(
                event_timestamp=row["timestamp"].to_pydatetime(),
                feature_version=feature_version,
                features=features,
            )
            strategy_version = f"58.0-brooks-{setup.value.lower()}"
            event_id = make_event_id(
                symbol=symbol,
                venue=venue,
                strategy_arm=StrategyArm.ARM_D,
                direction=direction,
                setup_timestamp=row["timestamp"].to_pydatetime(),
                entry_reference="NEXT_EXECUTABLE_BAR_OPEN",
                feature_snapshot_id=snapshot_id,
                strategy_version=strategy_version,
            )

            entry_price = float(next_row["open"])
            barrier = build_barrier_prices(
                entry_price=entry_price,
                atr_at_signal=atr_at_signal,
                direction=direction,
            )
            source_event_hash = stable_hash(
                {
                    "setup": setup.value,
                    "timestamp": row["timestamp"].isoformat(),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                    "signal": side,
                }
            )
            regime_value = str(row["regime"])
            regime = RegimeLabel(regime_value) if regime_value in RegimeLabel._value2member_map_ else RegimeLabel.UNKNOWN
            event = EventRecord(
                event_id=event_id,
                symbol=symbol,
                venue=venue,
                timestamp=row["timestamp"].to_pydatetime(),
                setup_timestamp=row["timestamp"].to_pydatetime(),
                strategy_arm=StrategyArm.ARM_D,
                direction=direction,
                entry_reference="NEXT_EXECUTABLE_BAR_OPEN",
                entry_time=next_row["timestamp"].to_pydatetime(),
                entry_price=entry_price,
                stop_price=barrier.stop_price,
                target_price=barrier.target_price,
                risk_R=barrier.reward_r,
                holding_horizon=PRIMARY_H4_BARRIER.max_hold_bars_after_entry,
                decision_timeframe=config.timeframe,
                regime=regime,
                regime_confidence=float(row["regime_confidence"]),
                feature_snapshot_id=snapshot_id,
                data_version=data_version,
                code_version=code_version,
                strategy_version=strategy_version,
                available_at=row["timestamp"].to_pydatetime(),
                source_event_hash=source_event_hash,
            )
            quality = (
                float(row["brooks_bull_signal_quality"])
                if direction is Direction.LONG
                else float(row["brooks_bear_signal_quality"])
            )
            output.append(BrooksEvent(setup=setup, signal_quality=quality, event=event))

    output.sort(key=lambda item: (item.event.timestamp, item.setup.value, item.event.direction.value))
    return output
