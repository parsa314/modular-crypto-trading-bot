"""Point-in-time Ichimoku event candidate; NOT a proven alpha strategy.

Senkou A=(Tenkan9+Kijun26)/2, Senkou B=(HH52+LL52)/2.
The cloud *visible now* is computed using `.shift(26)`, never future
unshifted projected cloud. A cross, cloud break or Kijun reclaim is required.
"""
from __future__ import annotations

from datetime import datetime
import pandas as pd

from .common import StrategySignal, closed_bars, result


def evaluate(frame: pd.DataFrame, *, as_of: datetime) -> StrategySignal:
    x = closed_bars(frame, as_of=as_of, min_rows=90)
    midpoint = lambda n: (x.high.rolling(n).max() + x.low.rolling(n).min()) / 2.0
    tenkan = midpoint(9)
    kijun = midpoint(26)
    visible_a = ((tenkan + kijun) / 2.0).shift(26)
    visible_b = midpoint(52).shift(26)
    upper = pd.concat([visible_a, visible_b], axis=1).max(axis=1)
    lower = pd.concat([visible_a, visible_b], axis=1).min(axis=1)
    close = x.close
    up = bool(close.iloc[-1] > upper.iloc[-1])
    down = bool(close.iloc[-1] < lower.iloc[-1])
    tk_up = bool(tenkan.iloc[-1] > kijun.iloc[-1])
    tk_down = bool(tenkan.iloc[-1] < kijun.iloc[-1])
    cloud_break_up = up and bool(close.iloc[-2] <= upper.iloc[-2])
    cloud_break_down = down and bool(close.iloc[-2] >= lower.iloc[-2])
    tk_cross_up = tk_up and bool(tenkan.iloc[-2] <= kijun.iloc[-2])
    tk_cross_down = tk_down and bool(tenkan.iloc[-2] >= kijun.iloc[-2])
    reclaim_up = bool(close.iloc[-2] <= kijun.iloc[-2] and close.iloc[-1] > kijun.iloc[-1])
    reclaim_down = bool(close.iloc[-2] >= kijun.iloc[-2] and close.iloc[-1] < kijun.iloc[-1])
    # Chikou confirmation is current close versus an earlier, already observed
    # candle, not the future unknown price at which the plot may be displayed.
    lag_up = bool(close.iloc[-1] > x.high.iloc[-27])
    lag_down = bool(close.iloc[-1] < x.low.iloc[-27])
    if up and tk_up and (cloud_break_up or tk_cross_up or reclaim_up):
        score = 2 + int(lag_up)
        return result("ICHIMOKU", x, 1, score, "CLOUD_UP", "TK_UP", "CAUSAL_ENTRY_EVENT",
                      evidence={"visible_cloud_top": float(upper.iloc[-1]),
                                "kijun": float(kijun.iloc[-1]), "chikou_confirm": float(lag_up)})
    if down and tk_down and (cloud_break_down or tk_cross_down or reclaim_down):
        score = 2 + int(lag_down)
        return result("ICHIMOKU", x, -1, score, "CLOUD_DOWN", "TK_DOWN", "CAUSAL_ENTRY_EVENT",
                      evidence={"visible_cloud_bottom": float(lower.iloc[-1]),
                                "kijun": float(kijun.iloc[-1]), "chikou_confirm": float(lag_down)})
    return result("ICHIMOKU", x, 0, 0)
