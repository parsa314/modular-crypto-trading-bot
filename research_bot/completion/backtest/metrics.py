from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Sequence
import numpy as np

from ..models import ZERO, ONE, signed_decimal, seconds


def average_uniqueness(intervals: Sequence[tuple[datetime, datetime]]) -> Decimal | None:
    """Time-weighted 1/concurrency on actual simulated bet intervals."""
    if not intervals:
        return None
    if any(end <= start for start, end in intervals):
        raise ValueError("positive bet intervals required")
    cuts = sorted({t for interval in intervals for t in interval})
    integrals = [ZERO] * len(intervals)
    for left, right in zip(cuts, cuts[1:]):
        active = [i for i, (start, end) in enumerate(intervals) if start <= left and end >= right]
        if active:
            duration = seconds(right - left)
            for i in active:
                integrals[i] += duration / len(active)
    return sum((v / seconds(end - start) for v, (start, end) in zip(integrals, intervals)), ZERO) / len(intervals)


def performance_metrics(equity: Sequence[Decimal], closed_pnls: Sequence[Decimal],
                        intervals: Sequence[tuple[datetime, datetime]], periods_per_year: int) -> dict:
    if type(periods_per_year) is not int or periods_per_year < 1:
        raise ValueError("explicit positive annual sampling count required")
    values = [signed_decimal(v) for v in equity]
    if not values or any(v <= ZERO for v in values):
        raise ValueError("positive finite equity curve required")
    returns = [b / a - ONE for a, b in zip(values, values[1:])]
    x = np.asarray([float(r) for r in returns], dtype=float)
    sharpe = float(x.mean() / x.std(ddof=1) * np.sqrt(periods_per_year)) if len(x) > 1 and x.std(ddof=1) > 0 else None
    peak, drawdown = values[0], ZERO
    for value in values:
        peak = max(peak, value)
        drawdown = max(drawdown, ONE - value / peak)
    losses = sorted((-r for r in returns), reverse=True)
    tail_size = max(1, (len(losses) + 19) // 20)
    cvar = sum(losses[:tail_size], ZERO) / tail_size if losses else None
    pnl = [signed_decimal(v) for v in closed_pnls]
    wins, losses_pnl = sum((max(ZERO, v) for v in pnl), ZERO), sum((max(ZERO, -v) for v in pnl), ZERO)
    return {"net_total_return": str(values[-1] / values[0] - ONE), "sharpe": sharpe,
        "max_drawdown": str(drawdown), "cvar_95": str(cvar) if cvar is not None else None,
        "win_rate": str(Decimal(sum(v > ZERO for v in pnl)) / len(pnl)) if pnl else None,
        "profit_factor": str(wins / losses_pnl) if losses_pnl > ZERO else None,
        "closed_inventory_reductions": len(pnl), "average_uniqueness": str(average_uniqueness(intervals)) if intervals else None,
        "dsr": None, "pbo": None, "statistical_status": "REQUIRES_COMPLETE_TRIAL_MATRIX_AND_PROTOCOL",
        "periods_per_year": periods_per_year, "net_returns": [str(r) for r in returns]}
