"""Mechanical Al Brooks-inspired signal-bar + trend and follow-through events.

Price-action concepts are operational hypotheses: direction of EMA20, a
large-body signal bar near its extreme, and either break above/below the prior
five bars or two-bar follow-through. Do not label them proven profitable.
"""
from __future__ import annotations
from datetime import datetime
import pandas as pd

from .common import StrategySignal, closed_bars, result


def evaluate(frame: pd.DataFrame, *, as_of: datetime) -> StrategySignal:
    x = closed_bars(frame, as_of=as_of, min_rows=90)
    ema = x.close.ewm(span=20, adjust=False).mean()
    last = x.iloc[-1]
    prev = x.iloc[-2]
    candle_range = float(last.high - last.low)
    if candle_range <= 0.0:
        return result("AL_BROOKS", x, 0, 0)
    body_ratio = abs(float(last.close - last.open)) / candle_range
    clv = float(last.close - last.low) / candle_range
    trend_up = bool(last.close > ema.iloc[-1] and ema.iloc[-1] > ema.iloc[-5])
    trend_down = bool(last.close < ema.iloc[-1] and ema.iloc[-1] < ema.iloc[-5])
    high_quality_up = bool(last.close > last.open and body_ratio >= 0.58 and clv >= 0.78)
    high_quality_down = bool(last.close < last.open and body_ratio >= 0.58 and clv <= 0.22)
    breakout_up = bool(last.close > x.high.iloc[-6:-1].max())
    breakout_down = bool(last.close < x.low.iloc[-6:-1].min())
    follow_up = bool(prev.close > prev.open and last.close > prev.close)
    follow_down = bool(prev.close < prev.open and last.close < prev.close)
    if trend_up and high_quality_up and (breakout_up or follow_up):
        return result("AL_BROOKS", x, 1, 2 + int(breakout_up and follow_up),
                      "TREND_CONTEXT", "SIGNAL_BAR_QUALITY",
                      evidence={"body_ratio": body_ratio, "close_location": clv,
                                "breakout": float(breakout_up)})
    if trend_down and high_quality_down and (breakout_down or follow_down):
        return result("AL_BROOKS", x, -1, 2 + int(breakout_down and follow_down),
                      "TREND_CONTEXT", "SIGNAL_BAR_QUALITY",
                      evidence={"body_ratio": body_ratio, "close_location": clv,
                                "breakout": float(breakout_down)})
    return result("AL_BROOKS", x, 0, 0)
