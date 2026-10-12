from __future__ import annotations

from dataclasses import dataclass
import re

import pandas as pd


_TIMEFRAME = re.compile(r"^(\d+)(m|h|d)$", re.IGNORECASE)


def timeframe_delta(timeframe: str) -> pd.Timedelta:
    match = _TIMEFRAME.fullmatch(str(timeframe).strip())
    if not match:
        raise ValueError(f"unsupported timeframe: {timeframe!r}")
    value = int(match.group(1))
    if value <= 0:
        raise ValueError("timeframe value must be positive")
    unit = match.group(2).lower()
    return pd.Timedelta(minutes=value) if unit == "m" else pd.Timedelta(hours=value) if unit == "h" else pd.Timedelta(days=value)


@dataclass(frozen=True)
class CandleTimeContractV53:
    """Point-in-time contract for closed-bar features.

    Raw OHLCV ``timestamp`` is defined as BAR OPEN time. A bar becomes usable only
    at ``available_at = bar_close_at + publication_lag``. This prevents a higher
    timeframe bar from becoming visible during the interval in which it is still
    forming.
    """

    timeframe: str
    publication_lag: pd.Timedelta = pd.Timedelta(0)
    max_future_clock_skew: pd.Timedelta = pd.Timedelta(seconds=5)

    def __post_init__(self) -> None:
        timeframe_delta(self.timeframe)
        if self.publication_lag < pd.Timedelta(0):
            raise ValueError("publication_lag cannot be negative")
        if self.max_future_clock_skew < pd.Timedelta(0):
            raise ValueError("max_future_clock_skew cannot be negative")


def annotate_candle_times_v53(
    frame: pd.DataFrame,
    *,
    contract: CandleTimeContractV53,
    timestamp_col: str = "timestamp",
) -> pd.DataFrame:
    if timestamp_col not in frame.columns:
        raise ValueError(f"missing {timestamp_col}")
    out = frame.copy()
    opened = pd.to_datetime(out[timestamp_col], utc=True, errors="coerce")
    if opened.isna().any():
        raise ValueError("invalid candle timestamp")
    if opened.duplicated().any() or not opened.is_monotonic_increasing:
        raise ValueError("candle timestamps must be unique and strictly increasing")
    delta = timeframe_delta(contract.timeframe)
    out["bar_open_at"] = opened
    out["bar_close_at"] = opened + delta
    out["available_at"] = out["bar_close_at"] + contract.publication_lag
    return out


def closed_bar_snapshot_v53(
    frame: pd.DataFrame,
    *,
    decision_time: pd.Timestamp | str,
    contract: CandleTimeContractV53,
    timestamp_col: str = "timestamp",
) -> pd.DataFrame:
    out = annotate_candle_times_v53(frame, contract=contract, timestamp_col=timestamp_col)
    decision = pd.Timestamp(decision_time)
    decision = decision.tz_localize("UTC") if decision.tzinfo is None else decision.tz_convert("UTC")
    newest_allowed = decision + contract.max_future_clock_skew
    if (out["bar_open_at"] > newest_allowed).any():
        raise ValueError("future clock skew exceeds allowed bound")
    return out.loc[out["available_at"] <= decision].reset_index(drop=True)


def resample_closed_ohlcv_v53(
    frame: pd.DataFrame,
    *,
    source_timeframe: str,
    target_timeframe: str,
    publication_lag: pd.Timedelta = pd.Timedelta(0),
) -> pd.DataFrame:
    """Aggregate closed lower-timeframe candles into causal higher-timeframe bars."""

    source_delta = timeframe_delta(source_timeframe)
    target_delta = timeframe_delta(target_timeframe)
    if target_delta <= source_delta or target_delta % source_delta != pd.Timedelta(0):
        raise ValueError("target timeframe must be an integer multiple of source timeframe")
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing OHLCV columns: {sorted(missing)}")

    low = annotate_candle_times_v53(
        frame,
        contract=CandleTimeContractV53(source_timeframe, publication_lag=publication_lag),
    )
    low = low.set_index("bar_open_at", drop=False)
    rule = target_delta
    grouped = low.resample(rule, label="left", closed="left", origin="epoch")
    high = grouped.agg(
        timestamp=("bar_open_at", "first"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        component_available_at=("available_at", "max"),
        component_count=("close", "count"),
    ).dropna(subset=["open", "high", "low", "close"])
    expected = int(target_delta / source_delta)
    high = high.loc[high["component_count"] == expected].reset_index(drop=True)
    high["bar_open_at"] = pd.to_datetime(high["timestamp"], utc=True)
    high["bar_close_at"] = high["bar_open_at"] + target_delta
    # Both the theoretical close and all component bars must be available.
    high["available_at"] = high[["bar_close_at", "component_available_at"]].max(axis=1)
    return high.drop(columns=["component_available_at"])
