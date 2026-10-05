"""Causal, cash-account spot evaluation for the frozen profit research protocol.

Bars are consecutive UTC hours and their timestamps identify the *open*. A
prediction on bar t can first trade at open(t+1). Stops use information known
at that open; intra-bar highs never retroactively raise a protective stop.
Daily loss is measured against the preceding day's final equity, including
overnight gaps. A drawdown halt lasts through the entire supplied OOS history,
including month/fold boundaries. Final-close liquidation is an explicit common
evaluation convention, not a signal available to a running strategy.

OHLCV cannot establish actual queue fills or realized slippage. The fixed adverse
slippage and fee settings below are scenario assumptions, not venue measurements.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BacktestConfig:
    initial_capital: float = 10_000.0
    risk_per_trade: float = 0.01
    max_daily_loss: float = 0.02
    max_drawdown_halt: float = 0.10
    max_exposure: float = 0.80
    fee_bps: float = 10.0
    slippage_bps: float = 5.0
    entry_threshold: float = 0.60
    exit_threshold: float = 0.50
    atr_stop_multiple: float = 2.0
    max_holding_bars: int = 24

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
        if self.initial_capital <= 0 or self.atr_stop_multiple <= 0:
            raise ValueError("Capital and ATR multiple must be positive")
        for name in ("risk_per_trade", "max_daily_loss", "max_drawdown_halt", "max_exposure"):
            if not 0 < getattr(self, name) < 1:
                raise ValueError(f"{name} must be strictly between zero and one")
        for name in ("fee_bps", "slippage_bps"):
            if not 0 <= getattr(self, name) < 1_000:
                raise ValueError(f"{name} must be between zero and 1,000 bps")
        if not 0 <= self.exit_threshold < self.entry_threshold <= 1:
            raise ValueError("Require 0 <= exit_threshold < entry_threshold <= 1")
        if type(self.max_holding_bars) is not int or self.max_holding_bars < 1:
            raise ValueError("max_holding_bars must be a positive integer")


@dataclass(frozen=True)
class BacktestResult:
    equity: pd.DataFrame
    trades: pd.DataFrame
    events: pd.DataFrame
    metrics: dict


TRADE_COLUMNS = [
    "entry_timestamp", "exit_timestamp", "exit_bar_timestamp", "exit_timing",
    "entry_price", "exit_price", "entry_reference_price", "exit_reference_price",
    "quantity", "entry_fee", "exit_fee", "fees_paid", "slippage_paid", "pnl_net",
    "return_on_deployed_capital", "stop_price", "holding_bars", "exit_reason",
]
EVENT_COLUMNS = ["timestamp", "event", "reason", "price"]


def _validated_frame(frame: pd.DataFrame, *, signals: bool) -> pd.DataFrame:
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    if signals:
        required.add("probability")
    missing = required.difference(frame.columns)
    if missing or frame.empty:
        raise ValueError(f"Nonempty frame required; missing columns: {sorted(missing)}")
    result = frame.copy().reset_index(drop=True)
    timestamps = result["timestamp"]
    numeric = pd.api.types.is_numeric_dtype(timestamps.dtype)
    result["timestamp"] = pd.to_datetime(timestamps, utc=True, errors="raise", unit="ms" if numeric else None)
    times = result["timestamp"]
    if times.isna().any() or times.duplicated().any() or not times.is_monotonic_increasing:
        raise ValueError("Bar timestamps must be valid, unique and increasing")
    if (times != times.dt.floor("h")).any() or not times.diff().dropna().eq(pd.Timedelta(hours=1)).all():
        raise ValueError("Bars must be aligned consecutive UTC hours; gaps are rejected")
    for name in ("open", "high", "low", "close", "volume"):
        if pd.api.types.is_bool_dtype(result[name].dtype):
            raise ValueError(f"Invalid numeric bar field: {name}")
        result[name] = pd.to_numeric(result[name], errors="raise").astype(float)
        if not np.isfinite(result[name]).all():
            raise ValueError(f"Nonfinite bar field: {name}")
    if (result[["open", "high", "low", "close"]] <= 0).any().any() or (result.volume < 0).any():
        raise ValueError("Prices must be positive and volume nonnegative")
    if (result.low > result[["open", "close"]].min(axis=1)).any() or (result.high < result[["open", "close"]].max(axis=1)).any():
        raise ValueError("OHLC price bounds are inconsistent")
    if signals:
        result["probability"] = pd.to_numeric(result.probability, errors="raise").astype(float)
        probability = result.probability.dropna()
        if not np.isfinite(probability).all() or not probability.between(0, 1).all():
            raise ValueError("Probability must be missing or finite in [0, 1]")
        if "atr" not in result:
            if "atr_14_pct" not in result:
                raise ValueError("Signal data requires atr or atr_14_pct")
            result["atr"] = pd.to_numeric(result.atr_14_pct, errors="raise") * result.close
        result["atr"] = pd.to_numeric(result.atr, errors="raise").astype(float)
        atr = result.atr.dropna()
        if not np.isfinite(atr).all() or (atr < 0).any():
            raise ValueError("ATR must be missing or finite and nonnegative")
    return result


def _growth(rate: float, periods: float) -> float | None:
    exponent = math.log(rate) * periods
    if exponent > math.log(np.finfo(float).max):
        return None
    return float(math.expm1(exponent))


def calculate_metrics(equity: pd.DataFrame, trades: pd.DataFrame, events: pd.DataFrame) -> dict:
    """Daily UTC Sharpe and trade-level profitability; undefined ratios are null.

The initial equity row is mandatory, preventing loss of the first day's return
or first loss in maximum drawdown. Bar closes at midnight belong to the UTC day
which has just ended. Partial first/last days are included and labelled by the
reported number of daily returns. Risk-free rate is fixed at zero.
"""
    if len(equity) < 2:
        raise ValueError("Metrics require initial equity and at least one bar close")
    values = equity["equity"].astype(float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("Equity must be finite and positive")
    times = pd.to_datetime(equity.timestamp, utc=True)
    if not times.is_monotonic_increasing or times.duplicated().any():
        raise ValueError("Equity timestamps must be increasing and unique")
    elapsed_days = (times.iloc[-1] - times.iloc[0]).total_seconds() / 86_400
    if elapsed_days <= 0:
        raise ValueError("Evaluation duration must be positive")
    initial, final = float(values.iloc[0]), float(values.iloc[-1])
    daily = pd.Series(values.iloc[1:].to_numpy(), index=pd.DatetimeIndex(times.iloc[1:]) - pd.Timedelta(1, "ns")).resample("D").last()
    if daily.isna().any():
        raise ValueError("Equity history contains a missing UTC day")
    daily_returns = daily.pct_change()
    daily_returns.iloc[0] = daily.iloc[0] / initial - 1
    std = float(daily_returns.std(ddof=1)) if len(daily_returns) > 1 else 0.0
    sharpe = float(daily_returns.mean() / std * math.sqrt(365)) if std > 0 and math.isfinite(std) else None
    max_drawdown = float((1 - values / values.cummax()).max())
    cagr = _growth(final / initial, 365 / elapsed_days)
    monthly = _growth(final / initial, 365 / 12 / elapsed_days)
    pnl = trades["pnl_net"].astype(float) if not trades.empty else pd.Series(dtype=float)
    profit = float(pnl[pnl > 0].sum())
    loss = float(-pnl[pnl < 0].sum())
    event_counts = events["event"].value_counts() if not events.empty else pd.Series(dtype=int)
    return {
        "initial_capital": initial, "final_equity": final,
        "total_return": final / initial - 1, "net_return": final / initial - 1,
        "elapsed_days": elapsed_days, "cagr": cagr, "annualized_return": cagr,
        "monthly_geometric_return": monthly, "sharpe": sharpe,
        "sharpe_frequency": "UTC daily", "sharpe_risk_free_rate": 0.0,
        "daily_return_count": int(len(daily_returns)), "max_drawdown": max_drawdown,
        "calmar": cagr / max_drawdown if cagr is not None and max_drawdown > 0 else None,
        "profit_factor": profit / loss if loss > 0 else None,
        "win_rate": float((pnl > 0).mean()) if len(pnl) else None,
        "trade_count": int(len(pnl)), "gross_trade_profit": profit, "gross_trade_loss": loss,
        "fees_paid": float(trades.fees_paid.sum()) if len(pnl) else 0.0,
        "slippage_paid": float(trades.slippage_paid.sum()) if len(pnl) else 0.0,
        "daily_halts": int(event_counts.get("DAILY_HALT", 0)),
        "drawdown_halts": int(event_counts.get("DRAWDOWN_HALT", 0)),
        "final_liquidation_convention": "last supplied bar close with adverse slippage and fee",
    }


def _run(frame: pd.DataFrame, config: BacktestConfig, *, strategy: str) -> BacktestResult:
    data = _validated_frame(frame, signals=strategy == "model")
    if strategy not in {"model", "cash", "buy_hold_80pct"}:
        raise ValueError("Unknown evaluation strategy")
    fee, slip = config.fee_bps / 10_000, config.slippage_bps / 10_000
    sell_factor = (1 - fee) * (1 - slip)
    cash, quantity = config.initial_capital, 0.0
    previous_equity = peak = day_anchor = config.initial_capital
    day = None
    daily_halted = drawdown_halted = False
    entry: dict | None = None
    equity_rows = [{"timestamp": data.timestamp.iloc[0], "equity": cash, "cash": cash, "quantity": 0.0}]
    trades: list[dict] = []
    events: list[dict] = []

    def event(timestamp, kind, reason, price=None):
        events.append({"timestamp": timestamp, "event": kind, "reason": reason, "price": price})

    def halts(value, timestamp):
        nonlocal daily_halted, drawdown_halted
        if strategy != "model":
            return
        # Small tolerance avoids floating-point equality missing an exact risk stop.
        if not daily_halted and value <= day_anchor * (1 - config.max_daily_loss) + 1e-9:
            daily_halted = True
            event(timestamp, "DAILY_HALT", "DAILY_LOSS")
        if not drawdown_halted and value <= peak * (1 - config.max_drawdown_halt) + 1e-9:
            drawdown_halted = True
            event(timestamp, "DRAWDOWN_HALT", "MAX_DRAWDOWN")

    def buy(i, reference, amount, stop):
        nonlocal cash, quantity, entry
        fill = reference * (1 + slip)
        charge = amount * fill * fee
        cash -= amount * fill + charge
        if cash < -1e-8:
            raise ArithmeticError("Backtest attempted to borrow cash")
        cash = max(0.0, cash)
        quantity = amount
        entry = {"index": i, "timestamp": data.timestamp.iloc[i], "reference": reference,
                 "fill": fill, "fee": charge, "slippage": amount * (fill - reference), "stop": stop}
        event(data.timestamp.iloc[i], "ENTRY", strategy, fill)

    def sell(i, reference, reason, timing):
        nonlocal cash, quantity, entry
        assert entry is not None and quantity > 0
        fill = reference * (1 - slip)
        charge = quantity * fill * fee
        proceeds = quantity * fill - charge
        deployed = quantity * entry["fill"] + entry["fee"]
        bar_time = data.timestamp.iloc[i]
        timestamp = bar_time if timing == "open" else bar_time + pd.Timedelta(hours=1)
        trades.append({
            "entry_timestamp": entry["timestamp"], "exit_timestamp": timestamp,
            "exit_bar_timestamp": bar_time, "exit_timing": timing,
            "entry_price": entry["fill"], "exit_price": fill,
            "entry_reference_price": entry["reference"], "exit_reference_price": reference,
            "quantity": quantity, "entry_fee": entry["fee"], "exit_fee": charge,
            "fees_paid": entry["fee"] + charge,
            "slippage_paid": entry["slippage"] + quantity * (reference - fill),
            "pnl_net": proceeds - deployed, "return_on_deployed_capital": proceeds / deployed - 1,
            "stop_price": entry["stop"], "holding_bars": i - entry["index"], "exit_reason": reason,
        })
        cash += proceeds
        quantity = 0.0
        entry = None
        event(timestamp, "EXIT", reason, fill)
        halts(cash, timestamp)

    def protective_stop():
        assert entry is not None and quantity > 0
        levels = [
            (entry["stop"], "ATR_STOP"),
            ((day_anchor * (1 - config.max_daily_loss) - cash) / (quantity * sell_factor), "DAILY_LOSS"),
            ((peak * (1 - config.max_drawdown_halt) - cash) / (quantity * sell_factor), "MAX_DRAWDOWN"),
        ]
        return max(levels, key=lambda value: value[0])

    # One ledger for the entire supplied history; no fold/day balance resets.
    for i, bar in enumerate(data.itertuples(index=False)):
        timestamp = bar.timestamp
        if timestamp.date() != day:
            if daily_halted:
                event(timestamp, "DAILY_RESUME", "NEW_UTC_DAY")
            day, day_anchor, daily_halted = timestamp.date(), previous_equity, False
        peak = max(peak, cash + quantity * bar.open)
        exited = False
        if strategy == "model" and quantity:
            stop, reason = protective_stop()
            if bar.open <= stop:
                sell(i, bar.open, reason, "open")
                exited = True
            elif i - entry["index"] >= config.max_holding_bars:
                sell(i, bar.open, "TIME_STOP", "open")
                exited = True
            elif i and np.isfinite(data.probability.iloc[i - 1]) and data.probability.iloc[i - 1] <= config.exit_threshold:
                sell(i, bar.open, "SIGNAL_EXIT", "open")
                exited = True
        if strategy == "model" and not quantity and not exited and not daily_halted and not drawdown_halted and i:
            previous = data.iloc[i - 1]
            if np.isfinite(previous.probability) and previous.probability >= config.entry_threshold and np.isfinite(previous.atr) and previous.atr > 0:
                stop = previous.close - config.atr_stop_multiple * previous.atr
                if stop > 0 and bar.open > stop:
                    fill = bar.open * (1 + slip)
                    unit_loss = fill * (1 + fee) - stop * sell_factor
                    amount = min(cash * config.risk_per_trade / unit_loss,
                                 cash * config.max_exposure / (fill * (1 + fee)))
                    buy(i, bar.open, amount, stop)
                else:
                    event(timestamp, "ENTRY_REJECTED", "GAP_BELOW_STOP_OR_INVALID_STOP", bar.open)
        elif strategy == "buy_hold_80pct" and i == 0:
            amount = cash * 0.8 / (bar.open * (1 + slip) * (1 + fee))
            buy(i, bar.open, amount, None)
        if strategy == "model" and quantity:
            stop, reason = protective_stop()
            if bar.low <= stop:
                # Entry costs can put a new protective level above this bar's
                # open; that case exits at the open instead of a favorable fill.
                sell(i, min(bar.open, stop), reason, "intrabar")
        if i == len(data) - 1 and quantity:
            sell(i, bar.close, "END_OF_SAMPLE", "close")
        value = cash + quantity * bar.close
        peak = max(peak, value)
        halts(value, timestamp + pd.Timedelta(hours=1))
        equity_rows.append({"timestamp": timestamp + pd.Timedelta(hours=1), "equity": value,
                            "cash": cash, "quantity": quantity})
        previous_equity = value

    equity = pd.DataFrame(equity_rows)
    trade_frame = pd.DataFrame(trades, columns=TRADE_COLUMNS)
    event_frame = pd.DataFrame(events, columns=EVENT_COLUMNS)
    return BacktestResult(equity, trade_frame, event_frame, calculate_metrics(equity, trade_frame, event_frame))


def run_backtest(frame: pd.DataFrame, config: BacktestConfig | None = None) -> BacktestResult:
    """Evaluate one continuous OOS account; do not call separately per fold."""
    return _run(frame, config or BacktestConfig(), strategy="model")


def run_baselines(frame: pd.DataFrame, config: BacktestConfig | None = None) -> dict[str, BacktestResult]:
    """Cash and static 80% buy-and-hold on identical dates with identical costs.

Buy-and-hold makes one purchase at the first supplied open, never rebalances,
and liquidates at the shared final close. It is a reference investment, so model
signal exits, daily loss stops and drawdown halts do not modify that benchmark.
"""
    config = config or BacktestConfig()
    return {name: _run(frame, config, strategy=name) for name in ("cash", "buy_hold_80pct")}
