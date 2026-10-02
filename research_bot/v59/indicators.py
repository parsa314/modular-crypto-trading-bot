from __future__ import annotations

import numpy as np
import pandas as pd


def true_range(frame: pd.DataFrame) -> pd.Series:
    previous = frame["close"].shift(1)
    return pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous).abs(),
            (frame["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    if period <= 0:
        raise ValueError("ATR period must be positive")
    return true_range(frame).ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def tsi(
    close: pd.Series,
    long_period: int = 25,
    short_period: int = 13,
    signal_period: int = 13,
) -> pd.DataFrame:
    if min(long_period, short_period, signal_period) <= 0:
        raise ValueError("TSI periods must be positive")
    momentum = close.diff()
    smooth = (
        momentum.ewm(span=long_period, adjust=False).mean()
        .ewm(span=short_period, adjust=False).mean()
    )
    abs_smooth = (
        momentum.abs().ewm(span=long_period, adjust=False).mean()
        .ewm(span=short_period, adjust=False).mean()
    )
    value = 100.0 * smooth / abs_smooth.replace(0.0, np.nan)
    signal = value.ewm(span=signal_period, adjust=False).mean()
    return pd.DataFrame({"tsi": value, "tsi_signal": signal}, index=close.index)


def ichimoku_causal(
    frame: pd.DataFrame,
    *,
    tenkan_period: int = 9,
    kijun_period: int = 26,
    span_b_period: int = 52,
    displacement: int = 26,
) -> pd.DataFrame:
    """Return only Ichimoku values observable at the current decision bar."""
    if min(tenkan_period, kijun_period, span_b_period, displacement) <= 0:
        raise ValueError("Ichimoku periods/displacement must be positive")
    high = frame["high"]
    low = frame["low"]
    tenkan = (
        high.rolling(tenkan_period, min_periods=tenkan_period).max()
        + low.rolling(tenkan_period, min_periods=tenkan_period).min()
    ) / 2.0
    kijun = (
        high.rolling(kijun_period, min_periods=kijun_period).max()
        + low.rolling(kijun_period, min_periods=kijun_period).min()
    ) / 2.0
    span_a_raw = (tenkan + kijun) / 2.0
    span_b_raw = (
        high.rolling(span_b_period, min_periods=span_b_period).max()
        + low.rolling(span_b_period, min_periods=span_b_period).min()
    ) / 2.0
    span_a = span_a_raw.shift(displacement)
    span_b = span_b_raw.shift(displacement)
    cloud_top = pd.concat([span_a, span_b], axis=1).max(axis=1)
    cloud_bottom = pd.concat([span_a, span_b], axis=1).min(axis=1)
    return pd.DataFrame(
        {
            "tenkan": tenkan,
            "kijun": kijun,
            "senkou_a_visible": span_a,
            "senkou_b_visible": span_b,
            "cloud_top": cloud_top,
            "cloud_bottom": cloud_bottom,
        },
        index=frame.index,
    )


def ema_slope(close: pd.Series, span: int = 50, lookback: int = 5) -> pd.Series:
    if span <= 0 or lookback <= 0:
        raise ValueError("EMA span/lookback must be positive")
    ema = close.ewm(span=span, adjust=False).mean()
    return (ema - ema.shift(lookback)) / ema.shift(lookback).replace(0.0, np.nan)
