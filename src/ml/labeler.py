"""Triple barriers at next available open, with pessimistic OHLC ties."""
from __future__ import annotations

import numpy as np
import pandas as pd


def event_outcome(bars: pd.DataFrame, signal: pd.Series | dict) -> dict | None:
    decision = int(signal["bar_index"])
    entry = decision + 1
    horizon = int(signal.get("horizon_m15_bars", 20))
    if entry >= len(bars):
        return None
    if signal.get("native_daily", 0):
        observed_days = pd.DatetimeIndex(bars.timestamp.iloc[entry:].dt.normalize().unique())
        if len(observed_days) < 20 and horizon >= 20 * 96:
            return None
        stop_time = observed_days[min(19, len(observed_days) - 1)] + pd.Timedelta(days=1)
        native_end = int(bars.timestamp.searchsorted(stop_time))
        if stop_time > bars.available_at.iloc[-1] and horizon >= 20 * 96:
            return None
        horizon = min(horizon, native_end - entry)
    # Never execute before the signal's source candle became observable.
    if "available_at" in signal and pd.Timestamp(signal["available_at"]) > bars.timestamp.iloc[entry]:
        raise ValueError("Next-open execution precedes signal availability")
    if entry + horizon > len(bars):
        return None  # censored at dataset boundary, not called a loss/timeout
    direction, width = int(signal.get("direction", 1)), float(signal["atr"])
    if direction not in (-1, 1) or not np.isfinite(width) or width <= 0:
        raise ValueError("Invalid signal direction or ATR")
    price = float(bars.open.iloc[entry])
    profit, stop = price + direction * 2 * width, price - direction * width
    for j in range(entry, entry + horizon):
        row = bars.iloc[j]
        # Gaps through a stop incur the worse open; favorable target gaps are
        # conservatively filled at the target. A same-bar tie is a loss.
        loss_hit = row.low <= stop if direction == 1 else row.high >= stop
        profit_hit = row.high >= profit if direction == 1 else row.low <= profit
        if loss_hit:
            fill = min(stop, row.open) if direction == 1 else max(stop, row.open)
            return {"entry_index": entry, "exit_index": j, "entry_price": price, "exit_price": float(fill), "label": 0, "reason": "loss", "ambiguous": bool(profit_hit)}
        if profit_hit:
            return {"entry_index": entry, "exit_index": j, "entry_price": price, "exit_price": profit, "label": 1, "reason": "profit", "ambiguous": False}
    j = entry + horizon - 1
    return {"entry_index": entry, "exit_index": j, "entry_price": price,
            "exit_price": float(bars.close.iloc[j]), "label": None, "reason": "timeout", "ambiguous": False}


def triple_barrier_labels(signals: pd.DataFrame, bars: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    outcomes = []
    counts = {"profit": 0, "loss": 0, "timeout": 0, "censored": 0, "ambiguous": 0}
    for signal in signals.to_dict("records"):
        outcome = event_outcome(bars, signal)
        if outcome is None:
            counts["censored"] += 1
            continue
        counts[outcome["reason"]] += 1
        counts["ambiguous"] += int(outcome["ambiguous"])
        if outcome["label"] is not None:
            outcomes.append({"event_id": signal["event_id"], **outcome,
                             "label_end": bars.available_at.iloc[outcome["exit_index"]]})
    columns = ["event_id", "entry_index", "exit_index", "entry_price", "exit_price", "label", "reason", "ambiguous", "label_end"]
    return pd.DataFrame(outcomes, columns=columns), counts
