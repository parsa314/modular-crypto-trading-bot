"""Deterministic shared-cash portfolio accounting for synthetic fixtures only.

This module creates no orders. Intrabar outcomes are available at bar close;
they cannot replenish cash for another decision at that bar's open.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

import pandas as pd

from research_bot.risk import RiskEngine, RiskLimits, RiskSnapshot, historical_cvar

from .contracts import assert_research_only
from .features import add_v58_continuous_features
from .safety import DrawdownRiskGate


@dataclass(frozen=True)
class PortfolioConfig:
    initial_equity: float = 10_000.0
    risk_per_trade: float = 0.0025
    max_asset_weight: float = 0.35
    max_gross_exposure: float = 0.70
    drawdown_kill: float = 0.05
    round_trip_cost_bps: float = 24.0
    max_cvar95: float = 0.035

    def __post_init__(self) -> None:
        ceilings = {"risk_per_trade": 0.0025, "max_asset_weight": 0.35,
                    "max_gross_exposure": 0.70, "drawdown_kill": 0.05,
                    "max_cvar95": 0.035}
        for name, value in asdict(self).items():
            if isinstance(value, bool):
                raise ValueError(f"{name} must be finite numeric data")
            try:
                value = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} must be finite numeric data") from exc
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite numeric data")
            if name == "round_trip_cost_bps":
                if not 0 <= value < 20_000:
                    raise ValueError("round_trip_cost_bps must be in [0, 20000)")
            elif value <= 0 or (name in ceilings and value > ceilings[name]):
                raise ValueError(f"{name} must be positive and cannot relax frozen V58 risk limits")
            object.__setattr__(self, name, value)


def _canonical_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a nonempty canonical string")
    return value


def _finite(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite numeric data")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite numeric data") from exc
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError(f"{name} must be finite" + (" and positive" if positive else ""))
    return number


def _utc(value: Any, name: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp) or timestamp.tzinfo is None or timestamp.utcoffset().total_seconds() != 0:
        raise ValueError(f"{name} must be non-null timezone-aware UTC")
    return timestamp.tz_convert("UTC")


def _validated_inputs(bars: dict[str, pd.DataFrame], decisions: list[dict]) -> tuple[dict[str, pd.DataFrame], list[dict]]:
    if not isinstance(bars, dict) or not bars:
        raise ValueError("bars must contain at least one synthetic symbol")
    prepared: dict[str, pd.DataFrame] = {}
    clock: pd.Series | None = None
    for symbol, frame in sorted(bars.items()):
        _canonical_text(symbol, "symbol")
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            raise ValueError("bars must be nonempty OHLCV dataframes")
        # Reuse the feature module's strict raw OHLCV/UTC/contiguity contract.
        raw = add_v58_continuous_features(frame, timeframe="4h")
        raw = raw[["timestamp", "open", "high", "low", "close", "volume"]].copy()
        raw[["open", "high", "low", "close", "volume"]] = raw[["open", "high", "low", "close", "volume"]].astype(float)
        if clock is not None and not raw["timestamp"].equals(clock):
            raise ValueError("all synthetic symbols must share the same contiguous 4h clock")
        clock = raw["timestamp"]
        prepared[symbol] = raw.set_index("timestamp", drop=False)
    if not isinstance(decisions, list):
        raise ValueError("decisions must be a list of synthetic intents")
    seen: set[str] = set()
    candidates = []
    for source in decisions:
        if not isinstance(source, dict):
            raise ValueError("each decision must be a dictionary")
        required = {"event_id", "venue", "symbol", "decision_at", "entry_price", "stop_price",
                    "target_price", "horizon_bars", "expected_utility"}
        if required - source.keys():
            raise ValueError(f"decision missing fields: {sorted(required - source.keys())}")
        event_id = _canonical_text(source["event_id"], "event_id")
        if event_id in seen:
            raise ValueError("duplicate event_id forbidden, including abstained intents")
        seen.add(event_id)
        if source["venue"] != "synthetic":
            raise PermissionError("portfolio simulator accepts synthetic venue only")
        symbol = _canonical_text(source["symbol"], "symbol")
        if symbol not in prepared:
            raise ValueError("decision symbol has no synthetic bars")
        if source.get("direction", "LONG") != "LONG" or source.get("market_type", "spot") != "spot":
            raise ValueError("only LONG spot synthetic decisions are supported")
        policy = source.get("policy", "ML_UTILITY")
        if policy not in {"ML_UTILITY", "STRATEGY_ONLY"}:
            raise ValueError("policy must be ML_UTILITY or STRATEGY_ONLY")
        timestamp = _utc(source["decision_at"], "decision_at")
        if timestamp not in prepared[symbol].index:
            raise ValueError("decision_at must identify the exact immediate next bar open; missing/future bars cannot be shifted")
        entry = _finite(source["entry_price"], "entry_price", positive=True)
        stop = _finite(source["stop_price"], "stop_price", positive=True)
        target = _finite(source["target_price"], "target_price", positive=True)
        if entry != float(prepared[symbol].at[timestamp, "open"]):
            raise ValueError("entry_price must exactly match the decision bar open")
        if not stop < entry < target:
            raise ValueError("LONG requires stop_price < entry_price < target_price")
        horizon = source["horizon_bars"]
        if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon <= 0:
            raise ValueError("horizon_bars must be a positive integer")
        candidate = {"event_id": event_id, "venue": "synthetic", "symbol": symbol,
                     "decision_at": timestamp, "entry_price": entry, "stop_price": stop,
                     "target_price": target, "horizon_bars": horizon,
                     "policy": policy,
                     "expected_utility": _finite(source["expected_utility"], "expected_utility"),
                     "admission_reason": _canonical_text(source.get("admission_reason", "ADMITTED"), "admission_reason")}
        for name in ("entropy", "shift_score"):
            if name in source:
                value = _finite(source[name], name)
                if value < 0 or (name == "entropy" and value > 1):
                    raise ValueError(f"{name} out of range")
                candidate[name] = value
        candidates.append(candidate)
    return prepared, sorted(candidates, key=lambda row: (row["decision_at"], -row["expected_utility"], row["event_id"]))


def simulate_synthetic_portfolio(
    bars: dict[str, pd.DataFrame], decisions: list[dict], config: PortfolioConfig | None = None,
) -> dict:
    """Run a spot-only shared-cash simulation with independent pre-trade vetoes.

    ``decision_at`` is both the closed signal bar's end and the immediate
    next bar's open. Every priority key must already be known at that instant.
    The caller supplies prior-only expected utility; outcomes are never read
    during allocation. Stop sizing includes entry and stop-fill exit fees.
    Gap loss can exceed that budget, and permanently latches the drawdown gate.
    """
    assert_research_only()
    cfg = PortfolioConfig() if config is None else config
    if not isinstance(cfg, PortfolioConfig):
        raise ValueError("config must be PortfolioConfig")
    cfg.__post_init__()
    frames, candidates = _validated_inputs(bars, decisions)
    clock = next(iter(frames.values())).index
    limits = RiskLimits(max_drawdown=cfg.drawdown_kill, max_gross_exposure=cfg.max_gross_exposure,
                        max_asset_weight=cfg.max_asset_weight, max_turnover_per_step=cfg.max_gross_exposure,
                        max_cvar_95=cfg.max_cvar95)
    risk_engine = RiskEngine(limits)
    drawdown_gate = DrawdownRiskGate(kill_threshold=cfg.drawdown_kill)
    fee_fraction = cfg.round_trip_cost_bps / 20_000.0
    cash = float(cfg.initial_equity)
    peak = cash
    max_drawdown = 0.0
    max_exposure = 0.0
    total_costs = 0.0
    positions: dict[str, dict] = {}
    fills: list[dict] = []
    ledger: list[dict] = []
    equity_curve: list[dict] = []
    completed_returns: list[float] = []
    previous_close_equity = cash
    by_clock: dict[pd.Timestamp, list[dict]] = {}
    for candidate in candidates:
        by_clock.setdefault(candidate["decision_at"], []).append(candidate)

    def state(prices: dict[str, float]) -> tuple[float, float]:
        gross = sum(position["quantity"] * prices[symbol] for symbol, position in positions.items())
        return cash + gross, gross

    def observe(prices: dict[str, float]) -> tuple[float, float]:
        nonlocal peak, max_drawdown, max_exposure
        equity, gross = state(prices)
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, 1.0 - equity / peak)
        max_exposure = max(max_exposure, gross / equity)
        drawdown_gate.evaluate(equity=equity, peak_equity=peak, new_risk=False)
        return equity, gross

    def record(timestamp: pd.Timestamp, phase: str, prices: dict[str, float]) -> None:
        equity, gross = observe(prices)
        equity_curve.append({
            "timestamp": timestamp.isoformat(), "phase": phase, "cash": float(cash),
            "equity": float(equity), "gross_exposure": float(gross / equity),
            "drawdown": float(1.0 - equity / peak), "drawdown_killed": drawdown_gate.killed,
            "positions": {symbol: {"event_id": position["event_id"], "quantity": position["quantity"],
                                    "mark_price": prices[symbol], "market_value": position["quantity"] * prices[symbol],
                                    "weight": position["quantity"] * prices[symbol] / equity}
                          for symbol, position in sorted(positions.items())},
        })

    def exit_position(symbol: str, price: float, timestamp: pd.Timestamp, reason: str,
                      *, ambiguous: bool = False) -> None:
        nonlocal cash, total_costs
        position = positions.pop(symbol)
        notional = position["quantity"] * price
        fee = notional * fee_fraction
        cash += notional - fee
        total_costs += fee
        fills.append({"event_id": position["event_id"], "symbol": symbol, "side": "SELL",
                      "timestamp": timestamp.isoformat(), "price": float(price),
                      "quantity": position["quantity"], "notional": float(notional), "fee": float(fee),
                      "reason": reason, "intrabar_ambiguity": bool(ambiguous)})

    def snapshot(equity: float, gross: float, asset: float, turnover: float) -> RiskSnapshot:
        return RiskSnapshot(equity=equity, peak_equity=peak, gross_exposure=gross / equity,
                            asset_weight=asset / equity, turnover=turnover / equity,
                            spread_bps=0.0, slippage_bps=0.0, recent_returns=tuple(completed_returns))

    for index, timestamp in enumerate(clock):
        opens = {symbol: float(frame.at[timestamp, "open"]) for symbol, frame in frames.items()}
        closes = {symbol: float(frame.at[timestamp, "close"]) for symbol, frame in frames.items()}
        # Resting gap exits are settled before MTM/peak observation. A target
        # limit fills at its target under this conservative convention, so an
        # overshooting open is not realizable equity for that exited position.
        # Actual gap-stop losses and fees remain in cash and enter OPEN risk.
        for symbol, position in list(sorted(positions.items())):
            if opens[symbol] <= position["stop_price"]:
                exit_position(symbol, opens[symbol], timestamp, "GAP_STOP")
            elif opens[symbol] >= position["target_price"]:
                # Conservative limit-target convention matches V58 barriers.
                exit_position(symbol, position["target_price"], timestamp, "GAP_TARGET")
        record(timestamp, "OPEN", opens)
        turnover_notional = 0.0
        for candidate in by_clock.get(timestamp, []):
            event_id, symbol = candidate["event_id"], candidate["symbol"]
            row = {"event_id": event_id, "venue": "synthetic", "symbol": symbol,
                   "decision_at": timestamp.isoformat(), "expected_utility": candidate["expected_utility"],
                   "policy": candidate["policy"],
                   "admitted": False, "reason": "", "notional": 0.0}
            for name in ("entropy", "shift_score"):
                if name in candidate:
                    row[name] = candidate[name]
            ledger.append(row)
            if candidate["admission_reason"] != "ADMITTED":
                row["reason"] = candidate["admission_reason"]
                continue
            if candidate["policy"] == "ML_UTILITY" and candidate["expected_utility"] <= 0:
                row["reason"] = "NON_POSITIVE_EXPECTED_UTILITY"
                continue
            equity, gross = observe(opens)
            if not drawdown_gate.evaluate(equity=equity, peak_equity=peak, new_risk=True):
                row["reason"] = "DRAWDOWN_KILL"
                continue
            if symbol in positions:
                row["reason"] = "ASSET_POSITION_OPEN"
                continue
            current_weights = [p["quantity"] * opens[s] for s, p in positions.items()]
            current_risk = risk_engine.evaluate(snapshot(equity, gross, max(current_weights, default=0.0), turnover_notional))
            if not current_risk.approved:
                row["reason"] = current_risk.reasons[0]
                row["risk_reasons"] = list(current_risk.reasons)
                continue
            entry, stop = candidate["entry_price"], candidate["stop_price"]
            # Loss at stop includes entry fee and exit fee on actual stop notional.
            stop_loss_fraction = (entry - stop + fee_fraction * (entry + stop)) / entry
            risk_size = risk_engine.position_size_from_risk(equity=equity, stop_distance_fraction=stop_loss_fraction,
                                                           risk_fraction=cfg.risk_per_trade)
            # Reserve all possible remaining entry fees before assigning an
            # asset's cap. Otherwise a later admission's fee could push an
            # already admitted asset above its frozen weight limit.
            full_gross_capacity = max(0.0, (cfg.max_gross_exposure * equity - gross)
                                      / (1.0 + cfg.max_gross_exposure * fee_fraction))
            fully_funded_equity = equity - fee_fraction * full_gross_capacity
            # Other caps are solved against equity after this entry's fee.
            capacity = min(
                cfg.max_asset_weight * fully_funded_equity,
                (cfg.max_gross_exposure * equity - gross) / (1.0 + cfg.max_gross_exposure * fee_fraction),
                (limits.max_turnover_per_step * equity - turnover_notional) / (1.0 + limits.max_turnover_per_step * fee_fraction),
                (cash - limits.min_cash_buffer * equity) / (1.0 + fee_fraction * (1.0 - limits.min_cash_buffer)),
                cash / (1.0 + fee_fraction),
            )
            # Conservative numeric margin prevents binary-float rounding
            # from presenting an exact-cap allocation as a risk breach.
            notional = min(risk_size, max(0.0, capacity) * (1.0 - 1e-12))
            if notional <= equity * 1e-10:
                row["reason"] = "NO_PORTFOLIO_CAPACITY"
                continue
            fee = notional * fee_fraction
            proposed_equity = equity - fee
            proposed_risk = risk_engine.evaluate(snapshot(proposed_equity, gross + notional,
                                                          max([notional, *current_weights]),
                                                          turnover_notional + notional))
            if not proposed_risk.approved:
                row["reason"] = proposed_risk.reasons[0]
                row["risk_reasons"] = list(proposed_risk.reasons)
                continue
            if not drawdown_gate.evaluate(equity=proposed_equity, peak_equity=peak, new_risk=True):
                row["reason"] = "DRAWDOWN_KILL"
                continue
            quantity = notional / entry
            cash -= notional + fee
            total_costs += fee
            turnover_notional += notional
            positions[symbol] = {**candidate, "quantity": float(quantity), "entry_index": index}
            row.update(admitted=True, reason="ADMITTED", notional=float(notional),
                       stop_risk=float(quantity * (entry - stop + fee_fraction * (entry + stop))))
            fills.append({"event_id": event_id, "symbol": symbol, "side": "BUY",
                          "timestamp": timestamp.isoformat(), "price": float(entry), "quantity": float(quantity),
                          "notional": float(notional), "fee": float(fee), "reason": "ENTRY",
                          "intrabar_ambiguity": False})
        record(timestamp, "AFTER_ADMISSIONS", opens)
        # Only now inspect the intrabar path: no future proceeds financed admissions.
        close_timestamp = timestamp + pd.Timedelta(hours=4)
        for symbol, position in list(sorted(positions.items())):
            bar = frames[symbol].loc[timestamp]
            stop_hit = float(bar["low"]) <= position["stop_price"]
            target_hit = float(bar["high"]) >= position["target_price"]
            if stop_hit:
                exit_position(symbol, position["stop_price"], close_timestamp, "STOP", ambiguous=target_hit)
            elif target_hit:
                exit_position(symbol, position["target_price"], close_timestamp, "TARGET")
            elif index - position["entry_index"] + 1 >= position["horizon_bars"]:
                exit_position(symbol, closes[symbol], close_timestamp, "TIMEOUT")
        if index == len(clock) - 1:
            for symbol in sorted(list(positions)):
                exit_position(symbol, closes[symbol], close_timestamp, "TERMINAL_LIQUIDATION")
        record(close_timestamp, "CLOSE", closes)
        close_equity, _ = state(closes)
        completed_returns.append(close_equity / previous_close_equity - 1.0)
        previous_close_equity = close_equity

    cvar = historical_cvar(completed_returns)
    return {
        "classification": "SYNTHETIC_ENGINEERING_ONLY", "empirical_training": False,
        "paper_execution": False, "live_execution": False, "broad_promotion": False,
        "config": asdict(cfg), "decisions": ledger, "fills": fills, "equity_curve": equity_curve,
        "summary": {"initial_equity": float(cfg.initial_equity), "final_equity": float(cash),
                    "net_return": float(cash / cfg.initial_equity - 1.0), "max_drawdown": float(max_drawdown),
                    "total_costs": float(total_costs), "max_gross_exposure": float(max_exposure),
                    "trade_count": sum(fill["side"] == "SELL" for fill in fills),
                    "admitted_count": sum(row["admitted"] for row in ledger),
                    "drawdown_kill_triggered": drawdown_gate.killed,
                    "cvar_95": None if cvar is None else float(max(0.0, cvar))},
        "unavailable": {"correlation_estimates": None, "market_impact": None,
                        "reasons": ["Synthetic fixtures cannot establish empirical asset correlations or market impact."]},
        "assumptions": {"bar_duration_seconds": 14_400, "direction": "LONG", "market_type": "spot",
                        "intrabar_ambiguity": "STOP_FIRST", "intrabar_information_time": "BAR_CLOSE",
                        "gap_target_fill": "TARGET_LIMIT_PRICE", "fees": "HALF_ROUND_TRIP_ON_EACH_FILLED_NOTIONAL",
                        "cvar_returns": "COMPLETED_CLOSE_TO_CLOSE", "min_cash_buffer": limits.min_cash_buffer,
                        "turnover_cap": limits.max_turnover_per_step,
                        "notional_dust_threshold_equity_fraction": 1e-10,
                        "expected_utility": "Caller-supplied prior-only net utility; not recomputed from future outcomes."},
    }
