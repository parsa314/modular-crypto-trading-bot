from __future__ import annotations

"""Nobitex TESTNET transport for the TESTNET execution gateway.

This module is testnet-only by construction:
- fixed host: https://testnetapi.nobitex.ir
- explicit NOBITEX_TESTNET_ENABLED=true required
- only NOBITEX_TESTNET_TOKEN is accepted
- no production host/token fallback exists
- ambiguous network outcomes are reconciled, never blindly retried
"""

from dataclasses import dataclass
from decimal import Decimal
import json
import os
import socket
from typing import Any, Mapping, Protocol
from urllib import error as urlerror
from urllib import request as urlrequest

from .execution import OrderSide
from .order_reconciliation_v52 import OrderLifecycle, ReconciledOrder, reconcile_nobitex_order
from .testnet_execution import (
    AmbiguousTransportOutcome,
    MarketPrecision,
    TestnetOrderIntent,
    TestnetSafetyError,
    VenueOrderSnapshot,
    decimal_from,
)

TESTNET_API_BASE = "https://testnetapi.nobitex.ir"
_TRUTHY = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class NobitexTestnetCredentials:
    token: str

    def __post_init__(self) -> None:
        if not self.token.strip():
            raise ValueError("Nobitex testnet token is required")


class JSONHTTPClient(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
        timeout_seconds: float = 10.0,
    ) -> dict[str, Any]: ...


class UrllibJSONClient:
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
        timeout_seconds: float = 10.0,
    ) -> dict[str, Any]:
        payload = None
        req_headers = {"Accept": "application/json", **dict(headers or {})}
        if body is not None:
            payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
            req_headers["Content-Type"] = "application/json"
        req = urlrequest.Request(url, data=payload, headers=req_headers, method=method.upper())
        with urlrequest.urlopen(req, timeout=float(timeout_seconds)) as response:
            raw = response.read().decode("utf-8")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise TestnetSafetyError("NOBITEX_RESPONSE_NOT_OBJECT")
        return parsed


def credentials_from_env(env: Mapping[str, str] | None = None) -> NobitexTestnetCredentials:
    source = os.environ if env is None else env
    if str(source.get("NOBITEX_TESTNET_ENABLED", "")).strip().lower() not in _TRUTHY:
        raise TestnetSafetyError("NOBITEX_TESTNET_EXPLICIT_ENABLE_REQUIRED")
    return NobitexTestnetCredentials(token=str(source.get("NOBITEX_TESTNET_TOKEN", "")))


def _split_symbol(symbol: str) -> tuple[str, str]:
    parts = [part.strip().lower() for part in str(symbol).split("/")]
    if len(parts) != 2 or not all(parts):
        raise ValueError("symbol must look like BTC/USDT")
    return parts[0], parts[1]


def _market_symbol(symbol: str) -> str:
    src, dst = _split_symbol(symbol)
    return f"{src}{dst}".upper()


def _success(payload: Mapping[str, Any]) -> bool:
    return str(payload.get("status", "")).strip().lower() == "ok"


def snapshot_from_nobitex_order(
    order: Mapping[str, Any],
    *,
    fallback_client_order_id: str,
    requested_quantity: Decimal,
    symbol: str,
    side: OrderSide,
) -> VenueOrderSnapshot:
    local = ReconciledOrder(
        client_order_id=fallback_client_order_id,
        symbol=symbol,
        side=side.value.lower(),
        requested_quantity=float(requested_quantity),
        state=OrderLifecycle.SUBMITTING,
    )
    reconciled = reconcile_nobitex_order(local, dict(order))
    if not reconciled.venue_order_id:
        raise AmbiguousTransportOutcome("NOBITEX_TESTNET_ACK_WITHOUT_ORDER_ID")
    return VenueOrderSnapshot(
        client_order_id=reconciled.client_order_id,
        venue_order_id=reconciled.venue_order_id,
        state=reconciled.state,
        matched_quantity=Decimal(str(reconciled.matched_quantity)),
        average_price=None if reconciled.average_price is None else Decimal(str(reconciled.average_price)),
        fee_reported=Decimal(str(reconciled.fee_reported)),
        fee_currency=reconciled.fee_currency,
    )


class NobitexTestnetTransport:
    sandbox = True
    venue = "NOBITEX_TESTNET"
    base_url = TESTNET_API_BASE

    def __init__(
        self,
        credentials: NobitexTestnetCredentials,
        *,
        http_client: JSONHTTPClient | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.credentials = credentials
        self.http = http_client or UrllibJSONClient()
        self.timeout_seconds = float(timeout_seconds)

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        http_client: JSONHTTPClient | None = None,
        timeout_seconds: float = 10.0,
    ) -> "NobitexTestnetTransport":
        return cls(
            credentials_from_env(env),
            http_client=http_client,
            timeout_seconds=timeout_seconds,
        )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Token {self.credentials.token}"}

    def _request(
        self,
        method: str,
        path: str,
        *,
        private: bool,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not path.startswith("/"):
            raise ValueError("path must start with /")
        url = f"{TESTNET_API_BASE}{path}"
        headers = self._headers() if private else {}
        return self.http.request(
            method,
            url,
            headers=headers,
            body=body,
            timeout_seconds=self.timeout_seconds,
        )

    def public_reference_price(self, symbol: str) -> Decimal:
        payload = self._request("GET", f"/v3/orderbook/{_market_symbol(symbol)}", private=False)
        if not _success(payload):
            raise TestnetSafetyError(f"NOBITEX_ORDERBOOK_FAILED:{payload.get('code', 'UNKNOWN')}")
        raw = payload.get("lastTradePrice")
        if raw in (None, "", 0, 0.0, "0"):
            asks = payload.get("asks") or []
            bids = payload.get("bids") or []
            if asks and bids:
                ask = decimal_from(asks[0][0], "best_ask")
                bid = decimal_from(bids[0][0], "best_bid")
                if ask > 0 and bid > 0:
                    return (ask + bid) / Decimal("2")
            raise TestnetSafetyError("NOBITEX_REFERENCE_PRICE_UNAVAILABLE")
        px = decimal_from(raw, "lastTradePrice")
        if px <= 0:
            raise TestnetSafetyError("NOBITEX_REFERENCE_PRICE_INVALID")
        return px

    def market_precision(self, symbol: str, *, reference_price: Decimal) -> MarketPrecision:
        ref = decimal_from(reference_price, "reference_price")
        if ref <= 0:
            raise ValueError("reference_price must be positive")
        payload = self._request("GET", "/v2/options", private=False)
        if not _success(payload):
            raise TestnetSafetyError(f"NOBITEX_OPTIONS_FAILED:{payload.get('code', 'UNKNOWN')}")
        settings = payload.get("nobitex")
        if not isinstance(settings, Mapping):
            settings = payload

        market = _market_symbol(symbol)
        amount_precisions = settings.get("amountPrecisions") or {}
        step_raw = amount_precisions.get(market)
        if step_raw in (None, ""):
            raise TestnetSafetyError(f"NOBITEX_AMOUNT_PRECISION_UNAVAILABLE:{market}")
        step = decimal_from(step_raw, "amountPrecision")
        if step <= 0:
            raise TestnetSafetyError("NOBITEX_AMOUNT_PRECISION_INVALID")

        _, dst = _split_symbol(symbol)
        min_orders = settings.get("minOrders") or {}
        min_notional_raw = min_orders.get(dst)
        if min_notional_raw in (None, ""):
            raise TestnetSafetyError(f"NOBITEX_MIN_ORDER_UNAVAILABLE:{dst}")
        min_notional = decimal_from(min_notional_raw, "minOrder")
        if min_notional <= 0:
            raise TestnetSafetyError("NOBITEX_MIN_ORDER_INVALID")

        return MarketPrecision(
            quantity_step=step,
            min_quantity=step,
            min_notional=min_notional,
        )

    def private_preflight(self, symbol: str) -> dict[str, Any]:
        reference_price = self.public_reference_price(symbol)
        precision = self.market_precision(symbol, reference_price=reference_price)
        wallets = self._request("GET", "/users/wallets/list", private=True)
        if not _success(wallets):
            raise TestnetSafetyError(f"NOBITEX_TESTNET_AUTH_FAILED:{wallets.get('code', 'UNKNOWN')}")
        wallet_rows = wallets.get("wallets")
        return {
            "venue": self.venue,
            "sandbox": True,
            "base_url": TESTNET_API_BASE,
            "symbol": symbol,
            "authenticated": True,
            "wallet_count": len(wallet_rows) if isinstance(wallet_rows, list) else None,
            "reference_price": str(reference_price),
            "quantity_step": str(precision.quantity_step),
            "min_quantity": str(precision.min_quantity),
            "min_notional": str(precision.min_notional),
        }

    def submit_market_order(self, intent: TestnetOrderIntent) -> VenueOrderSnapshot:
        src, dst = _split_symbol(intent.symbol)
        body = {
            "type": intent.side.value.lower(),
            "execution": "market",
            "srcCurrency": src,
            "dstCurrency": dst,
            "amount": str(intent.quantity),
            # Nobitex explicitly recommends supplying an expected market price
            # even for market orders to constrain adverse execution.
            "price": str(intent.reference_price),
            "clientOrderId": intent.client_order_id,
        }
        try:
            payload = self._request("POST", "/market/orders/add", private=True, body=body)
        except (TimeoutError, socket.timeout, urlerror.URLError, ConnectionError, OSError) as exc:
            raise AmbiguousTransportOutcome("NOBITEX_TESTNET_SUBMIT_NETWORK_OUTCOME_UNKNOWN") from exc

        if not _success(payload):
            raise TestnetSafetyError(
                f"NOBITEX_TESTNET_ORDER_REJECTED:{payload.get('code', 'UNKNOWN')}"
            )
        order = payload.get("order")
        if not isinstance(order, Mapping):
            raise AmbiguousTransportOutcome("NOBITEX_TESTNET_ACK_WITHOUT_ORDER_OBJECT")
        return snapshot_from_nobitex_order(
            order,
            fallback_client_order_id=intent.client_order_id,
            requested_quantity=intent.quantity,
            symbol=intent.symbol,
            side=intent.side,
        )

    def fetch_order(
        self,
        *,
        client_order_id: str,
        venue_order_id: str | None,
        symbol: str,
    ) -> VenueOrderSnapshot | None:
        body: dict[str, Any]
        if venue_order_id:
            body = {"id": venue_order_id}
        else:
            body = {"clientOrderId": client_order_id}

        payload = self._request("POST", "/market/orders/status", private=True, body=body)
        if not _success(payload):
            code = str(payload.get("code", "UNKNOWN"))
            if code in {"OrderNotFound", "NotFound", "InvalidOrder"}:
                return None
            raise TestnetSafetyError(f"NOBITEX_TESTNET_STATUS_FAILED:{code}")
        order = payload.get("order")
        if not isinstance(order, Mapping):
            return None

        amount = decimal_from(order.get("amount", "0"), "amount")
        order_type = str(order.get("type", "")).strip().lower()
        side = OrderSide.BUY if order_type == "buy" else OrderSide.SELL
        return snapshot_from_nobitex_order(
            order,
            fallback_client_order_id=client_order_id,
            requested_quantity=amount,
            symbol=symbol,
            side=side,
        )

    def cancel_order(
        self,
        *,
        client_order_id: str,
        venue_order_id: str | None,
        symbol: str,
    ) -> VenueOrderSnapshot:
        body: dict[str, Any] = {"status": "canceled"}
        if venue_order_id:
            body["order"] = venue_order_id
        else:
            body["clientOrderId"] = client_order_id
        payload = self._request("POST", "/market/orders/update-status", private=True, body=body)
        if not _success(payload):
            raise TestnetSafetyError(
                f"NOBITEX_TESTNET_CANCEL_FAILED:{payload.get('code', 'UNKNOWN')}"
            )
        refreshed = self.fetch_order(
            client_order_id=client_order_id,
            venue_order_id=venue_order_id,
            symbol=symbol,
        )
        if refreshed is None:
            raise TestnetSafetyError("NOBITEX_TESTNET_CANCEL_RECONCILIATION_MISSING")
        return refreshed
