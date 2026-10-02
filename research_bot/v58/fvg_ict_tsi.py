"""Causal multi-timeframe FVG/ICT/TSI signal family adapted from the supplied strategy.

Timestamps are interpreted as bar OPEN times. A signal is formed only after the
LTF signal bar closes and its research entry reference is the immediate next
bar open. Signal generation never sizes capital or creates an order; financial
authority remains downstream.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Any
import argparse
import math

import numpy as np
import pandas as pd

from research_bot.risk import historical_cvar
from .events import stable_hash


Direction = Literal["long", "short"]
ConfirmMode = Literal["tsi", "structure", "either", "both"]
StopMode = Literal["midpoint", "zone_edge", "swing"]


@dataclass(frozen=True)
class FVGICTTSIConfig:
    htf: str = "1h"
    ltf: str = "5min"
    atr_period: int = 14
    displacement_body_atr: float = 0.80
    min_fvg_atr: float = 0.10
    max_fvg_age_htf_bars: int = 80
    tsi_long: int = 25
    tsi_short: int = 13
    tsi_signal: int = 13
    confirmation: ConfirmMode = "either"
    structure_lookback: int = 8
    require_midpoint_rejection: bool = True
    stop_mode: StopMode = "zone_edge"
    stop_buffer_atr: float = 0.10
    swing_lookback: int = 8
    reward_risk: float = 2.0
    fee_bps_per_side: float = 5.0
    slippage_bps_per_side: float = 2.0
    initial_equity: float = 10_000.0
    risk_per_trade: float = 0.0025
    max_asset_weight: float = 0.35
    max_gross_exposure: float = 0.70
    drawdown_kill: float = 0.05
    max_cvar95: float = 0.035
    stop_first: bool = True
    one_signal_per_bar: bool = True

    def __post_init__(self) -> None:
        _rule_delta(self.htf)
        _rule_delta(self.ltf)
        if _rule_delta(self.htf) <= _rule_delta(self.ltf):
            raise ValueError("htf must be strictly higher than ltf")
        if self.confirmation not in {"tsi", "structure", "either", "both"}:
            raise ValueError("invalid confirmation mode")
        if self.stop_mode not in {"midpoint", "zone_edge", "swing"}:
            raise ValueError("invalid stop mode")
        positive = (
            "atr_period", "displacement_body_atr", "min_fvg_atr", "max_fvg_age_htf_bars",
            "tsi_long", "tsi_short", "tsi_signal", "structure_lookback", "stop_buffer_atr",
            "swing_lookback", "reward_risk", "initial_equity", "risk_per_trade",
            "max_asset_weight", "max_gross_exposure", "drawdown_kill", "max_cvar95",
        )
        for name in positive:
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(float(value)) or float(value) <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in ("fee_bps_per_side", "slippage_bps_per_side"):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(float(value)) or float(value) < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if self.risk_per_trade > 0.0025:
            raise ValueError("risk_per_trade cannot relax V58 0.25% ceiling")
        if self.max_asset_weight > 0.35 or self.max_gross_exposure > 0.70:
            raise ValueError("portfolio exposure cannot relax V58 ceilings")
        if self.drawdown_kill > 0.05 or self.max_cvar95 > 0.035:
            raise ValueError("drawdown/CVaR cannot relax V58 ceilings")
        if not self.stop_first:
            raise ValueError("V58 primary ambiguity rule is STOP_FIRST")


@dataclass(frozen=True)
class FVG:
    id: int
    direction: Direction
    formed_at: pd.Timestamp
    lower: float
    upper: float
    midpoint: float
    atr: float
    htf_index: int
    displacement_body_atr: float


@dataclass(frozen=True)
class FVGSignal:
    signal_id: str
    strategy_id: str
    symbol: str
    venue: str
    direction: Direction
    htf: str
    ltf: str
    fvg_id: int
    fvg_formed_at: str
    signal_time: str
    entry_time: str
    entry_reference_price: float
    fvg_lower: float
    fvg_upper: float
    fvg_midpoint: float
    stop_price: float
    target_price: float
    reward_risk: float
    confirmation_mode: str
    tsi_cross: bool
    structure_break: bool
    midpoint_rejection: bool
    fvg_age_htf_bars: int
    displacement_body_atr: float
    stop_mode: str
    fee_bps_per_side: float
    slippage_bps_per_side: float
    risk_per_trade: float
    financial_gate_required: bool
    portfolio_execution_eligible: bool
    execution_note: str


@dataclass
class Trade:
    id: int
    signal_id: str
    fvg_id: int
    direction: Direction
    signal_time: pd.Timestamp
    entry_time: pd.Timestamp
    entry_reference_price: float
    entry_price: float
    stop_price: float
    target_price: float
    size: float
    notional: float
    risk_cash_budget: float
    stop_loss_budget_used: float
    exit_time: pd.Timestamp | None = None
    exit_reference_price: float | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    gross_pnl: float = 0.0
    fee_costs: float = 0.0
    slippage_costs: float = 0.0
    total_costs: float = 0.0
    net_pnl: float = 0.0
    r_multiple: float = 0.0


def _rule_delta(rule: str) -> pd.Timedelta:
    value = str(rule).strip().lower()
    aliases = {"1m": "1min", "5m": "5min", "15m": "15min", "60m": "1h", "240m": "4h"}
    value = aliases.get(value, value)
    try:
        delta = pd.Timedelta(value)
    except ValueError as exc:
        raise ValueError(f"unsupported timeframe: {rule}") from exc
    if delta <= pd.Timedelta(0):
        raise ValueError("timeframe must be positive")
    return delta


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame) or df.empty:
        raise ValueError("OHLCV input must be a nonempty dataframe")
    x = df.copy()
    rename: dict[str, str] = {}
    for column in x.columns:
        key = str(column).strip().lower()
        if key in {"date", "datetime", "time"}:
            rename[column] = "timestamp"
        elif key in {"timestamp", "open", "high", "low", "close", "volume"}:
            rename[column] = key
    x = x.rename(columns=rename)
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(x.columns)
    if missing:
        raise ValueError(f"missing OHLCV columns: {sorted(missing)}")
    x = x.loc[:, ["timestamp", "open", "high", "low", "close", "volume"]].copy()
    x["timestamp"] = pd.to_datetime(x["timestamp"], utc=True, errors="raise")
    for column in ("open", "high", "low", "close", "volume"):
        x[column] = pd.to_numeric(x[column], errors="raise")
    if x["timestamp"].duplicated().any():
        raise ValueError("duplicate timestamps are forbidden")
    x = x.sort_values("timestamp").reset_index(drop=True)
    values = x[["open", "high", "low", "close", "volume"]].to_numpy(float)
    if not np.isfinite(values).all():
        raise ValueError("OHLCV must be finite")
    if (x["volume"] < 0).any() or (x[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("prices must be positive and volume nonnegative")
    if (x["high"] < x[["open", "close", "low"]].max(axis=1)).any():
        raise ValueError("invalid candle high")
    if (x["low"] > x[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("invalid candle low")
    return x


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample bar-open timestamps while preserving bar-open indexing."""
    x = normalize_ohlcv(df).set_index("timestamp")
    out = (
        x.resample(rule, label="left", closed="left", origin="epoch")
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .dropna()
    )
    out.index.name = "timestamp"
    return out


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    previous_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def tsi(close: pd.Series, long_n: int = 25, short_n: int = 13, signal_n: int = 13) -> pd.DataFrame:
    momentum = close.diff()
    smoothed = momentum.ewm(span=long_n, adjust=False).mean().ewm(span=short_n, adjust=False).mean()
    absolute = momentum.abs().ewm(span=long_n, adjust=False).mean().ewm(span=short_n, adjust=False).mean()
    value = 100.0 * smoothed / absolute.replace(0, np.nan)
    signal = value.ewm(span=signal_n, adjust=False).mean()
    return pd.DataFrame({"tsi": value, "tsi_signal": signal}, index=close.index)


def detect_fvgs(htf: pd.DataFrame, cfg: FVGICTTSIConfig) -> list[FVG]:
    x = htf.copy()
    x["atr"] = atr(x, cfg.atr_period)
    x["body"] = (x["close"] - x["open"]).abs()
    duration = _rule_delta(cfg.htf)
    out: list[FVG] = []
    next_id = 0
    for i in range(2, len(x)):
        c1, c2, c3 = x.iloc[i - 2], x.iloc[i - 1], x.iloc[i]
        a = float(c2["atr"])
        if not math.isfinite(a) or a <= 0:
            continue
        displacement_ratio = float(c2["body"]) / a
        if displacement_ratio < cfg.displacement_body_atr:
            continue
        formed_at = pd.Timestamp(x.index[i]) + duration
        if float(c3["low"]) > float(c1["high"]):
            lower, upper = float(c1["high"]), float(c3["low"])
            if upper - lower >= cfg.min_fvg_atr * a:
                out.append(FVG(next_id, "long", formed_at, lower, upper, (lower + upper) / 2.0,
                               a, i, displacement_ratio))
                next_id += 1
        if float(c3["high"]) < float(c1["low"]):
            lower, upper = float(c3["high"]), float(c1["low"])
            if upper - lower >= cfg.min_fvg_atr * a:
                out.append(FVG(next_id, "short", formed_at, lower, upper, (lower + upper) / 2.0,
                               a, i, displacement_ratio))
                next_id += 1
    return out


def tsi_cross(df: pd.DataFrame, i: int, direction: Direction) -> bool:
    if i < 1:
        return False
    p0, s0 = df["tsi"].iloc[i - 1], df["tsi_signal"].iloc[i - 1]
    p1, s1 = df["tsi"].iloc[i], df["tsi_signal"].iloc[i]
    if any(pd.isna(value) for value in (p0, s0, p1, s1)):
        return False
    if direction == "long":
        return bool(p0 <= s0 and p1 > s1)
    return bool(p0 >= s0 and p1 < s1)


def structure_break(df: pd.DataFrame, i: int, direction: Direction, lookback: int) -> bool:
    if i < lookback:
        return False
    if direction == "long":
        return float(df["close"].iloc[i]) > float(df["high"].iloc[i - lookback:i].max())
    return float(df["close"].iloc[i]) < float(df["low"].iloc[i - lookback:i].min())


def overlaps(row: pd.Series, fvg: FVG) -> bool:
    return float(row["low"]) <= fvg.upper and float(row["high"]) >= fvg.lower


def midpoint_rejection(row: pd.Series, fvg: FVG) -> bool:
    if fvg.direction == "long":
        return float(row["low"]) <= fvg.midpoint and float(row["close"]) > fvg.midpoint
    return float(row["high"]) >= fvg.midpoint and float(row["close"]) < fvg.midpoint


def _confirmation(tsi_hit: bool, structure_hit: bool, mode: ConfirmMode) -> bool:
    if mode == "tsi":
        return tsi_hit
    if mode == "structure":
        return structure_hit
    if mode == "both":
        return tsi_hit and structure_hit
    return tsi_hit or structure_hit


def stop_price(df: pd.DataFrame, i: int, fvg: FVG, cfg: FVGICTTSIConfig) -> float:
    current_atr = float(df["atr"].iloc[i])
    if not math.isfinite(current_atr) or current_atr <= 0:
        return math.nan
    buffer_ = cfg.stop_buffer_atr * current_atr
    if cfg.stop_mode == "midpoint":
        return fvg.midpoint - buffer_ if fvg.direction == "long" else fvg.midpoint + buffer_
    if cfg.stop_mode == "zone_edge":
        return fvg.lower - buffer_ if fvg.direction == "long" else fvg.upper + buffer_
    window = df.iloc[max(0, i - cfg.swing_lookback + 1): i + 1]
    return (
        float(window["low"].min()) - buffer_
        if fvg.direction == "long"
        else float(window["high"].max()) + buffer_
    )


def target_price(entry: float, stop: float, direction: Direction, rr: float) -> float:
    risk = abs(float(entry) - float(stop))
    return float(entry + rr * risk if direction == "long" else entry - rr * risk)


def slip(price: float, direction: Direction, bps: float, *, entry: bool) -> float:
    fraction = float(bps) / 10_000.0
    if entry:
        return float(price * (1 + fraction) if direction == "long" else price * (1 - fraction))
    return float(price * (1 - fraction) if direction == "long" else price * (1 + fraction))


def fvg_age_htf_bars(fvg: FVG, decision_time: pd.Timestamp, cfg: FVGICTTSIConfig) -> int:
    delta = pd.Timestamp(decision_time) - pd.Timestamp(fvg.formed_at)
    if delta < pd.Timedelta(0):
        return -1
    return int(delta // _rule_delta(cfg.htf))


def scan_signals(
    raw: pd.DataFrame,
    cfg: FVGICTTSIConfig | None = None,
    *,
    symbol: str = "UNKNOWN",
    venue: str = "research_csv",
) -> dict[str, Any]:
    """Detect causal FVG/ICT/TSI signals; this function never sizes capital."""
    config = FVGICTTSIConfig() if cfg is None else cfg
    if not isinstance(config, FVGICTTSIConfig):
        raise ValueError("cfg must be FVGICTTSIConfig")
    config.__post_init__()
    if not isinstance(symbol, str) or not symbol.strip() or not isinstance(venue, str) or not venue.strip():
        raise ValueError("symbol and venue must be nonempty strings")
    base = normalize_ohlcv(raw)
    ltf = resample_ohlcv(base, config.ltf)
    htf = resample_ohlcv(base, config.htf)
    ltf["atr"] = atr(ltf, config.atr_period)
    ltf = ltf.join(tsi(ltf["close"], config.tsi_long, config.tsi_short, config.tsi_signal))
    fvgs = detect_fvgs(htf, config)
    used: set[int] = set()
    signals: list[FVGSignal] = []
    ltf_duration = _rule_delta(config.ltf)
    warmup = max(config.atr_period * 3, config.tsi_long + config.tsi_short + config.tsi_signal)

    for i in range(warmup, len(ltf) - 1):
        bar_open_time = pd.Timestamp(ltf.index[i])
        decision_time = bar_open_time + ltf_duration
        next_open_time = pd.Timestamp(ltf.index[i + 1])
        if next_open_time != decision_time:
            continue
        row = ltf.iloc[i]
        candidates = []
        for fvg in fvgs:
            age = fvg_age_htf_bars(fvg, decision_time, config)
            if fvg.id in used or fvg.formed_at > decision_time or age < 0 or age > config.max_fvg_age_htf_bars:
                continue
            candidates.append((fvg, age))
        candidates.sort(key=lambda item: (item[0].formed_at, item[0].id), reverse=True)

        for fvg, age in candidates:
            if not overlaps(row, fvg):
                continue
            midpoint_hit = midpoint_rejection(row, fvg)
            if config.require_midpoint_rejection and not midpoint_hit:
                continue
            tsi_hit = tsi_cross(ltf, i, fvg.direction)
            structure_hit = structure_break(ltf, i, fvg.direction, config.structure_lookback)
            if not _confirmation(tsi_hit, structure_hit, config.confirmation):
                continue
            reference_entry = float(ltf["open"].iloc[i + 1])
            stop = stop_price(ltf, i, fvg, config)
            if not math.isfinite(stop):
                continue
            if (fvg.direction == "long" and stop >= reference_entry) or (
                fvg.direction == "short" and stop <= reference_entry
            ):
                continue
            target = target_price(reference_entry, stop, fvg.direction, config.reward_risk)
            if not math.isfinite(target) or target <= 0:
                continue
            signal_id = stable_hash(
                {
                    "family": "FVG_ICT_TSI_MTF",
                    "symbol": symbol,
                    "venue": venue,
                    "direction": fvg.direction,
                    "htf": config.htf,
                    "ltf": config.ltf,
                    "fvg_id": fvg.id,
                    "fvg_formed_at": fvg.formed_at.isoformat(),
                    "signal_time": decision_time.isoformat(),
                    "confirmation": config.confirmation,
                    "stop_mode": config.stop_mode,
                }
            )
            long_eligible = fvg.direction == "long"
            signals.append(
                FVGSignal(
                    signal_id=signal_id,
                    strategy_id="FVG_ICT_TSI_MTF",
                    symbol=symbol,
                    venue=venue,
                    direction=fvg.direction,
                    htf=config.htf,
                    ltf=config.ltf,
                    fvg_id=fvg.id,
                    fvg_formed_at=fvg.formed_at.isoformat(),
                    signal_time=decision_time.isoformat(),
                    entry_time=decision_time.isoformat(),
                    entry_reference_price=reference_entry,
                    fvg_lower=fvg.lower,
                    fvg_upper=fvg.upper,
                    fvg_midpoint=fvg.midpoint,
                    stop_price=float(stop),
                    target_price=float(target),
                    reward_risk=config.reward_risk,
                    confirmation_mode=config.confirmation,
                    tsi_cross=tsi_hit,
                    structure_break=structure_hit,
                    midpoint_rejection=midpoint_hit,
                    fvg_age_htf_bars=age,
                    displacement_body_atr=fvg.displacement_body_atr,
                    stop_mode=config.stop_mode,
                    fee_bps_per_side=config.fee_bps_per_side,
                    slippage_bps_per_side=config.slippage_bps_per_side,
                    risk_per_trade=config.risk_per_trade,
                    financial_gate_required=True,
                    portfolio_execution_eligible=long_eligible,
                    execution_note=(
                        "ELIGIBLE_FOR_V58_SPOT_FINANCIAL_GATE"
                        if long_eligible
                        else "SIGNAL_ONLY_BEARISH_SHORT_NOT_AUTHORIZED_BY_CURRENT_V58_SPOT_PORTFOLIO"
                    ),
                )
            )
            used.add(fvg.id)
            if config.one_signal_per_bar:
                break

    return {
        "config": asdict(config),
        "signals": pd.DataFrame([asdict(signal) for signal in signals]),
        "fvgs": pd.DataFrame([asdict(fvg) for fvg in fvgs]),
        "ltf": ltf,
        "htf": htf,
    }


def _signal_index(ltf: pd.DataFrame, signal: pd.Series) -> int:
    stamp = pd.Timestamp(signal["entry_time"])
    location = ltf.index.get_indexer([stamp])
    if len(location) != 1 or int(location[0]) < 0:
        raise ValueError("signal entry time is not an exact LTF bar open")
    return int(location[0])


def backtest(
    raw: pd.DataFrame,
    cfg: FVGICTTSIConfig | None = None,
    *,
    symbol: str = "UNKNOWN",
    venue: str = "research_csv",
) -> dict[str, Any]:
    """Conservative research backtest with financial ceilings and explicit costs."""
    config = FVGICTTSIConfig() if cfg is None else cfg
    scan = scan_signals(raw, config, symbol=symbol, venue=venue)
    ltf = scan["ltf"]
    signals = scan["signals"]
    fee_fraction = config.fee_bps_per_side / 10_000.0
    equity = float(config.initial_equity)
    peak_equity = equity
    completed_returns: list[float] = []
    occupied_until = -1
    trades: list[Trade] = []
    equity_rows: list[dict[str, Any]] = [
        {"timestamp": ltf.index[0], "equity": equity, "drawdown": 0.0}
    ] if len(ltf) else []

    for trade_id, (_, signal) in enumerate(signals.sort_values(["entry_time", "signal_id"]).iterrows()):
        entry_i = _signal_index(ltf, signal)
        if entry_i <= occupied_until or entry_i >= len(ltf):
            continue
        if equity <= peak_equity * (1.0 - config.drawdown_kill):
            continue
        cvar = historical_cvar(completed_returns)
        if cvar is not None and cvar > config.max_cvar95:
            continue

        direction: Direction = str(signal["direction"])  # type: ignore[assignment]
        entry_reference = float(ltf["open"].iloc[entry_i])
        entry_fill = slip(entry_reference, direction, config.slippage_bps_per_side, entry=True)
        stop = float(signal["stop_price"])
        if (direction == "long" and stop >= entry_fill) or (direction == "short" and stop <= entry_fill):
            continue
        target = target_price(entry_fill, stop, direction, config.reward_risk)
        stop_fill = slip(stop, direction, config.slippage_bps_per_side, entry=False)
        price_loss = entry_fill - stop_fill if direction == "long" else stop_fill - entry_fill
        unit_stop_loss = price_loss + fee_fraction * (entry_fill + abs(stop_fill))
        if not math.isfinite(unit_stop_loss) or unit_stop_loss <= 0:
            continue

        risk_cash = equity * config.risk_per_trade
        desired_size = risk_cash / unit_stop_loss
        cap_notional = equity * min(config.max_asset_weight, config.max_gross_exposure)
        size = min(desired_size, cap_notional / entry_fill)
        if size <= 0:
            continue
        notional = size * entry_fill
        stop_budget_used = size * unit_stop_loss

        trade = Trade(
            id=len(trades),
            signal_id=str(signal["signal_id"]),
            fvg_id=int(signal["fvg_id"]),
            direction=direction,
            signal_time=pd.Timestamp(signal["signal_time"]),
            entry_time=pd.Timestamp(signal["entry_time"]),
            entry_reference_price=entry_reference,
            entry_price=entry_fill,
            stop_price=stop,
            target_price=target,
            size=float(size),
            notional=float(notional),
            risk_cash_budget=float(risk_cash),
            stop_loss_budget_used=float(stop_budget_used),
        )

        raw_exit = float(ltf["close"].iloc[-1])
        exit_i = len(ltf) - 1
        reason = "END_OF_DATA"
        for j in range(entry_i, len(ltf)):
            row = ltf.iloc[j]
            opening, high, low = float(row["open"]), float(row["high"]), float(row["low"])
            if direction == "long":
                if opening <= stop:
                    raw_exit, exit_i, reason = opening, j, "GAP_STOP"
                    break
                if opening >= target:
                    raw_exit, exit_i, reason = target, j, "GAP_TARGET"
                    break
                stop_hit, target_hit = low <= stop, high >= target
            else:
                if opening >= stop:
                    raw_exit, exit_i, reason = opening, j, "GAP_STOP"
                    break
                if opening <= target:
                    raw_exit, exit_i, reason = target, j, "GAP_TARGET"
                    break
                stop_hit, target_hit = high >= stop, low <= target
            if stop_hit:
                raw_exit, exit_i = stop, j
                reason = "STOP_FIRST" if target_hit else "STOP"
                break
            if target_hit:
                raw_exit, exit_i, reason = target, j, "TARGET"
                break

        exit_fill = slip(raw_exit, direction, config.slippage_bps_per_side, entry=False)
        side = 1.0 if direction == "long" else -1.0
        gross_pnl = side * (raw_exit - entry_reference) * size
        fee_costs = fee_fraction * size * (abs(entry_fill) + abs(exit_fill))
        slippage_costs = size * (abs(entry_fill - entry_reference) + abs(exit_fill - raw_exit))
        total_costs = fee_costs + slippage_costs
        net_pnl = gross_pnl - total_costs
        before = equity
        equity += net_pnl
        peak_equity = max(peak_equity, equity)
        account_return = equity / before - 1.0 if before > 0 else 0.0
        completed_returns.append(account_return)
        occupied_until = exit_i

        trade.exit_time = pd.Timestamp(ltf.index[exit_i]) + _rule_delta(config.ltf)
        trade.exit_reference_price = float(raw_exit)
        trade.exit_price = float(exit_fill)
        trade.exit_reason = reason
        trade.gross_pnl = float(gross_pnl)
        trade.fee_costs = float(fee_costs)
        trade.slippage_costs = float(slippage_costs)
        trade.total_costs = float(total_costs)
        trade.net_pnl = float(net_pnl)
        trade.r_multiple = float(net_pnl / stop_budget_used) if stop_budget_used > 0 else 0.0
        trades.append(trade)
        equity_rows.append(
            {
                "timestamp": trade.exit_time,
                "equity": equity,
                "drawdown": 1.0 - equity / peak_equity,
            }
        )

    trade_frame = pd.DataFrame([asdict(trade) for trade in trades])
    equity_frame = pd.DataFrame(equity_rows)
    if not equity_frame.empty:
        equity_frame = equity_frame.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)

    if trade_frame.empty:
        metrics = {
            "trades": 0,
            "win_rate": 0.0,
            "net_pnl": 0.0,
            "return_pct": 0.0,
            "profit_factor": 0.0,
            "avg_r": 0.0,
            "max_drawdown_pct": 0.0,
            "total_costs": 0.0,
            "fee_costs": 0.0,
            "slippage_costs": 0.0,
        }
    else:
        wins = float(trade_frame.loc[trade_frame["net_pnl"] > 0, "net_pnl"].sum())
        losses = -float(trade_frame.loc[trade_frame["net_pnl"] < 0, "net_pnl"].sum())
        if equity_frame.empty:
            max_dd = 0.0
        else:
            curve = equity_frame["equity"].astype(float)
            max_dd = float((curve / curve.cummax() - 1.0).min())
        metrics = {
            "trades": int(len(trade_frame)),
            "win_rate": float((trade_frame["net_pnl"] > 0).mean()),
            "net_pnl": float(trade_frame["net_pnl"].sum()),
            "return_pct": float(equity / config.initial_equity - 1.0),
            "profit_factor": float(wins / losses) if losses > 0 else (math.inf if wins > 0 else 0.0),
            "avg_r": float(trade_frame["r_multiple"].mean()),
            "max_drawdown_pct": max_dd,
            "total_costs": float(trade_frame["total_costs"].sum()),
            "fee_costs": float(trade_frame["fee_costs"].sum()),
            "slippage_costs": float(trade_frame["slippage_costs"].sum()),
        }

    return {
        "config": asdict(config),
        "trades": trade_frame,
        "equity": equity_frame,
        "fvgs": scan["fvgs"],
        "signals": signals,
        "metrics": metrics,
        "paper_execution": False,
        "live_execution": False,
    }


def write_backtest_outputs(result: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    result["trades"].to_csv(output_dir / "fvg_ict_trades.csv", index=False)
    result["fvgs"].to_csv(output_dir / "detected_fvgs.csv", index=False)
    result["equity"].to_csv(output_dir / "equity_curve.csv", index=False)
    result["signals"].to_csv(output_dir / "fvg_ict_signals.csv", index=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="V58 research-only FVG/ICT/TSI strategy")
    parser.add_argument("csv", help="timestamp,open,high,low,close,volume")
    parser.add_argument("--symbol", default="UNKNOWN")
    parser.add_argument("--venue", default="research_csv")
    parser.add_argument("--htf", default="1h")
    parser.add_argument("--ltf", default="5min")
    parser.add_argument("--rr", type=float, default=2.0)
    parser.add_argument("--confirmation", choices=["tsi", "structure", "either", "both"], default="either")
    parser.add_argument("--stop-mode", choices=["midpoint", "zone_edge", "swing"], default="zone_edge")
    parser.add_argument("--output-dir", type=Path, default=Path("."))
    args = parser.parse_args()
    cfg = FVGICTTSIConfig(
        htf=args.htf,
        ltf=args.ltf,
        reward_risk=args.rr,
        confirmation=args.confirmation,
        stop_mode=args.stop_mode,
    )
    result = backtest(pd.read_csv(args.csv), cfg, symbol=args.symbol, venue=args.venue)
    write_backtest_outputs(result, args.output_dir)
    metrics = result["metrics"]
    print("=== ICT/FVG/TSI RESEARCH BACKTEST ===")
    print(f"Trades: {metrics['trades']}")
    print(f"Win rate: {metrics['win_rate'] * 100:.2f}%")
    print(f"Net PnL: {metrics['net_pnl']:.2f}")
    print(f"Return: {metrics['return_pct'] * 100:.2f}%")
    print(f"Profit factor: {metrics['profit_factor']}")
    print(f"Average R: {metrics['avg_r']:.3f}")
    print(f"Max DD: {metrics['max_drawdown_pct'] * 100:.2f}%")
    print(f"Total costs: {metrics['total_costs']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
