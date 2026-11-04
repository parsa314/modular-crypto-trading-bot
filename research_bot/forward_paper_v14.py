from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Protocol

import numpy as np
import pandas as pd

from .coinex_depth import DepthSnapshot, fetch_coinex_depth
from .coinex_public import PERIOD_MS, fetch_coinex_klines
from .contracts import ExecutionMode
from .execution import ExecutionPolicy, ExecutionRequest, OrderSide, PaperExecutionEngine
from .execution_hardening_v52 import (
    DuplicateSettlementError,
    StaleMarketDataError,
    commit_fill_atomic,
    executable_reference_price,
    settlement_exists,
    validate_executable_quote,
)
from .ichimoku_advanced import detect_kumo_triangle_breakout
from .persistence import PaperAccount
from .risk import RiskDecision, RiskEngine, RiskLimits, RiskSnapshot


STRATEGY_VERSION = "ICHIMOKU_SHADOW_V14"


@dataclass(frozen=True)
class ForwardPaperConfig:
    symbols: tuple[str, ...] = ("BTC/USDT", "ETH/USDT")
    period: str = "4hour"
    bars: int = 280
    initial_cash: float = 10_000.0
    risk_fraction: float = 0.0025
    max_order_notional: float = 2_000.0
    entry_rule_score: float = 0.80
    fee_bps: float = 10.0
    slippage_bps: float = 2.0
    max_spread_bps: float = 35.0
    max_drawdown: float = 0.05
    max_gross_exposure: float = 0.70
    max_asset_weight: float = 0.25
    min_stop_fraction: float = 0.01
    atr_stop_multiple: float = 2.0
    max_quote_age_seconds: float = 30.0
    max_future_quote_skew_seconds: float = 5.0
    max_signal_age_minutes: float = 60.0

    def __post_init__(self) -> None:
        # Strategy-level paper limits cannot be relaxed by configuration.
        # This is NOT a durable daily-loss gate or a real broker stop order.
        import math

        checks = {
            "risk_fraction": (self.risk_fraction, 0.0025),
            "max_drawdown": (self.max_drawdown, 0.05),
            "max_gross_exposure": (self.max_gross_exposure, 0.70),
            "max_asset_weight": (self.max_asset_weight, 0.35),
        }
        for name, (value, ceiling) in checks.items():
            if not math.isfinite(value) or not 0 < value <= ceiling:
                raise ValueError(f"PAPER_RISK_CONSTITUTION_BREACH:{name}")
        if not math.isfinite(self.max_order_notional) or self.max_order_notional <= 0:
            raise ValueError("INVALID_PAPER_ORDER_NOTIONAL")


@dataclass(frozen=True)
class ShadowSignal:
    action: str
    rule_score: float
    reasons: tuple[str, ...]
    atr_pct: float
    reference_price: float
    features: dict


class ForwardMarketClient(Protocol):
    def klines(self, symbol: str, period: str, bars: int) -> pd.DataFrame: ...
    def depth(self, symbol: str) -> DepthSnapshot: ...


class CoinExForwardMarketClient:
    def klines(self, symbol: str, period: str, bars: int) -> pd.DataFrame:
        return fetch_coinex_klines(symbol, period=period, market_type="spot", bars=bars)

    def depth(self, symbol: str) -> DepthSnapshot:
        return fetch_coinex_depth(symbol)


def _last_closed_bars(df: pd.DataFrame, period: str, now: datetime | None = None) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    if period not in PERIOD_MS:
        raise ValueError(f"unsupported period: {period}")
    now = now or datetime.now(timezone.utc)
    x = df.copy()
    x["timestamp"] = pd.to_datetime(x["timestamp"], utc=True)
    close_time = x["timestamp"] + pd.Timedelta(milliseconds=PERIOD_MS[period])
    return x[close_time <= pd.Timestamp(now)].sort_values("timestamp").reset_index(drop=True)


def _atr_pct(df: pd.DataFrame, window: int = 14) -> float:
    if len(df) < window + 1:
        return float("nan")
    prev = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev).abs(),
            (df["low"] - prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1.0 / window, adjust=False).mean()
    last = float(df["close"].iloc[-1])
    return float(atr.iloc[-1] / last) if last > 0 else float("nan")


def frozen_shadow_signal(df: pd.DataFrame, currently_long: bool, entry_rule_score: float = 0.80) -> ShadowSignal:
    """Frozen transparent paper-only Ichimoku rule.

    The rule is deliberately not labelled as validated alpha. It exists to
    collect forward execution/risk evidence after v0.12 produced no promoted
    learned model.
    """
    if len(df) < 120:
        raise ValueError("at least 120 closed bars are required")
    x = detect_kumo_triangle_breakout(df)
    r = x.iloc[-1]
    mom6 = float(df["close"].iloc[-1] / df["close"].iloc[-7] - 1.0)
    checks = {
        "price_above_cloud": bool(float(r["ichi_price_above_cloud"]) >= 0.5),
        "tk_bullish": bool(float(r["ichi_tk_bullish"]) >= 0.5),
        "cloud_bullish": bool(float(r["ichi_cloud_bullish"]) >= 0.5),
        "price_above_kijun": bool(float(r["ichi_price_kijun_pct"]) > 0.0),
        "momentum_6_positive": bool(mom6 > 0.0),
    }
    score = float(sum(checks.values()) / len(checks))
    triangle = bool(float(r.get("triangle_candidate", 0.0)) >= 0.5)
    if triangle:
        score = 1.0

    below_cloud = bool(float(r["ichi_price_below_cloud"]) >= 0.5)
    bearish_tk_below_kijun = not checks["tk_bullish"] and float(r["ichi_price_kijun_pct"]) < 0.0
    reasons = [k.upper() for k, v in checks.items() if v]
    if triangle:
        reasons.append("TRIANGLE_KUMO_CANDIDATE")

    if currently_long and (below_cloud or bearish_tk_below_kijun):
        action = "EXIT"
        reasons.append("FROZEN_ICHIMOKU_EXIT")
    elif currently_long:
        action = "HOLD"
    elif score >= float(entry_rule_score):
        action = "BUY_CANDIDATE"
        reasons.append("FROZEN_ICHIMOKU_ENTRY_SCORE")
    else:
        action = "NO_TRADE"

    atr = _atr_pct(df)
    features = {
        "price_above_cloud": checks["price_above_cloud"],
        "tk_bullish": checks["tk_bullish"],
        "cloud_bullish": checks["cloud_bullish"],
        "price_above_kijun": checks["price_above_kijun"],
        "momentum_6": mom6,
        "triangle_candidate": triangle,
        "ichi_tk_distance_pct": float(r["ichi_tk_distance_pct"]),
        "ichi_price_kijun_pct": float(r["ichi_price_kijun_pct"]),
        "ichi_cloud_width_pct": float(r["ichi_cloud_width_pct"]),
    }
    return ShadowSignal(
        action=action,
        rule_score=score,
        reasons=tuple(reasons),
        atr_pct=float(atr),
        reference_price=float(df["close"].iloc[-1]),
        features=features,
    )


class ForwardPaperRunner:
    """Forward-only CoinEx paper observer/executor with restart deduplication.

    v0.52 hardening keeps the v0.14 strategy frozen but changes execution
    semantics: candle close is signal evidence only; fills use fresh top-of-book
    quotes and settlement is crash-consistent/idempotent at the persistence layer.
    """

    def __init__(
        self,
        store,
        *,
        config: ForwardPaperConfig | None = None,
        market_client: ForwardMarketClient | None = None,
        paper_execution_enabled: bool = False,
    ):
        self.store = store
        self.config = config or ForwardPaperConfig()
        self.market = market_client or CoinExForwardMarketClient()
        self.paper_execution_enabled = bool(paper_execution_enabled)
        self.risk_engine = RiskEngine(
            RiskLimits(
                max_drawdown=self.config.max_drawdown,
                max_gross_exposure=self.config.max_gross_exposure,
                max_asset_weight=self.config.max_asset_weight,
                max_turnover_per_step=0.50,
                max_spread_bps=self.config.max_spread_bps,
                max_slippage_bps=25.0,
                max_cvar_95=0.035,
                min_cash_buffer=0.05,
            )
        )
        self.execution = PaperExecutionEngine(
            ExecutionPolicy(
                mode=ExecutionMode.PAPER,
                fee_bps=self.config.fee_bps,
                slippage_bps=self.config.slippage_bps,
                max_order_notional=self.config.max_order_notional,
                live_execution_enabled=False,
            )
        )
        self._last_marks: dict[str, float] = {}

    def _mark_account(self, current_symbol: str, current_price: float) -> tuple[PaperAccount, float]:
        account = self.store.get_account()
        self._last_marks[current_symbol] = float(current_price)
        gross = 0.0
        position_value = 0.0
        for pos in self.store.list_positions():
            mark = self._last_marks.get(pos.symbol, pos.avg_price)
            value = float(pos.quantity * mark)
            gross += abs(value)
            position_value += value
        equity = float(account.cash + position_value)
        peak = max(float(account.peak_equity), equity)
        updated = PaperAccount(cash=float(account.cash), equity=equity, peak_equity=peak)
        self.store.set_account(updated)
        return updated, (gross / equity if equity > 0 else 0.0)

    def _signal_age_minutes(self, bar_ts: datetime, now: datetime) -> float:
        close_ts = pd.Timestamp(bar_ts) + pd.Timedelta(milliseconds=PERIOD_MS[self.config.period])
        return float((pd.Timestamp(now) - close_ts).total_seconds() / 60.0)

    def run_symbol(self, symbol: str, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        raw = self.market.klines(symbol, self.config.period, self.config.bars)
        bars = _last_closed_bars(raw, self.config.period, now=now)
        if len(bars) < 120:
            return {"symbol": symbol, "status": "INSUFFICIENT_CLOSED_BARS", "rows": int(len(bars))}

        position = self.store.get_position(symbol)
        signal = frozen_shadow_signal(
            bars,
            currently_long=position.quantity > 0,
            entry_rule_score=self.config.entry_rule_score,
        )
        bar_ts = pd.Timestamp(bars["timestamp"].iloc[-1]).to_pydatetime()
        signal_age_minutes = self._signal_age_minutes(bar_ts, now)
        if signal_age_minutes < -1e-6 or signal_age_minutes > self.config.max_signal_age_minutes:
            return {
                "symbol": symbol,
                "status": "STALE_SIGNAL_BAR",
                "bar_timestamp": bar_ts.isoformat(),
                "signal_age_minutes": signal_age_minutes,
            }

        depth = self.market.depth(symbol)
        try:
            quote_age_seconds = validate_executable_quote(
                depth,
                now=now,
                max_age_seconds=self.config.max_quote_age_seconds,
                max_future_skew_seconds=self.config.max_future_quote_skew_seconds,
            )
        except StaleMarketDataError as exc:
            return {
                "symbol": symbol,
                "status": "STALE_OR_INVALID_QUOTE",
                "error": str(exc),
                "bar_timestamp": bar_ts.isoformat(),
            }

        observation = {
            "observed_at": now.isoformat(),
            "bar_timestamp": bar_ts.isoformat(),
            "symbol": symbol,
            "strategy_version": STRATEGY_VERSION,
            "action": signal.action,
            "rule_score": signal.rule_score,
            "reference_price": signal.reference_price,
            "spread_bps": depth.spread_bps,
            "atr_pct": signal.atr_pct if np.isfinite(signal.atr_pct) else None,
            "features": {
                **signal.features,
                "depth_imbalance": depth.imbalance,
                "best_bid": depth.best_bid,
                "best_ask": depth.best_ask,
                "quote_timestamp": depth.timestamp.isoformat(),
                "quote_age_seconds": quote_age_seconds,
                "signal_age_minutes": signal_age_minutes,
                "evidence_label": "FORWARD_PAPER_HYPOTHESIS",
            },
            "reasons": list(signal.reasons),
        }
        observation_inserted = self.store.record_observation(observation)

        mark_price = float(depth.mid)
        account, gross_exposure = self._mark_account(symbol, mark_price)
        actionable = signal.action in {"BUY_CANDIDATE", "EXIT"}
        if not actionable or not self.paper_execution_enabled:
            if not observation_inserted:
                return {
                    "symbol": symbol,
                    "status": "ALREADY_OBSERVED_BAR",
                    "bar_timestamp": bar_ts.isoformat(),
                    "action": signal.action,
                }
            self.store.record_equity(
                {
                    "timestamp": now.isoformat(),
                    "equity": account.equity,
                    "cash": account.cash,
                    "gross_exposure": gross_exposure,
                    "metadata": {
                        "symbol": symbol,
                        "action": signal.action,
                        "paper_execution_enabled": self.paper_execution_enabled,
                    },
                }
            )
            return {
                "symbol": symbol,
                "status": "OBSERVED_ONLY" if not self.paper_execution_enabled else "ABSTAINED",
                "signal": asdict(signal),
                "depth": asdict(depth),
                "account": asdict(account),
            }

        side = OrderSide.BUY if signal.action == "BUY_CANDIDATE" else OrderSide.SELL
        client_order_id = (
            f"{STRATEGY_VERSION}:{symbol.replace('/','')}:"
            f"{int(pd.Timestamp(bar_ts).timestamp())}:{side.value}"
        )
        if settlement_exists(self.store, client_order_id):
            return {
                "symbol": symbol,
                "status": "ALREADY_SETTLED_ORDER",
                "client_order_id": client_order_id,
                "recovered_from_duplicate_observation": not observation_inserted,
            }

        execution_reference = executable_reference_price(depth, side)

        if signal.action == "BUY_CANDIDATE":
            stop_fraction = max(
                self.config.min_stop_fraction,
                self.config.atr_stop_multiple
                * (signal.atr_pct if np.isfinite(signal.atr_pct) else self.config.min_stop_fraction),
            )
            # Conservative estimated round-trip costs participate in the
            # sizing hypothesis; this does not imply an actual stop is placed.
            round_trip_cost_fraction = (
                2.0 * (self.config.fee_bps + self.config.slippage_bps)
                + max(0.0, depth.spread_bps)
            ) / 10_000.0
            risk_notional = self.risk_engine.position_size_from_risk(
                equity=account.equity,
                stop_distance_fraction=stop_fraction + round_trip_cost_fraction,
                risk_fraction=self.config.risk_fraction,
                volatility_scale=1.0,
            )
            notional = min(
                risk_notional,
                self.config.max_order_notional,
                max(0.0, account.cash * 0.90),
            )
            quantity = notional / execution_reference if execution_reference > 0 else 0.0
            proposed_weight = notional / account.equity if account.equity > 0 else 1.0
            turnover = proposed_weight
        else:
            max_exit_quantity = self.config.max_order_notional / execution_reference
            quantity = min(float(position.quantity), float(max_exit_quantity))
            notional = quantity * execution_reference
            proposed_weight = notional / account.equity if account.equity > 0 else 1.0
            turnover = proposed_weight

        if quantity <= 0:
            return {"symbol": symbol, "status": "NO_POSITION_OR_SIZE", "signal": asdict(signal)}

        all_in_cost_bps = float(self.config.slippage_bps + max(0.0, depth.spread_bps / 2.0))
        if signal.action == "EXIT":
            risk = RiskDecision(True, False, ("RISK_REDUCING_EXIT",), None)
        else:
            risk = self.risk_engine.evaluate(
                RiskSnapshot(
                    equity=account.equity,
                    peak_equity=account.peak_equity,
                    gross_exposure=gross_exposure + proposed_weight,
                    asset_weight=proposed_weight,
                    turnover=turnover,
                    spread_bps=float(depth.spread_bps),
                    slippage_bps=all_in_cost_bps,
                    recent_returns=tuple(self.store.recent_equity_returns(limit=100)),
                )
            )
        if not risk.approved:
            self.store.record_equity(
                {
                    "timestamp": now.isoformat(),
                    "equity": account.equity,
                    "cash": account.cash,
                    "gross_exposure": gross_exposure,
                    "metadata": {
                        "symbol": symbol,
                        "risk_rejected": list(risk.reasons),
                        "recovery_attempt": not observation_inserted,
                    },
                }
            )
            return {"symbol": symbol, "status": "RISK_REJECTED", "signal": asdict(signal), "risk": asdict(risk)}

        visible_qty = depth.ask_depth if side is OrderSide.BUY else depth.bid_depth
        if not np.isfinite(visible_qty) or visible_qty < 0.0:
            raise ValueError("visible depth must be finite and non-negative")
        fill_fraction = float(min(1.0, visible_qty / quantity)) if quantity > 0 else 0.0
        fill = self.execution.execute(
            ExecutionRequest(
                client_order_id=client_order_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                reference_price=execution_reference,
                created_at=now,
            ),
            fill_fraction=fill_fraction,
            # spread is already represented by bid/ask selection; do not charge it twice.
            extra_slippage_bps=0.0,
        )
        if fill.filled_quantity == 0.0:
            self.execution.release_unsettled(client_order_id)
            return {
                "symbol": symbol,
                "status": "NO_LIQUIDITY",
                "signal": asdict(signal),
                "risk": asdict(risk),
                "fill": {
                    **asdict(fill),
                    "side": fill.side.value,
                    "mode": fill.mode.value,
                    "timestamp": fill.timestamp.isoformat(),
                },
                "account": asdict(account),
            }

        metadata = {
            "rule_score": signal.rule_score,
            "risk_reasons": list(risk.reasons),
            "spread_bps": float(depth.spread_bps),
            "paper_only": True,
            "signal_reference_price": float(signal.reference_price),
            "execution_reference_price": float(execution_reference),
            "mark_price": mark_price,
            "quote_timestamp": depth.timestamp.isoformat(),
            "quote_age_seconds": quote_age_seconds,
            "signal_age_minutes": signal_age_minutes,
            "recovery_attempt": not observation_inserted,
        }
        try:
            commit_fill_atomic(
                self.store,
                symbol=symbol,
                fill=fill,
                strategy_version=STRATEGY_VERSION,
                metadata=metadata,
            )
        except DuplicateSettlementError:
            return {
                "symbol": symbol,
                "status": "ALREADY_SETTLED_ORDER",
                "client_order_id": client_order_id,
            }
        except Exception as exc:
            try:
                committed = settlement_exists(self.store, client_order_id)
            except Exception as reconciliation_exc:
                raise RuntimeError("SETTLEMENT_OUTCOME_UNKNOWN") from reconciliation_exc
            if committed:
                return {
                    "symbol": symbol,
                    "status": "ALREADY_SETTLED_ORDER",
                    "client_order_id": client_order_id,
                    "recovered_after_commit_error": True,
                }
            self.execution.release_unsettled(client_order_id)
            raise exc

        account, gross_exposure = self._mark_account(symbol, mark_price)
        self.store.record_equity(
            {
                "timestamp": now.isoformat(),
                "equity": account.equity,
                "cash": account.cash,
                "gross_exposure": gross_exposure,
                "metadata": {"symbol": symbol, "fill_status": fill.status},
            }
        )
        return {
            "symbol": symbol,
            "status": "EXECUTED_PAPER",
            "signal": asdict(signal),
            "risk": asdict(risk),
            "fill": {
                **asdict(fill),
                "side": fill.side.value,
                "mode": fill.mode.value,
                "timestamp": fill.timestamp.isoformat(),
            },
            "account": asdict(account),
            "recovery_attempt": not observation_inserted,
        }

    def run_cycle(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        rows = []
        for symbol in self.config.symbols:
            try:
                rows.append(self.run_symbol(symbol, now=now))
            except Exception as exc:
                rows.append({"symbol": symbol, "status": "ERROR", "error": f"{type(exc).__name__}: {exc}"})
        return {
            "research_status": "V14_FORWARD_PAPER_RUNNING_NOT_LIVE",
            "strategy_version": STRATEGY_VERSION,
            "timestamp": now.isoformat(),
            "paper_execution_enabled": self.paper_execution_enabled,
            "live_execution": False,
            "results": rows,
            "store": self.store.summary(),
        }
