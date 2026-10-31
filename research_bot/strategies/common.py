"""Causal closed-bar contract shared by the four exploratory signal arms.

Only point-in-time observations already available at `as_of` may influence a
decision. Unit-test fixtures are synthetic; market-performance claims require
independent real-data backtests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import math
from typing import Literal, Mapping

import numpy as np
import pandas as pd


Action = Literal["BUY", "SELL", "HOLD"]


@dataclass(frozen=True)
class StrategySignal:
    style: str
    action: Action
    score: int
    decision_at: datetime
    reasons: tuple[str, ...] = ()
    evidence: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.action not in {"BUY", "SELL", "HOLD"}:
            raise ValueError("Invalid action")
        if isinstance(self.score, bool) or not isinstance(self.score, int) or not 0 <= self.score <= 3:
            raise ValueError("Score must be integer in [0,3]")
        if self.decision_at.tzinfo is None or self.decision_at.utcoffset() is None:
            raise ValueError("decision_at must be timezone aware")
        if self.action == "HOLD" and self.score != 0:
            raise ValueError("HOLD score must be zero")
        if self.action != "HOLD" and self.score == 0:
            raise ValueError("Trading candidate must have a positive score")
        if not all(math.isfinite(float(v)) for v in self.evidence.values()):
            raise ValueError("Nonfinite evidence")


def closed_bars(frame: pd.DataFrame, *, as_of: datetime, min_rows: int = 90) -> pd.DataFrame:
    """Validate OHLCV, strictly increasing open times, and point-in-time availability.

    `timestamp` is candle open time and `available_at` is the time the
    *completed* candle was observable (normally close plus dissemination lag).
    Rows with availability after as_of are excluded, never backfilled.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of needs timezone")
    required = ("timestamp", "available_at", "open", "high", "low", "close", "volume")
    if any(c not in frame.columns for c in required):
        raise ValueError("MISSING_OHLCV_OR_AVAILABILITY")
    if frame.empty:
        raise ValueError("EMPTY_OHLCV")
    data = frame.loc[:, required].copy()
    for col in ("timestamp", "available_at"):
        raw = data[col]
        if raw.isna().any():
            raise ValueError("MISSING_EVENT_TIME")
        data[col] = pd.to_datetime(raw, utc=True, errors="raise")
    if data["timestamp"].isna().any() or data["available_at"].isna().any():
        raise ValueError("INVALID_EVENT_TIME")
    if not data["timestamp"].is_monotonic_increasing or data["timestamp"].duplicated().any():
        raise ValueError("NON_MONOTONIC_OR_DUPLICATE_CANDLES")
    if (data["timestamp"] > data["available_at"]).any():
        raise ValueError("AVAILABLE_BEFORE_CANDLE_OPEN")
    for col in ("open", "high", "low", "close", "volume"):
        data[col] = pd.to_numeric(data[col], errors="raise")
    numbers = data[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float)
    if not np.isfinite(numbers).all():
        raise ValueError("NONFINITE_MARKET_DATA")
    if (data[["open", "high", "low", "close"]] <= 0).any().any() or (data["volume"] < 0).any():
        raise ValueError("INVALID_PRICE_OR_VOLUME")
    if (data["low"] > data[["open", "close"]].min(axis=1)).any() or (
        data["high"] < data[["open", "close"]].max(axis=1)
    ).any() or (data["low"] > data["high"]).any():
        raise ValueError("INCONSISTENT_OHLC")
    data = data.loc[data["available_at"] <= pd.Timestamp(as_of)].copy()
    if len(data) < min_rows:
        raise ValueError("INSUFFICIENT_CAUSAL_BARS")
    return data.reset_index(drop=True)


def atr(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    """Causal Wilder-smoothed true range, not using future data."""
    prev = bars["close"].shift(1)
    tr = pd.concat(
        [(bars.high - bars.low), (bars.high - prev).abs(), (bars.low - prev).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def result(style: str, bars: pd.DataFrame, direction: int, score: int,
           *reasons: str, evidence: Mapping[str, float] | None = None) -> StrategySignal:
    if direction not in (-1, 0, 1):
        raise ValueError("Invalid direction")
    action: Action = "BUY" if direction > 0 else "SELL" if direction < 0 else "HOLD"
    if not direction:
        score, reasons, evidence = 0, (), {}
    return StrategySignal(
        style=style,
        action=action,
        score=int(score),
        decision_at=bars["available_at"].iloc[-1].to_pydatetime(),
        reasons=tuple(reasons),
        evidence=dict(evidence or {}),
    )
