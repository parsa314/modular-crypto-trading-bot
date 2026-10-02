from __future__ import annotations

from datetime import datetime, timezone

import ccxt

from ..hashing import canonical_json, stable_hash
from ..microstructure import OrderBookLevel, OrderBookSnapshot
from ..microstructure_plane import OrderBookProviderResult


class CCXTPublicOrderBookProvider:
    provider_id = "CCXT_PUBLIC_ORDER_BOOK_V59"

    def __init__(self, exchange_id: str) -> None:
        if not isinstance(exchange_id, str) or not exchange_id.strip():
            raise ValueError("exchange_id is required")
        if not hasattr(ccxt, exchange_id):
            raise ValueError(f"unknown CCXT exchange: {exchange_id}")
        exchange_cls = getattr(ccxt, exchange_id)
        self.exchange_id = exchange_id
        self.exchange = exchange_cls({"enableRateLimit": True})
        if getattr(self.exchange, "apiKey", None) or getattr(self.exchange, "secret", None):
            raise RuntimeError("credentials are forbidden in public order-book provider")

    def fetch_order_book(self, *, symbol: str, limit: int = 50) -> OrderBookProviderResult:
        if not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        if getattr(self.exchange, "apiKey", None) or getattr(self.exchange, "secret", None):
            raise RuntimeError("credentials are forbidden in public order-book provider")
        self.exchange.load_markets()
        if symbol not in self.exchange.markets:
            raise ValueError(f"{symbol} is not listed on {self.exchange_id}")
        if not self.exchange.has.get("fetchOrderBook"):
            raise RuntimeError(f"{self.exchange_id} does not advertise fetchOrderBook")

        payload = self.exchange.fetch_order_book(symbol, limit=limit)
        bids_raw = tuple((float(row[0]), float(row[1])) for row in (payload.get("bids") or [])[:limit])
        asks_raw = tuple((float(row[0]), float(row[1])) for row in (payload.get("asks") or [])[:limit])
        if not bids_raw or not asks_raw:
            raise RuntimeError("exchange returned an empty order-book side")

        timestamp_ms = payload.get("timestamp")
        observed_at = (
            datetime.fromtimestamp(float(timestamp_ms) / 1000.0, tz=timezone.utc)
            if timestamp_ms is not None
            else datetime.now(timezone.utc)
        )
        evidence = {
            "exchange": self.exchange_id,
            "symbol": symbol,
            "timestamp": observed_at.isoformat(),
            "bids": bids_raw,
            "asks": asks_raw,
            "nonce": payload.get("nonce"),
        }
        raw = canonical_json(evidence)
        snapshot = OrderBookSnapshot(
            venue=self.exchange_id,
            symbol=symbol,
            observed_at=observed_at,
            bids=tuple(OrderBookLevel(price, qty) for price, qty in bids_raw),
            asks=tuple(OrderBookLevel(price, qty) for price, qty in asks_raw),
            source=f"ccxt:{self.exchange_id}:fetch_order_book",
            source_hash=stable_hash(evidence),
        )
        return OrderBookProviderResult(
            provider_id=self.provider_id,
            snapshot=snapshot,
            raw_payload=raw,
            provider_metadata={
                "wire_raw_available": False,
                "payload_kind": "CCXT_PARSED_PUBLIC_RESPONSE",
                "limit": limit,
                "exchange_name": getattr(self.exchange, "name", self.exchange_id),
            },
        )
