from __future__ import annotations

"""Direct registered-strategy -> MT5 DEMO execution path.

This module lets the existing deterministic strategy registry consume completed
MT5 bars and submit bracketed orders directly to the already connected
MT5DemoExecutor. TradingView is not required for this path.

Scientific boundary:
- this is DEMO forward/execution validation;
- it does not authorize real-money execution;
- the selected strategy remains a research candidate, not a profitability claim.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import threading
from typing import Any

import pandas as pd

from .execution import ExecutionRequest, OrderSide
from .execution_adapters import (
    MT5DemoExecutionResult,
    MT5DemoExecutor,
)
from .multitimeframe_strategies_v19 import (
    STRATEGY_REGISTRY,
    StrategySpec,
    generate_direction,
)
from .risk import RiskEngine


class MT5DirectStrategyError(RuntimeError):
    pass


class DuplicateDirectSignalError(MT5DirectStrategyError):
    pass


@dataclass(frozen=True)
class MT5DirectStrategyConfig:
    canonical_symbol: str
    venue_symbol: str
    strategy_name: str
    bars: int = 600
    risk_fraction: float = 0.0025
    min_stop_fraction: float = 0.0005
    journal_path: str = "results/mt5_direct_strategy_journal.jsonl"
    one_bot_position_per_symbol: bool = True

    def __post_init__(self) -> None:
        if not str(self.canonical_symbol).strip():
            raise ValueError("canonical_symbol is required")
        if not str(self.venue_symbol).strip():
            raise ValueError("venue_symbol is required")
        if not str(self.strategy_name).strip():
            raise ValueError("strategy_name is required")
        if int(self.bars) < 240:
            raise ValueError("bars must be >= 240")
        if not math.isfinite(float(self.risk_fraction)):
            raise ValueError("risk_fraction must be finite")
        if not 0.0 < float(self.risk_fraction) <= 0.01:
            raise ValueError("risk_fraction must be in (0, 0.01]")
        if (
            not math.isfinite(float(self.min_stop_fraction))
            or float(self.min_stop_fraction) <= 0.0
        ):
            raise ValueError("min_stop_fraction must be positive and finite")


@dataclass(frozen=True)
class MT5DirectStrategyOutcome:
    status: str
    reason: str
    strategy_name: str
    canonical_symbol: str
    venue_symbol: str
    signal_id: str
    signal_time: str
    direction: int
    side: str | None
    reference_price: float | None
    stop_loss: float | None
    take_profit: float | None
    requested_quantity: float | None
    requested_notional: float | None
    risk_fraction: float
    spread_bps: float | None
    order_ticket: int | None = None
    deal_ticket: int | None = None
    execution_status: str | None = None


class DirectStrategyJournal:
    """Append-only direct-strategy intent/result journal.

    An INTENT is persisted before network order submission. A repeated signal_id
    is then refused after process restart as well as within one process.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _rows_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                raw = line.strip()
                if not raw:
                    continue
                try:
                    item = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise MT5DirectStrategyError(
                        f"invalid direct-strategy journal JSON at line {line_no}"
                    ) from exc
                if not isinstance(item, dict):
                    raise MT5DirectStrategyError(
                        f"invalid direct-strategy journal row at line {line_no}"
                    )
                out.append(item)
        return out

    def _append_unlocked(self, row: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            row,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def reserve_intent(self, signal_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            rows = self._rows_unlocked()
            if any(
                row.get("signal_id") == signal_id
                and row.get("record_type") == "INTENT"
                for row in rows
            ):
                raise DuplicateDirectSignalError(signal_id)
            self._append_unlocked(
                {
                    "record_type": "INTENT",
                    "signal_id": signal_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "payload": payload,
                }
            )

    def record_result(
        self,
        signal_id: str,
        result: MT5DemoExecutionResult,
    ) -> None:
        with self._lock:
            payload = asdict(result)
            payload["timestamp"] = result.timestamp.astimezone(
                timezone.utc
            ).isoformat()
            self._append_unlocked(
                {
                    "record_type": "EXECUTION_RESULT",
                    "signal_id": signal_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "result": payload,
                }
            )

    def record_error(self, signal_id: str, error: Exception) -> None:
        with self._lock:
            self._append_unlocked(
                {
                    "record_type": "EXECUTION_ERROR",
                    "signal_id": signal_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "error": f"{type(error).__name__}: {error}"[:1000],
                }
            )

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._rows_unlocked()
        return rows[-max(1, int(limit)) :]


def registered_strategy_names() -> tuple[str, ...]:
    return tuple(spec.name for spec in STRATEGY_REGISTRY)


def _find_strategy(name: str) -> StrategySpec:
    wanted = str(name).strip()
    for spec in STRATEGY_REGISTRY:
        if spec.name == wanted:
            return spec
    raise ValueError(
        f"unknown strategy_name={wanted}; choose one of "
        + ", ".join(registered_strategy_names())
    )


def _signal_id(
    *,
    strategy_name: str,
    canonical_symbol: str,
    venue_symbol: str,
    signal_time: str,
    direction: int,
) -> str:
    raw = (
        f"{strategy_name}|{canonical_symbol}|{venue_symbol}|"
        f"{signal_time}|{int(direction)}"
    )
    return "direct-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class DirectMT5StrategyRunner:
    """Evaluate one registered strategy on completed MT5 bars and execute DEMO."""

    def __init__(
        self,
        executor: MT5DemoExecutor,
        config: MT5DirectStrategyConfig,
        *,
        risk_engine: RiskEngine | None = None,
        journal: DirectStrategyJournal | None = None,
    ):
        self.executor = executor
        self.config = config
        self.strategy = _find_strategy(config.strategy_name)
        if self.strategy.timeframe not in {
            "1m",
            "5m",
            "15m",
            "1h",
            "4h",
            "1d",
        }:
            raise ValueError(
                f"strategy timeframe unsupported by direct MT5 runner: "
                f"{self.strategy.timeframe}"
            )
        self.risk_engine = risk_engine or RiskEngine()
        self.journal = journal or DirectStrategyJournal(config.journal_path)

    def _closed_frame(self) -> pd.DataFrame:
        rows = self.executor.copy_closed_bars(
            self.config.venue_symbol,
            self.strategy.timeframe,
            count=self.config.bars,
        )
        if len(rows) < 240:
            raise MT5DirectStrategyError(
                f"insufficient completed MT5 bars: {len(rows)}"
            )
        frame = pd.DataFrame(rows)
        frame["timestamp"] = pd.to_datetime(
            frame["time"],
            unit="s",
            utc=True,
        )
        frame["volume"] = pd.to_numeric(
            frame["tick_volume"],
            errors="coerce",
        )
        return frame[
            ["timestamp", "open", "high", "low", "close", "volume"]
        ].copy()

    def evaluate_once(self) -> MT5DirectStrategyOutcome:
        if not self.executor.connected:
            raise MT5DirectStrategyError("MT5 DEMO is not connected")

        frame = self._closed_frame()
        direction_series, features = generate_direction(
            self.strategy,
            frame,
        )
        if features.empty or direction_series.empty:
            raise MT5DirectStrategyError("strategy produced no usable feature rows")

        row = features.iloc[-1]
        direction = int(direction_series.iloc[-1])
        signal_time = pd.Timestamp(row["timestamp"]).isoformat()
        sid = _signal_id(
            strategy_name=self.strategy.name,
            canonical_symbol=self.config.canonical_symbol,
            venue_symbol=self.config.venue_symbol,
            signal_time=signal_time,
            direction=direction,
        )

        if direction not in {-1, 1}:
            return MT5DirectStrategyOutcome(
                status="NO_SIGNAL",
                reason="latest completed MT5 bar has no entry signal",
                strategy_name=self.strategy.name,
                canonical_symbol=self.config.canonical_symbol,
                venue_symbol=self.config.venue_symbol,
                signal_id=sid,
                signal_time=signal_time,
                direction=0,
                side=None,
                reference_price=None,
                stop_loss=None,
                take_profit=None,
                requested_quantity=None,
                requested_notional=None,
                risk_fraction=self.config.risk_fraction,
                spread_bps=None,
            )

        if self.config.one_bot_position_per_symbol:
            existing = self.executor.bot_positions(self.config.venue_symbol)
            if existing:
                return MT5DirectStrategyOutcome(
                    status="POSITION_EXISTS",
                    reason="bot-owned position already exists for venue symbol",
                    strategy_name=self.strategy.name,
                    canonical_symbol=self.config.canonical_symbol,
                    venue_symbol=self.config.venue_symbol,
                    signal_id=sid,
                    signal_time=signal_time,
                    direction=direction,
                    side="BUY" if direction > 0 else "SELL",
                    reference_price=None,
                    stop_loss=None,
                    take_profit=None,
                    requested_quantity=None,
                    requested_notional=None,
                    risk_fraction=self.config.risk_fraction,
                    spread_bps=None,
                )

        quote = self.executor.market_snapshot(self.config.venue_symbol)
        side = OrderSide.BUY if direction > 0 else OrderSide.SELL
        reference_price = (
            float(quote["ask"]) if direction > 0 else float(quote["bid"])
        )
        spread_bps = float(quote["spread_bps"])
        if spread_bps > float(self.executor.config.max_spread_bps):
            return MT5DirectStrategyOutcome(
                status="RISK_REJECTED",
                reason=f"spread too wide: {spread_bps:.4f} bps",
                strategy_name=self.strategy.name,
                canonical_symbol=self.config.canonical_symbol,
                venue_symbol=self.config.venue_symbol,
                signal_id=sid,
                signal_time=signal_time,
                direction=direction,
                side=side.value,
                reference_price=reference_price,
                stop_loss=None,
                take_profit=None,
                requested_quantity=None,
                requested_notional=None,
                risk_fraction=self.config.risk_fraction,
                spread_bps=spread_bps,
            )

        atr = float(row.get("atr", math.nan))
        if not math.isfinite(atr) or atr <= 0.0:
            raise MT5DirectStrategyError("latest ATR is unavailable or invalid")

        stop_distance = max(
            float(self.strategy.stop_atr) * atr,
            reference_price * float(self.config.min_stop_fraction),
        )
        if direction > 0:
            stop_loss = reference_price - stop_distance
            take_profit = reference_price + float(self.strategy.rr) * stop_distance
        else:
            stop_loss = reference_price + stop_distance
            take_profit = reference_price - float(self.strategy.rr) * stop_distance

        stop_fraction = stop_distance / reference_price
        account = self.executor.account_summary()
        equity = float(account.get("equity", 0.0) or 0.0)
        if equity <= 0.0:
            raise MT5DirectStrategyError("connected DEMO account equity is invalid")

        requested_notional = self.risk_engine.position_size_from_risk(
            equity=equity,
            stop_distance_fraction=stop_fraction,
            risk_fraction=float(self.config.risk_fraction),
            volatility_scale=1.0,
        )
        requested_notional = min(
            float(requested_notional),
            float(self.executor.config.max_order_notional),
        )
        if (
            not math.isfinite(requested_notional)
            or requested_notional <= 0.0
        ):
            return MT5DirectStrategyOutcome(
                status="RISK_REJECTED",
                reason="risk sizing produced zero/invalid notional",
                strategy_name=self.strategy.name,
                canonical_symbol=self.config.canonical_symbol,
                venue_symbol=self.config.venue_symbol,
                signal_id=sid,
                signal_time=signal_time,
                direction=direction,
                side=side.value,
                reference_price=reference_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                requested_quantity=None,
                requested_notional=requested_notional,
                risk_fraction=self.config.risk_fraction,
                spread_bps=spread_bps,
            )

        quantity = requested_notional / reference_price
        if not self.executor.submission_enabled:
            return MT5DirectStrategyOutcome(
                status="READY_DRY_RUN",
                reason="valid direct strategy signal; DEMO submission is disabled",
                strategy_name=self.strategy.name,
                canonical_symbol=self.config.canonical_symbol,
                venue_symbol=self.config.venue_symbol,
                signal_id=sid,
                signal_time=signal_time,
                direction=direction,
                side=side.value,
                reference_price=reference_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                requested_quantity=quantity,
                requested_notional=requested_notional,
                risk_fraction=self.config.risk_fraction,
                spread_bps=spread_bps,
            )

        intent_payload = {
            "strategy_name": self.strategy.name,
            "canonical_symbol": self.config.canonical_symbol,
            "venue_symbol": self.config.venue_symbol,
            "signal_time": signal_time,
            "direction": direction,
            "side": side.value,
            "reference_price": reference_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "requested_quantity": quantity,
            "requested_notional": requested_notional,
            "risk_fraction": self.config.risk_fraction,
            "spread_bps": spread_bps,
        }

        self.journal.reserve_intent(sid, intent_payload)
        request = ExecutionRequest(
            client_order_id=sid[:31],
            symbol=self.config.canonical_symbol,
            side=side,
            quantity=quantity,
            reference_price=reference_price,
            created_at=datetime.now(timezone.utc),
        )

        try:
            result = self.executor.execute(
                request,
                stop_loss=stop_loss,
                take_profit=take_profit,
                now=datetime.now(timezone.utc),
            )
        except Exception as exc:
            self.journal.record_error(sid, exc)
            raise

        self.journal.record_result(sid, result)
        return MT5DirectStrategyOutcome(
            status="EXECUTED_MT5_DEMO",
            reason="registered strategy signal passed direct DEMO execution gates",
            strategy_name=self.strategy.name,
            canonical_symbol=self.config.canonical_symbol,
            venue_symbol=self.config.venue_symbol,
            signal_id=sid,
            signal_time=signal_time,
            direction=direction,
            side=side.value,
            reference_price=reference_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            requested_quantity=quantity,
            requested_notional=requested_notional,
            risk_fraction=self.config.risk_fraction,
            spread_bps=spread_bps,
            order_ticket=result.order_ticket,
            deal_ticket=result.deal_ticket,
            execution_status=result.status,
        )
