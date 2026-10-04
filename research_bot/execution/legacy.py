from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import math

from ..contracts import ExecutionMode


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


@dataclass(frozen=True)
class ExecutionPolicy:
    mode: ExecutionMode = ExecutionMode.PAPER
    fee_bps: float = 10.0
    slippage_bps: float = 2.0
    max_order_notional: float = 5_000.0
    live_execution_enabled: bool = False

    def __post_init__(self) -> None:
        numeric = (self.fee_bps, self.slippage_bps, self.max_order_notional)
        if not all(math.isfinite(float(value)) for value in numeric):
            raise ValueError("execution-policy values must be finite")
        if self.fee_bps < 0.0 or self.slippage_bps < 0.0:
            raise ValueError("fee_bps and slippage_bps must be non-negative")
        if self.max_order_notional <= 0.0:
            raise ValueError("max_order_notional must be positive")

    def assert_safe(self) -> None:
        if self.mode is ExecutionMode.LIVE and not self.live_execution_enabled:
            raise RuntimeError(
                "LIVE execution is disabled. Complete paper/testnet validation and a live-readiness audit first."
            )


@dataclass(frozen=True)
class ExecutionRequest:
    client_order_id: str
    symbol: str
    side: OrderSide
    quantity: float
    reference_price: float
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    created_at: datetime = datetime.min.replace(tzinfo=timezone.utc)

    @property
    def notional(self) -> float:
        return float(self.quantity * self.reference_price)


@dataclass(frozen=True)
class ExecutionFill:
    client_order_id: str
    symbol: str
    side: OrderSide
    requested_quantity: float
    filled_quantity: float
    fill_price: float
    fee_paid: float
    slippage_paid: float
    mode: ExecutionMode
    status: str
    timestamp: datetime


class PaperExecutionEngine:
    """Deterministic paper/testnet-like execution simulator.

    Signal generation and execution remain separate.  This engine models fee,
    slippage, partial fills and duplicate-order protection.  It intentionally
    contains no exchange-secret handling and no live-order method.
    """

    def __init__(self, policy: ExecutionPolicy | None = None):
        self.policy = policy or ExecutionPolicy()
        self.policy.assert_safe()
        if self.policy.mode not in {ExecutionMode.PAPER, ExecutionMode.BACKTEST, ExecutionMode.TESTNET}:
            raise RuntimeError("PaperExecutionEngine cannot run in LIVE mode")
        self._seen_ids: set[str] = set()

    def execute(
        self,
        request: ExecutionRequest,
        *,
        fill_fraction: float = 1.0,
        extra_slippage_bps: float = 0.0,
    ) -> ExecutionFill:
        if request.client_order_id in self._seen_ids:
            raise RuntimeError(f"duplicate client_order_id: {request.client_order_id}")
        if not request.client_order_id.strip() or not request.symbol.strip():
            raise ValueError("client_order_id and symbol are required")
        if not math.isfinite(float(request.quantity)) or not math.isfinite(float(request.reference_price)):
            raise ValueError("quantity and reference_price must be finite")
        if request.quantity <= 0 or request.reference_price <= 0:
            raise ValueError("quantity and reference_price must be positive")
        if request.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        if request.order_type is OrderType.LIMIT:
            raise RuntimeError("LIMIT_ORDER_SIMULATION_UNSUPPORTED")
        if request.notional > self.policy.max_order_notional:
            raise RuntimeError("MAX_ORDER_NOTIONAL_BREACH")
        if not math.isfinite(float(fill_fraction)) or not 0.0 <= fill_fraction <= 1.0:
            raise ValueError("fill_fraction must be in [0, 1]")
        if not math.isfinite(float(extra_slippage_bps)):
            raise ValueError("extra_slippage_bps must be finite")

        self._seen_ids.add(request.client_order_id)
        filled_quantity = request.quantity * fill_fraction
        total_slippage_bps = max(0.0, self.policy.slippage_bps + extra_slippage_bps)
        direction = 1.0 if request.side is OrderSide.BUY else -1.0
        fill_price = request.reference_price * (1.0 + direction * total_slippage_bps / 10_000.0)
        filled_notional = filled_quantity * fill_price
        fee_paid = filled_notional * self.policy.fee_bps / 10_000.0
        slippage_paid = abs(fill_price - request.reference_price) * filled_quantity

        if fill_fraction == 0:
            status = "REJECTED_NO_LIQUIDITY"
        elif fill_fraction < 1:
            status = "PARTIALLY_FILLED"
        else:
            status = "FILLED"

        return ExecutionFill(
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            requested_quantity=float(request.quantity),
            filled_quantity=float(filled_quantity),
            fill_price=float(fill_price),
            fee_paid=float(fee_paid),
            slippage_paid=float(slippage_paid),
            mode=self.policy.mode,
            status=status,
            timestamp=request.created_at,
        )

    def release_unsettled(self, client_order_id: str) -> None:
        """Release a simulated fill only after persistence proves no commit occurred."""
        if self.policy.mode is ExecutionMode.LIVE:
            raise RuntimeError("release_unsettled is forbidden in LIVE mode")
        self._seen_ids.discard(str(client_order_id))
