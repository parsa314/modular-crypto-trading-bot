from __future__ import annotations

"""OKX Demo Trading transport for the TESTNET execution gateway.

Safety invariants:
- only demo-specific environment variable names are accepted;
- OKX sandbox mode is enabled immediately after exchange construction;
- the wrapper never exposes a method that disables sandbox mode;
- no LIVE endpoint or production credential fallback exists;
- submit network failures are treated as ambiguous and must be reconciled.
"""

from dataclasses import dataclass
from decimal import Decimal
import os
from typing import Any, Callable, Mapping

import ccxt

from .order_reconciliation_v52 import OrderLifecycle
from .testnet_execution import (
    AmbiguousTransportOutcome,
    MarketPrecision,
    TestnetOrderIntent,
    TestnetSafetyError,
    VenueOrderSnapshot,
    decimal_from,
)


_TRUTHY = {"1", "true", "yes", "on"}
_TERMINAL_REJECT_STATUSES = {"rejected", "expired"}
_CANCEL_STATUSES = {"canceled", "cancelled"}
_OPEN_STATUSES = {"open"}
_CLOSED_STATUSES = {"closed"}


@dataclass(frozen=True)
class OKXDemoCredentials:
    api_key: str
    secret: str
    passphrase: str

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ValueError("OKX demo api_key is required")
        if not self.secret.strip():
            raise ValueError("OKX demo secret is required")
        if not self.passphrase.strip():
            raise ValueError("OKX demo passphrase is required")


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in _TRUTHY


def credentials_from_env(env: Mapping[str, str] | None = None) -> OKXDemoCredentials:
    source = os.environ if env is None else env
    if not _enabled(source.get("OKX_DEMO_ENABLED")):
        raise TestnetSafetyError("OKX_DEMO_EXPLICIT_ENABLE_REQUIRED")
    return OKXDemoCredentials(
        api_key=str(source.get("OKX_DEMO_API_KEY", "")),
        secret=str(source.get("OKX_DEMO_API_SECRET", "")),
        passphrase=str(source.get("OKX_DEMO_API_PASSPHRASE", "")),
    )


def _decimal_or_zero(value: Any, name: str) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    return decimal_from(value, name)


def _status_to_lifecycle(*, status: Any, filled: Decimal, amount: Decimal) -> OrderLifecycle:
    raw = str(status or "").strip().lower()
    if not raw:
        # OKX place-order ACK payloads can omit lifecycle status while still
        # returning ordId/clOrdId. The durable client ID makes this reconcilable.
        return OrderLifecycle.PARTIAL if filled > 0 else OrderLifecycle.ACKNOWLEDGED
    if raw in _OPEN_STATUSES:
        return OrderLifecycle.PARTIAL if filled > 0 else OrderLifecycle.ACKNOWLEDGED
    if raw in _CLOSED_STATUSES:
        if amount > 0 and filled + Decimal("1e-18") >= amount:
            return OrderLifecycle.FILLED
        # A closed order with residual quantity is terminal but not fully filled.
        return OrderLifecycle.CANCELLED
    if raw in _CANCEL_STATUSES:
        return OrderLifecycle.CANCELLED
    if raw in _TERMINAL_REJECT_STATUSES:
        return OrderLifecycle.REJECTED
    raise TestnetSafetyError(f"UNSUPPORTED_OKX_ORDER_STATUS:{raw}")


def _fee_from_order(order: Mapping[str, Any]) -> tuple[Decimal, str | None]:
    fee = order.get("fee")
    if isinstance(fee, Mapping):
        cost = _decimal_or_zero(fee.get("cost"), "fee.cost")
        currency = fee.get("currency")
        if cost < 0:
            # Unified CCXT fee.cost is normally non-negative, but fail closed
            # rather than guessing whether a negative value is a rebate.
            raise TestnetSafetyError("NEGATIVE_OKX_FEE_UNSUPPORTED")
        return cost, None if currency in (None, "") else str(currency)
    return Decimal("0"), None


def snapshot_from_ccxt_order(
    order: Mapping[str, Any],
    *,
    fallback_client_order_id: str,
    fallback_requested_quantity: Decimal | None = None,
) -> VenueOrderSnapshot:
    if not isinstance(order, Mapping):
        raise TestnetSafetyError("OKX_ORDER_RESPONSE_NOT_MAPPING")

    venue_id = order.get("id")
    if venue_id in (None, ""):
        # A submit can have succeeded even when the response is truncated.
        raise AmbiguousTransportOutcome("OKX_DEMO_ACK_WITHOUT_VENUE_ORDER_ID")

    venue_client = order.get("clientOrderId")
    if venue_client not in (None, "", fallback_client_order_id):
        raise TestnetSafetyError("OKX_CLIENT_ORDER_ID_MISMATCH")
    client_order_id = fallback_client_order_id if venue_client in (None, "") else str(venue_client)

    filled = _decimal_or_zero(order.get("filled"), "filled")
    amount_raw = order.get("amount")
    if amount_raw in (None, ""):
        amount = fallback_requested_quantity or Decimal("0")
    else:
        amount = decimal_from(amount_raw, "amount")
    if filled < 0 or amount < 0 or (amount > 0 and filled > amount + Decimal("1e-18")):
        raise TestnetSafetyError("INVALID_OKX_FILL_QUANTITIES")

    average_raw = order.get("average")
    average = None
    if average_raw not in (None, "", 0, 0.0, "0"):
        average = decimal_from(average_raw, "average")
        if average <= 0:
            raise TestnetSafetyError("INVALID_OKX_AVERAGE_PRICE")

    fee_cost, fee_currency = _fee_from_order(order)
    return VenueOrderSnapshot(
        client_order_id=client_order_id,
        venue_order_id=str(venue_id),
        state=_status_to_lifecycle(status=order.get("status"), filled=filled, amount=amount),
        matched_quantity=filled,
        average_price=average,
        fee_reported=fee_cost,
        fee_currency=fee_currency,
    )


class OKXDemoTransport:
    """CCXT-backed OKX Demo transport locked to simulated trading."""

    sandbox = True
    venue = "OKX_DEMO"

    def __init__(self, exchange: Any) -> None:
        if exchange is None:
            raise ValueError("exchange is required")
        if str(getattr(exchange, "id", "")).lower() != "okx":
            raise TestnetSafetyError("OKX_EXCHANGE_REQUIRED")
        self._exchange = exchange

    @classmethod
    def from_credentials(
        cls,
        credentials: OKXDemoCredentials,
        *,
        exchange_factory: Callable[[dict[str, Any]], Any] | None = None,
        timeout_ms: int = 10_000,
    ) -> "OKXDemoTransport":
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        factory = exchange_factory or ccxt.okx
        exchange = factory(
            {
                "apiKey": credentials.api_key,
                "secret": credentials.secret,
                "password": credentials.passphrase,
                "enableRateLimit": True,
                "timeout": int(timeout_ms),
                "options": {"defaultType": "spot"},
            }
        )
        # CRITICAL: CCXT requires sandbox mode to be the first call after
        # construction. For OKX this injects x-simulated-trading: 1.
        exchange.set_sandbox_mode(True)
        return cls(exchange)

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        exchange_factory: Callable[[dict[str, Any]], Any] | None = None,
        timeout_ms: int = 10_000,
    ) -> "OKXDemoTransport":
        return cls.from_credentials(
            credentials_from_env(env),
            exchange_factory=exchange_factory,
            timeout_ms=timeout_ms,
        )

    def market_precision(self, symbol: str, *, reference_price: Decimal) -> MarketPrecision:
        if not symbol.strip():
            raise ValueError("symbol is required")
        ref = decimal_from(reference_price, "reference_price")
        if ref <= 0:
            raise ValueError("reference_price must be positive")

        markets = self._exchange.load_markets()
        market = markets.get(symbol)
        if not isinstance(market, Mapping):
            raise TestnetSafetyError(f"OKX_DEMO_MARKET_NOT_FOUND:{symbol}")

        precision = market.get("precision") or {}
        amount_precision = precision.get("amount")
        if amount_precision in (None, ""):
            raise TestnetSafetyError("OKX_DEMO_AMOUNT_PRECISION_UNAVAILABLE")

        if getattr(self._exchange, "precisionMode", None) == ccxt.TICK_SIZE:
            step = decimal_from(amount_precision, "amount_precision")
        else:
            digits = int(amount_precision)
            if digits < 0:
                raise TestnetSafetyError("INVALID_OKX_AMOUNT_PRECISION")
            step = Decimal(1).scaleb(-digits)
        if step <= 0:
            raise TestnetSafetyError("INVALID_OKX_AMOUNT_STEP")

        limits = market.get("limits") or {}
        amount_limits = limits.get("amount") or {}
        cost_limits = limits.get("cost") or {}
        min_qty_raw = amount_limits.get("min")
        min_qty = step if min_qty_raw in (None, "") else decimal_from(min_qty_raw, "min_quantity")
        if min_qty <= 0:
            min_qty = step

        min_cost_raw = cost_limits.get("min")
        if min_cost_raw in (None, ""):
            # If OKX/CCXT does not expose a separate cost floor, the effective
            # notional floor implied by min quantity at the executable reference
            # price is used. This never weakens the amount constraint.
            min_notional = min_qty * ref
        else:
            min_notional = decimal_from(min_cost_raw, "min_notional")
        if min_notional <= 0:
            min_notional = min_qty * ref

        return MarketPrecision(
            quantity_step=step,
            min_quantity=min_qty,
            min_notional=min_notional,
        )

    def private_preflight(self, symbol: str) -> dict[str, Any]:
        """Authenticate against Demo without placing, amending or cancelling an order."""
        markets = self._exchange.load_markets()
        if symbol not in markets:
            raise TestnetSafetyError(f"OKX_DEMO_MARKET_NOT_FOUND:{symbol}")
        balance = self._exchange.fetch_balance()
        return {
            "venue": self.venue,
            "sandbox": True,
            "symbol": symbol,
            "market_loaded": True,
            "authenticated_balance_response": isinstance(balance, Mapping),
        }

    def submit_market_order(self, intent: TestnetOrderIntent) -> VenueOrderSnapshot:
        params = {"clientOrderId": intent.client_order_id}
        try:
            order = self._exchange.create_order(
                intent.symbol,
                "market",
                intent.side.value.lower(),
                float(intent.quantity),
                None,
                params,
            )
        except ccxt.NetworkError as exc:
            # We cannot prove whether the request reached OKX. Never blind-retry.
            raise AmbiguousTransportOutcome("OKX_DEMO_SUBMIT_NETWORK_OUTCOME_UNKNOWN") from exc

        return snapshot_from_ccxt_order(
            order,
            fallback_client_order_id=intent.client_order_id,
            fallback_requested_quantity=intent.quantity,
        )

    def fetch_order(
        self,
        *,
        client_order_id: str,
        venue_order_id: str | None,
        symbol: str,
    ) -> VenueOrderSnapshot | None:
        try:
            if venue_order_id:
                order = self._exchange.fetch_order(venue_order_id, symbol)
            else:
                order = self._exchange.fetch_order(
                    client_order_id,
                    symbol,
                    {"clientOrderId": client_order_id},
                )
        except ccxt.OrderNotFound:
            return None
        return snapshot_from_ccxt_order(
            order,
            fallback_client_order_id=client_order_id,
        )

    def cancel_order(
        self,
        *,
        client_order_id: str,
        venue_order_id: str | None,
        symbol: str,
    ) -> VenueOrderSnapshot:
        if venue_order_id:
            order = self._exchange.cancel_order(venue_order_id, symbol)
        else:
            order = self._exchange.cancel_order(
                client_order_id,
                symbol,
                {"clientOrderId": client_order_id},
            )
        # Cancel ACK responses may omit a final status; fetch authoritative state.
        refreshed = self.fetch_order(
            client_order_id=client_order_id,
            venue_order_id=str(order.get("id") or venue_order_id or "") or None,
            symbol=symbol,
        )
        if refreshed is None:
            raise TestnetSafetyError("OKX_CANCEL_RECONCILIATION_MISSING")
        return refreshed
