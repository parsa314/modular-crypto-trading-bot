from __future__ import annotations

"""TradingView webhook -> MT5 DEMO bridge primitives.

This module intentionally keeps TradingView transport separate from the
scientific strategy engine. It accepts only explicit BUY/SELL entry intents and
routes them through the existing fail-closed MT5DemoExecutor. Exits remain
server-side SL/TP or the bot's own execution path until a dedicated,
reconciliation-safe close-position adapter is added.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import threading
from typing import Any, Mapping

from .execution import ExecutionRequest, OrderSide
from .execution_adapters import MT5DemoExecutionResult, MT5DemoExecutor


class TradingViewPayloadError(ValueError):
    pass


class TradingViewAuthError(RuntimeError):
    pass


class TradingViewDuplicateError(RuntimeError):
    pass


@dataclass(frozen=True)
class TradingViewSignal:
    event_id: str
    canonical_symbol: str
    action: str
    quantity: float
    reference_price: float
    stop_loss: float
    take_profit: float | None
    event_time: datetime
    strategy_version: str = "TRADINGVIEW_BRIDGE"
    model_version: str = "EXTERNAL_SIGNAL"
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        required = (
            self.event_id,
            self.canonical_symbol,
            self.action,
            self.strategy_version,
            self.model_version,
        )
        if not all(str(x).strip() for x in required):
            raise TradingViewPayloadError("required TradingView signal field is empty")
        action = self.action.upper()
        if action not in {"BUY", "SELL"}:
            raise TradingViewPayloadError("action must be BUY or SELL")
        values = (
            self.quantity,
            self.reference_price,
            self.stop_loss,
        )
        if not all(math.isfinite(float(x)) and float(x) > 0.0 for x in values):
            raise TradingViewPayloadError(
                "quantity/reference_price/stop_loss must be finite and positive"
            )
        if self.take_profit is not None and (
            not math.isfinite(float(self.take_profit))
            or float(self.take_profit) <= 0.0
        ):
            raise TradingViewPayloadError("take_profit must be finite and positive")
        if self.event_time.tzinfo is None:
            raise TradingViewPayloadError("event_time must be timezone-aware")

    @property
    def side(self) -> OrderSide:
        return OrderSide(self.action.upper())

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "TradingViewSignal":
        if not isinstance(payload, Mapping):
            raise TradingViewPayloadError("webhook payload must be a JSON object")

        def req(name: str) -> Any:
            value = payload.get(name)
            if value in (None, ""):
                raise TradingViewPayloadError(f"missing field: {name}")
            return value

        raw_time = req("event_time")
        try:
            event_time = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
        except ValueError as exc:
            raise TradingViewPayloadError("event_time must be ISO-8601") from exc

        take_profit_raw = payload.get("take_profit")
        take_profit = (
            None
            if take_profit_raw in (None, "", 0, "0", 0.0)
            else float(take_profit_raw)
        )
        metadata = payload.get("metadata")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, Mapping):
            raise TradingViewPayloadError("metadata must be an object")

        return cls(
            event_id=str(req("event_id")).strip(),
            canonical_symbol=str(req("symbol")).strip(),
            action=str(req("action")).upper().strip(),
            quantity=float(req("quantity")),
            reference_price=float(req("reference_price")),
            stop_loss=float(req("stop_loss")),
            take_profit=take_profit,
            event_time=event_time.astimezone(timezone.utc),
            strategy_version=str(
                payload.get("strategy_version", "TRADINGVIEW_BRIDGE")
            ).strip(),
            model_version=str(
                payload.get("model_version", "EXTERNAL_SIGNAL")
            ).strip(),
            metadata=dict(metadata),
        )


def verify_route_token(expected: str, received: str) -> None:
    if not expected or len(expected) < 24:
        raise TradingViewAuthError(
            "configured TradingView route token must be at least 24 characters"
        )
    if not hmac.compare_digest(str(expected), str(received)):
        raise TradingViewAuthError("invalid TradingView route token")


def deterministic_tv_client_order_id(signal: TradingViewSignal) -> str:
    raw = (
        f"{signal.event_id}|{signal.canonical_symbol}|{signal.action}|"
        f"{signal.event_time.isoformat()}"
    )
    return "tv-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:25]


class TradingViewWebhookJournal:
    """Small append-only JSONL audit journal with restart duplicate detection."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                raw = line.strip()
                if not raw:
                    continue
                try:
                    item = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"invalid TradingView journal JSON at line {line_no}"
                    ) from exc
                if not isinstance(item, dict):
                    raise RuntimeError(
                        f"invalid TradingView journal row at line {line_no}"
                    )
                rows.append(item)
        return rows

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

    def accept_once(self, signal: TradingViewSignal, *, received_at: datetime) -> None:
        with self._lock:
            rows = self._read_unlocked()
            if any(
                row.get("event_id") == signal.event_id
                and row.get("record_type") == "RECEIVED"
                for row in rows
            ):
                raise TradingViewDuplicateError(signal.event_id)

            row = {
                "record_type": "RECEIVED",
                "event_id": signal.event_id,
                "received_at": received_at.astimezone(timezone.utc).isoformat(),
                "signal": {
                    **asdict(signal),
                    "event_time": signal.event_time.astimezone(
                        timezone.utc
                    ).isoformat(),
                    "metadata": dict(signal.metadata or {}),
                },
            }
            self._append_unlocked(row)

    def record_result(
        self,
        *,
        event_id: str,
        result: MT5DemoExecutionResult,
        completed_at: datetime,
    ) -> None:
        with self._lock:
            row = {
                "record_type": "EXECUTION_RESULT",
                "event_id": event_id,
                "completed_at": completed_at.astimezone(timezone.utc).isoformat(),
                "result": {
                    **asdict(result),
                    "timestamp": result.timestamp.astimezone(
                        timezone.utc
                    ).isoformat(),
                },
            }
            self._append_unlocked(row)

    def record_error(
        self,
        *,
        event_id: str,
        error: str,
        completed_at: datetime,
    ) -> None:
        with self._lock:
            self._append_unlocked(
                {
                    "record_type": "EXECUTION_ERROR",
                    "event_id": event_id,
                    "completed_at": completed_at.astimezone(timezone.utc).isoformat(),
                    "error": str(error)[:1000],
                }
            )

    def latest(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._read_unlocked()
        return rows[-max(1, int(limit)) :]


class TradingViewMT5Bridge:
    def __init__(
        self,
        *,
        executor: MT5DemoExecutor,
        journal: TradingViewWebhookJournal,
    ):
        self.executor = executor
        self.journal = journal

    def accept(
        self,
        payload: Mapping[str, Any],
        *,
        received_at: datetime | None = None,
    ) -> TradingViewSignal:
        signal = TradingViewSignal.from_payload(payload)
        now = received_at or datetime.now(timezone.utc)
        self.journal.accept_once(signal, received_at=now)
        return signal

    def execute(self, signal: TradingViewSignal) -> MT5DemoExecutionResult:
        request = ExecutionRequest(
            client_order_id=deterministic_tv_client_order_id(signal),
            symbol=signal.canonical_symbol,
            side=signal.side,
            quantity=float(signal.quantity),
            reference_price=float(signal.reference_price),
            created_at=signal.event_time,
        )
        try:
            result = self.executor.execute(
                request,
                stop_loss=float(signal.stop_loss),
                take_profit=signal.take_profit,
                now=datetime.now(timezone.utc),
            )
        except Exception as exc:
            self.journal.record_error(
                event_id=signal.event_id,
                error=f"{type(exc).__name__}: {exc}",
                completed_at=datetime.now(timezone.utc),
            )
            raise

        self.journal.record_result(
            event_id=signal.event_id,
            result=result,
            completed_at=datetime.now(timezone.utc),
        )
        return result
