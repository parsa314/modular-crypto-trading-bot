from __future__ import annotations

"""Pure reconciliation/state-machine logic for future TESTNET/private adapters.

No network calls or secrets live in this module. It exists so ambiguous submit
outcomes, partial fills, reconnects and venue-reported fees can be tested before
any private exchange execution is enabled.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import math
from decimal import Decimal, localcontext
from .financial_decimal import decimal_value
from typing import Any, Iterable


class OrderLifecycle(str, Enum):
    CREATED = "CREATED"
    SUBMITTING = "SUBMITTING"
    UNKNOWN_PENDING_RECONCILIATION = "UNKNOWN_PENDING_RECONCILIATION"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


TERMINAL_STATES = {
    OrderLifecycle.FILLED,
    OrderLifecycle.CANCELLED,
    OrderLifecycle.REJECTED,
}


@dataclass(frozen=True)
class ReconciledOrder:
    client_order_id: str
    symbol: str
    side: str
    requested_quantity: Decimal
    state: OrderLifecycle = OrderLifecycle.CREATED
    venue_order_id: str | None = None
    matched_quantity: Decimal = Decimal("0")
    average_price: Decimal | None = None
    fee_reported: Decimal = Decimal("0")
    fee_currency: str | None = None
    updated_at: datetime = datetime.min.replace(tzinfo=timezone.utc)
    state_version: int = 0

    def __post_init__(self) -> None:
        for name in ("requested_quantity", "matched_quantity", "fee_reported"):
            object.__setattr__(self, name, decimal_value(getattr(self, name), name))
        if self.average_price is not None:
            object.__setattr__(self, "average_price", decimal_value(self.average_price, "average_price"))
        if self.requested_quantity <= 0 or not 0 <= self.matched_quantity <= self.requested_quantity or self.fee_reported < 0:
            raise ValueError("invalid reconciled quantities / fee")
        if self.state is OrderLifecycle.FILLED and self.matched_quantity != self.requested_quantity:
            raise ValueError("FILLED requires exact requested quantity")
        if self.state_version < 0:
            raise ValueError("invalid state version")



@dataclass(frozen=True)
class RealizedCostBreakdown:
    filled_quantity: Decimal
    vwap: Decimal | None
    gross_quote_notional: Decimal
    explicit_fee_reported: Decimal
    explicit_fee_currency: str | None
    explicit_fee_quote: Decimal | None
    implementation_shortfall_quote: Decimal | None
    total_cost_quote: Decimal | None
    complete_for_quote_cost: bool


def deterministic_client_order_id(
    *,
    strategy_version: str,
    symbol: str,
    decision_epoch: int,
    side: str,
    max_length: int = 32,
) -> str:
    """Stable <=32-char identity suitable for Nobitex/CoinEx client IDs."""
    raw = f"{strategy_version}|{symbol}|{int(decision_epoch)}|{side.upper()}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    prefix = "mbot-"
    result = f"{prefix}{digest}"
    if len(result) > max_length:
        result = result[:max_length]
    return result


def mark_submit_started(order: ReconciledOrder, *, now: datetime | None = None) -> ReconciledOrder:
    if order.state is not OrderLifecycle.CREATED:
        raise ValueError("submit may start only from CREATED")
    return replace(order, state=OrderLifecycle.SUBMITTING, updated_at=now or datetime.now(timezone.utc))


def mark_submit_ambiguous(order: ReconciledOrder, *, now: datetime | None = None) -> ReconciledOrder:
    if order.state is not OrderLifecycle.SUBMITTING:
        raise ValueError("ambiguous submit requires SUBMITTING state")
    return replace(
        order,
        state=OrderLifecycle.UNKNOWN_PENDING_RECONCILIATION,
        updated_at=now or datetime.now(timezone.utc),
    )


def retry_submission_allowed(order: ReconciledOrder) -> bool:
    """Fail closed: never blind-retry an ambiguous/private submit."""
    return order.state is OrderLifecycle.CREATED


def _finite_nonnegative(value: Any, name: str) -> float:
    x = float(value)
    if not math.isfinite(x) or x < 0.0:
        raise ValueError(f"invalid {name}")
    return x


def reconcile_nobitex_order(
    local: ReconciledOrder,
    venue_order: dict[str, Any],
    *,
    now: datetime | None = None,
) -> ReconciledOrder:
    """Reconcile one Nobitex order/status/list object into the local state.

    Nobitex documentation currently describes clientOrderId as experimental.
    The adapter must therefore persist both clientOrderId and numeric order id.
    """
    if not isinstance(venue_order, dict):
        raise ValueError("venue order must be an object")
    venue_client = venue_order.get("clientOrderId")
    if venue_client not in (None, "", local.client_order_id):
        raise ValueError("clientOrderId mismatch")

    venue_id = venue_order.get("id")
    if local.venue_order_id and venue_id is not None and str(venue_id) != str(local.venue_order_id):
        raise ValueError("venue order id mismatch")

    amount = decimal_value(venue_order.get("amount", local.requested_quantity), "amount")
    matched = decimal_value(venue_order.get("matchedAmount", "0"), "matchedAmount")
    if amount <= 0 or not local.matched_quantity <= matched <= amount:
        raise ValueError("invalid fill quantities")
    if amount != local.requested_quantity:
        raise ValueError("requested quantity mismatch")

    raw_status = str(venue_order.get("status", "")).strip().lower()
    if raw_status in {"new", "inactive"}:
        state = OrderLifecycle.ACKNOWLEDGED
    elif raw_status == "active":
        state = OrderLifecycle.PARTIAL if matched > 0.0 else OrderLifecycle.ACKNOWLEDGED
    elif raw_status == "done":
        if matched != amount:
            raise ValueError("Done order is not fully matched")
        state = OrderLifecycle.FILLED
    elif raw_status == "canceled":
        state = OrderLifecycle.CANCELLED
    elif raw_status in {"rejected", "failed"}:
        state = OrderLifecycle.REJECTED
    else:
        raise ValueError(f"unsupported Nobitex order status: {raw_status!r}")

    avg_raw = venue_order.get("averagePrice")
    avg_price = None
    if avg_raw not in (None, "", "0", 0, 0.0):
        avg_price = decimal_value(avg_raw, "averagePrice")
        if avg_price <= 0.0:
            avg_price = None
    if matched > 0.0 and avg_price is None:
        # Status/list evidence is insufficient to price the fill; user trades
        # must be queried before economic reconciliation is considered complete.
        avg_price = local.average_price

    fee = decimal_value(venue_order.get("fee", local.fee_reported), "fee")
    if local.state in TERMINAL_STATES and state != local.state:
        raise ValueError("terminal state regression")
    if fee < local.fee_reported:
        raise ValueError("cumulative fee regression")
    return ReconciledOrder(
        client_order_id=local.client_order_id,
        symbol=local.symbol,
        side=local.side,
        requested_quantity=local.requested_quantity,
        state=state,
        venue_order_id=str(venue_id) if venue_id is not None else local.venue_order_id,
        matched_quantity=matched,
        average_price=avg_price,
        fee_reported=fee,
        # Nobitex Order/Trade examples expose a fee value but the cited object
        # schema does not make fee denomination explicit. Do not guess it.
        fee_currency=local.fee_currency,
        updated_at=now or datetime.now(timezone.utc),
        state_version=local.state_version,
    )


def realized_cost_from_trades(
    trades: Iterable[dict[str, Any]], *, side: str,
    decision_reference_price: Decimal | str | float,
    fee_currency: str | None = None,
    fee_to_quote_rate: Decimal | str | float | None = None,
) -> RealizedCostBreakdown:
    """Exact trade-ledger economics. Unknown fee FX stays unresolved."""
    ref = decimal_value(decision_reference_price, "reference_price")
    if ref <= 0 or side.lower() not in {"buy", "sell"}:
        raise ValueError("invalid side / reference")
    with localcontext() as ctx:
        ctx.prec = 120
        qty, gross, fees = Decimal(0), Decimal(0), Decimal(0)
        for trade in trades:
            amount = decimal_value(trade.get("amount", "0"), "amount")
            price = decimal_value(trade.get("price", "0"), "price")
            fee = decimal_value(trade.get("fee", "0"), "fee")
            if amount <= 0 or price <= 0 or fee < 0:
                raise ValueError("invalid trade economics")
            qty += amount
            gross += amount * price
            fees += fee
        if qty == 0:
            return RealizedCostBreakdown(qty,None,gross,fees,fee_currency,None,None,None,False)
        vwap = gross/qty
        shortfall = (Decimal(1) if side.lower()=="buy" else Decimal(-1))*(gross-ref*qty)
        quote_fee = Decimal(0) if fees==0 else None
        if fee_to_quote_rate is not None:
            rate=decimal_value(fee_to_quote_rate,"fee FX")
            if rate<=0: raise ValueError("invalid fee FX")
            quote_fee=fees*rate
        return RealizedCostBreakdown(qty,vwap,gross,fees,fee_currency,quote_fee,shortfall,
            None if quote_fee is None else shortfall+quote_fee,quote_fee is not None)
