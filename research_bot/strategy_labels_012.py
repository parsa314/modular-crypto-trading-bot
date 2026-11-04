"""Causal research labels, not a fill simulator or economic backtest."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import pandas as pd

from .strategy_research_012 import _aware, _DURATIONS, _UTC_ALIGNMENT


@dataclass(frozen=True)
class BarrierLabel:
    outcome: str
    label_available_at: str | None
    observed_bars: int
    reason: str


def _number(value) -> Decimal:
    try:
        result = Decimal(str(value))
    except (ValueError, InvalidOperation) as exc:
        raise ValueError("INVALID_BARRIER_PRICE") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError("INVALID_BARRIER_PRICE")
    return result


def triple_barrier_label(frame: pd.DataFrame, *, decision_at: datetime, entry_at: datetime,
                         entry_price: Decimal, take_profit: Decimal, stop_loss: Decimal,
                         timeframe: str, horizon_bars: int, direction: str,
                         as_of: datetime) -> BarrierLabel:
    """Label completed bars only; ambiguous intrabar ordering stays ambiguous.

    Caller supplies already-fixed barriers, entry and volatility-based parameters.
    Entry and barrier values are assumptions, not verified exchange executions.
    A label is never available before the closing bar itself becomes available.
    """
    if timeframe not in _DURATIONS or direction not in {"LONG", "SHORT"}:
        raise ValueError("INVALID_LABEL_SPEC")
    if isinstance(horizon_bars, bool) or not isinstance(horizon_bars, int) or horizon_bars <= 0:
        raise ValueError("INVALID_HORIZON")
    decision, entry, cutoff = _aware(decision_at), _aware(entry_at), _aware(as_of)
    if entry <= decision:
        raise ValueError("ENTRY_MUST_FOLLOW_DECISION")
    ep, tp, sl = map(_number,(entry_price,take_profit,stop_loss))
    if not ((sl < ep < tp) if direction == "LONG" else (tp < ep < sl)):
        raise ValueError("INVALID_BARRIER_ORDER")
    if not {"timestamp","available_at","high","low"}.issubset(frame.columns):
        raise ValueError("MISSING_LABEL_DATA")
    data = frame[["timestamp","available_at","high","low"]].copy()
    data["timestamp"] = data["timestamp"].map(_aware)
    data["available_at"] = data["available_at"].map(_aware)
    data = data.loc[(data.timestamp >= entry) & (data.available_at <= cutoff)].copy()
    if data.empty:
        return BarrierLabel("PENDING",None,0,"NO_COMPLETED_ENTRY_BAR")
    data=data.iloc[:horizon_bars]
    # Path-dependent labels consume every bar up through the event bar.
    # The label cannot be available before the slowest consumed observation.
    latest_consumed_availability = None
    for index, row in enumerate(data.itertuples(index=False),start=1):
        expected = entry + (index-1)*_DURATIONS[timeframe]
        if row.timestamp != expected or row.timestamp.floor(_UTC_ALIGNMENT[timeframe]) != row.timestamp:
            raise ValueError("MISSING_DUPLICATE_OR_MISALIGNED_LABEL_BARS")
        if row.available_at < row.timestamp + _DURATIONS[timeframe]:
            raise ValueError("LABEL_BAR_NOT_CLOSED")
        latest_consumed_availability = (
            row.available_at if latest_consumed_availability is None
            else max(latest_consumed_availability, row.available_at)
        )
        high,low = _number(row.high),_number(row.low)
        if low > high:
            raise ValueError("INVALID_BAR_RANGE")
        tp_hit = high >= tp if direction == "LONG" else low <= tp
        sl_hit = low <= sl if direction == "LONG" else high >= sl
        if tp_hit and sl_hit:
            return BarrierLabel("AMBIGUOUS",latest_consumed_availability.isoformat(),index,"BOTH_BARRIERS_SAME_BAR")
        if tp_hit or sl_hit:
            return BarrierLabel("TP" if tp_hit else "SL",latest_consumed_availability.isoformat(),index,"BARRIER_OBSERVED")
    if len(data) == horizon_bars:
        return BarrierLabel("TIMEOUT",latest_consumed_availability.isoformat(),len(data),"HORIZON_COMPLETE")
    return BarrierLabel("PENDING",None,len(data),"HORIZON_NOT_YET_AVAILABLE")
