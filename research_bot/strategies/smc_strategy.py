"""Causal SMC-inspired BOS with mechanical order-block/FVG confirmation.

An OB is proxied by the latest opposing candle before a break. It is NOT
verified institutional order flow. FVG requires a strictly three-bar gap:
bullish low[t] > high[t-2], bearish high[t] < low[t-2].
"""
from __future__ import annotations
from datetime import datetime
import pandas as pd

from .common import StrategySignal, atr, closed_bars, result


def evaluate(frame: pd.DataFrame, *, as_of: datetime) -> StrategySignal:
    x = closed_bars(frame, as_of=as_of, min_rows=90)
    hi = float(x.high.iloc[-13:-1].max())
    lo = float(x.low.iloc[-13:-1].min())
    last = x.iloc[-1]
    bullish_break = bool(last.close > hi and last.close > last.open)
    bearish_break = bool(last.close < lo and last.close < last.open)
    if not (bullish_break or bearish_break):
        return result("SMC", x, 0, 0)
    side = 1 if bullish_break else -1
    prior = x.iloc[-11:-1]
    opposing = prior.loc[
        prior.close < prior.open if side == 1 else prior.close > prior.open
    ]
    if opposing.empty:
        return result("SMC", x, 0, 0)
    ob = opposing.iloc[-1]
    # The OB reference is a completed *prior* candle; current close must be on
    # the correct side of that zone. This is not yet an OB-retest execution rule.
    zone_confirm = bool(last.close > ob.high if side == 1 else last.close < ob.low)
    if not zone_confirm:
        return result("SMC", x, 0, 0)
    fvg = bool(last.low > x.high.iloc[-3] if side == 1 else last.high < x.low.iloc[-3])
    a = float(atr(x).iloc[-1])
    displacement = bool((last.high - last.low) >= a and
                        abs(last.close - last.open) >= 0.55 * (last.high - last.low))
    score = 2 + int(fvg and displacement)
    return result("SMC", x, side, score, "CLOSED_BAR_BOS", "OPPOSING_BAR_OB_PROXY",
                  evidence={"ob_high": float(ob.high), "ob_low": float(ob.low),
                            "fvg": float(fvg), "displacement": float(displacement)})
