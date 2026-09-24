"""Spot-only Ichimoku research runner with next-open historical fills."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math

import pandas as pd

from .coinex_public import PERIOD_MS
from .forward_paper_v14 import frozen_shadow_signal


@dataclass(frozen=True)
class SpotBacktestConfig:
    initial_cash: float = 10_000.0
    allocation_fraction: float = 0.20
    fee_bps: float = 10.0
    slippage_bps: float = 2.0
    minimum_notional: float = 10.0
    entry_rule_score: float = 0.80

    def __post_init__(self) -> None:
        values = (self.initial_cash, self.allocation_fraction, self.fee_bps,
                  self.slippage_bps, self.minimum_notional, self.entry_rule_score)
        if not all(math.isfinite(x) for x in values):
            raise ValueError("configuration requires finite values")
        if (self.initial_cash <= 0 or not 0 < self.allocation_fraction <= 1
                or self.fee_bps < 0 or self.slippage_bps < 0
                or self.minimum_notional <= 0 or not 0 <= self.entry_rule_score <= 1):
            raise ValueError("invalid spot backtest configuration")


def backtest_spot_ichimoku(
    bars: pd.DataFrame, *, period: str = "4hour",
    config: SpotBacktestConfig | None = None,
    observed_at: datetime | None = None,
) -> dict:
    """Signal at closed bar i; fill at next bar's open; never borrow or short.

    The final position is marked to the last observed close and is *not*
    liquidated at an unknown future price. Fees and slippage apply to fills.
    """
    cfg = config or SpotBacktestConfig()
    if period not in PERIOD_MS:
        raise ValueError("unsupported period")
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    if required - set(bars):
        raise ValueError(f"missing columns: {sorted(required - set(bars))}")
    if len(bars) < 122:
        raise ValueError("at least 122 bars required")
    x = bars.copy().reset_index(drop=True)
    x["timestamp"] = pd.to_datetime(x["timestamp"], utc=True, errors="raise")
    if not x["timestamp"].is_monotonic_increasing or x["timestamp"].duplicated().any():
        raise ValueError("bars must have strictly increasing timestamps")
    expected = pd.Timedelta(milliseconds=PERIOD_MS[period])
    if not (x["timestamp"].diff().dropna() == expected).all():
        raise ValueError("missing bars: next-open semantics require contiguous candles")
    for col in ("open", "high", "low", "close", "volume"):
        x[col] = pd.to_numeric(x[col], errors="raise")
    if not (x[["open", "high", "low", "close", "volume"]].apply(lambda s: s.map(math.isfinite))).all().all():
        raise ValueError("non-finite market data")
    if (x[["open", "high", "low", "close"]] <= 0).any().any() or (x["volume"] < 0).any():
        raise ValueError("invalid price or volume")
    if ((x["high"] < x[["open", "close"]].max(axis=1)) |
            (x["low"] > x[["open", "close"]].min(axis=1))).any():
        raise ValueError("inconsistent OHLC prices")
    cutoff = pd.Timestamp(observed_at or datetime.now(timezone.utc))
    if cutoff.tzinfo is None:
        raise ValueError("observed_at must be timezone aware")
    x = x[x["timestamp"] + expected <= cutoff].reset_index(drop=True)
    if len(x) < 122:
        raise ValueError("insufficient fully closed bars")

    cash, quantity = cfg.initial_cash, 0.0
    trades: list[dict] = []
    equity = [cash]
    for i in range(119, len(x) - 1):
        signal = frozen_shadow_signal(x.iloc[:i + 1], quantity > 0, cfg.entry_rule_score)
        next_open = float(x.at[i + 1, "open"])
        filled_at = x.at[i + 1, "timestamp"].isoformat()
        if signal.action == "BUY_CANDIDATE" and quantity == 0:
            budget = cash * cfg.allocation_fraction
            price = next_open * (1 + cfg.slippage_bps / 10_000)
            bought = budget / (price * (1 + cfg.fee_bps / 10_000))
            if budget >= cfg.minimum_notional and bought > 0:
                cash -= bought * price * (1 + cfg.fee_bps / 10_000)
                quantity = bought
                trades.append({"side": "BUY", "signal_closed_at": (x.at[i, "timestamp"] + expected).isoformat(),
                               "filled_at": filled_at, "fill_price": price, "quantity": bought})
        elif signal.action == "EXIT" and quantity > 0:
            price = next_open * (1 - cfg.slippage_bps / 10_000)
            cash += quantity * price * (1 - cfg.fee_bps / 10_000)
            trades.append({"side": "SELL", "signal_closed_at": (x.at[i, "timestamp"] + expected).isoformat(),
                           "filled_at": filled_at, "fill_price": price, "quantity": quantity})
            quantity = 0.0
        assert cash >= -1e-8 and quantity >= 0
        equity.append(cash + quantity * float(x.at[i + 1, "close"]))
    peak = pd.Series(equity).cummax()
    drawdown = pd.Series(equity) / peak - 1
    return {
        "mode": "historical_spot_research", "strategy": "ICHIMOKU_SHADOW_V14",
        "config": asdict(cfg), "bars": len(x), "trades": trades,
        "initial_cash": cfg.initial_cash, "final_cash": cash,
        "final_base_quantity": quantity,
        "final_equity_marked_at_close": equity[-1],
        "total_return": equity[-1] / cfg.initial_cash - 1,
        "max_drawdown": float(drawdown.min()),
        "open_position_unrealized": quantity > 0,
        "assumptions": "next-open hypothetical fills; fixed fees and slippage; no liquidity or partial-fill model",
    }
