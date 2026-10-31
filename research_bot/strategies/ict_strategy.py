"""Causal ICT-derived sweep -> MSS -> displacement candidate.

This is a mechanical proxy for discretionary concepts, not a statement that
institutional stops were actually observed. Liquidity proxy: swing extreme
from the *previous* 20 completed bars. No future pivots may be consulted.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
from .common import StrategySignal, atr, closed_bars, result


def _session_ok(t: pd.Timestamp) -> bool:
    # Local market hours are converted with IANA zones to respect DST.
    dt = t.to_pydatetime()
    london = dt.astimezone(ZoneInfo("Europe/London"))
    new_york = dt.astimezone(ZoneInfo("America/New_York"))
    return (8 <= london.hour < 11) or (8 <= new_york.hour < 11)


def evaluate(frame: pd.DataFrame, *, as_of: datetime) -> StrategySignal:
    x = closed_bars(frame, as_of=as_of, min_rows=90)
    n = len(x)
    candidates: list[tuple[int, int]] = []
    for j in range(n - 9, n - 1):
        lows = x.low.iloc[j - 20:j]
        highs = x.high.iloc[j - 20:j]
        # Sweep below liquidity followed by a close back inside.
        if x.low.iloc[j] < lows.min() and x.close.iloc[j] > lows.min():
            candidates.append((j, 1))
        if x.high.iloc[j] > highs.max() and x.close.iloc[j] < highs.max():
            candidates.append((j, -1))
    if not candidates:
        return result("ICT", x, 0, 0)
    sweep_index, side = max(candidates, key=lambda z: z[0])
    between = x.iloc[sweep_index + 1:-1]
    if between.empty:
        return result("ICT", x, 0, 0)
    # Most recent confirmation must close through the intervening structure.
    structure = float(between.high.max() if side == 1 else between.low.min())
    mss = bool(x.close.iloc[-1] > structure if side == 1 else x.close.iloc[-1] < structure)
    if not mss:
        return result("ICT", x, 0, 0)
    true_range = float(x.high.iloc[-1] - x.low.iloc[-1])
    body_fraction = abs(float(x.close.iloc[-1] - x.open.iloc[-1])) / max(true_range, 1e-12)
    volatility = float(atr(x).iloc[-1])
    displacement = bool(
        body_fraction >= 0.55
        and true_range >= 0.85 * volatility
        and (x.close.iloc[-1] > x.open.iloc[-1] if side == 1 else x.close.iloc[-1] < x.open.iloc[-1])
    )
    # 2: past sweep + current confirmed MSS; +1: displacement in a liquid session.
    score = 2 + int(displacement and _session_ok(x.available_at.iloc[-1]))
    return result("ICT", x, side, score, "PREVIOUS_LIQUIDITY_SWEEP", "MSS_CONFIRMED",
                  evidence={"sweep_bars_ago": float(n - 1 - sweep_index),
                            "body_fraction": float(body_fraction),
                            "session_liquid": float(_session_ok(x.available_at.iloc[-1]))})
