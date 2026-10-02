from __future__ import annotations

from datetime import datetime, timezone
import json
import time
from typing import Any

import ccxt

from ..hashing import canonical_json
from ..market_data import MarketDataRequest, ProviderOHLCVResult, TIMEFRAME_SECONDS


class CCXTPublicOHLCVProvider:
    """Public, credential-free OHLCV adapter.

    This adapter deliberately exposes only load_markets and fetch_ohlcv. API
    keys/secrets are rejected so the Stage-2 data plane cannot gain execution
    privileges by configuration.
    """

    provider_id = "CCXT_PUBLIC_OHLCV_V59"

    def __init__(self, exchange_id: str) -> None:
        if not isinstance(exchange_id, str) or not exchange_id.strip():
            raise ValueError("exchange_id is required")
        if not hasattr(ccxt, exchange_id):
            raise ValueError(f"unknown CCXT exchange: {exchange_id}")
        self.exchange_id = exchange_id
        exchange_cls = getattr(ccxt, exchange_id)
        self.exchange = exchange_cls({"enableRateLimit": True})
        if getattr(self.exchange, "apiKey", None) or getattr(self.exchange, "secret", None):
            raise RuntimeError("V59 public data adapter must not carry exchange credentials")

    def fetch_ohlcv(self, request: MarketDataRequest) -> ProviderOHLCVResult:
        if request.exchange != self.exchange_id:
            raise ValueError("request exchange does not match provider")
        if request.market_type != "spot":
            raise ValueError("Stage-2 generic CCXT adapter currently supports spot OHLCV only")
        if getattr(self.exchange, "apiKey", None) or getattr(self.exchange, "secret", None):
            raise RuntimeError("credentials are forbidden in V59 public data adapter")

        self.exchange.load_markets()
        if request.symbol not in self.exchange.markets:
            raise ValueError(f"{request.symbol} is not listed on {self.exchange_id}")
        if not self.exchange.has.get("fetchOHLCV"):
            raise RuntimeError(f"{self.exchange_id} does not advertise fetchOHLCV")

        step_ms = TIMEFRAME_SECONDS[request.timeframe] * 1000
        since_ms = int(request.since.timestamp() * 1000)
        until_ms = int(request.until.timestamp() * 1000)
        rows: list[list[Any]] = []
        cursor = since_ms
        batch_limit = min(1000, request.limit)

        while cursor < until_ms and len(rows) < request.limit:
            batch = self.exchange.fetch_ohlcv(
                request.symbol,
                timeframe=request.timeframe,
                since=cursor,
                limit=batch_limit,
            )
            if not batch:
                break
            fresh = [row[:6] for row in batch if int(row[0]) < until_ms]
            rows.extend(fresh)
            last_ms = int(batch[-1][0])
            next_cursor = last_ms + step_ms
            if next_cursor <= cursor:
                raise RuntimeError("non-advancing OHLCV pagination cursor")
            cursor = next_cursor
            if len(batch) < batch_limit:
                break
            rate_limit = int(getattr(self.exchange, "rateLimit", 0) or 0)
            if rate_limit > 0:
                time.sleep(rate_limit / 1000.0)

        rows = rows[: request.limit]
        normalized_rows = tuple(
            (
                datetime.fromtimestamp(int(row[0]) / 1000.0, tz=timezone.utc),
                row[1], row[2], row[3], row[4], row[5],
            )
            for row in rows
        )
        evidence = {
            "provider_id": self.provider_id,
            "exchange_id": self.exchange_id,
            "symbol": request.symbol,
            "timeframe": request.timeframe,
            "market_type": request.market_type,
            "rows": rows,
        }
        markets = self.exchange.markets[request.symbol]
        metadata = {
            "exchange_name": getattr(self.exchange, "name", self.exchange_id),
            "market_id": markets.get("id"),
            "base": markets.get("base"),
            "quote": markets.get("quote"),
            "spot": bool(markets.get("spot")),
            "active": markets.get("active"),
            "precision": markets.get("precision"),
            "limits": markets.get("limits"),
            "wire_raw_available": False,
            "payload_kind": "CCXT_PARSED_PUBLIC_RESPONSE",
        }
        return ProviderOHLCVResult(
            provider_id=self.provider_id,
            exchange=self.exchange_id,
            symbol=request.symbol,
            timeframe=request.timeframe,
            market_type=request.market_type,
            received_at=datetime.now(timezone.utc),
            rows=normalized_rows,
            raw_payload=canonical_json(evidence),
            source_descriptor=f"ccxt:{self.exchange_id}:fetch_ohlcv",
            provider_metadata=metadata,
        )
