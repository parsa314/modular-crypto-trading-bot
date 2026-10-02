from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .hashing import canonical_json
from .market_data import MarketDataRequest, ProviderOHLCVResult


class DeterministicFixtureProvider:
    provider_id = "V59_DETERMINISTIC_FIXTURE"

    def fetch_ohlcv(self, request: MarketDataRequest) -> ProviderOHLCVResult:
        step_seconds = 60
        rows = []
        cursor = request.since
        price = 100.0
        while cursor < request.until and len(rows) < request.limit:
            rows.append((cursor, price, price + 1.0, price - 1.0, price + 0.25, 10.0))
            cursor += timedelta(seconds=step_seconds)
            price += 0.5
        evidence = {
            "provider_id": self.provider_id,
            "exchange": request.exchange,
            "symbol": request.symbol,
            "timeframe": request.timeframe,
            "rows": [
                [row[0].astimezone(timezone.utc).isoformat(), *row[1:]]
                for row in rows
            ],
        }
        return ProviderOHLCVResult(
            provider_id=self.provider_id,
            exchange=request.exchange,
            symbol=request.symbol,
            timeframe=request.timeframe,
            market_type=request.market_type,
            received_at=request.as_of,
            rows=tuple(rows),
            raw_payload=canonical_json(evidence),
            source_descriptor="deterministic-fixture",
            provider_metadata={"wire_raw_available": True, "fixture": True},
        )


def fixture_request() -> MarketDataRequest:
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    return MarketDataRequest(
        exchange="fixture",
        symbol="BTC/USDT",
        timeframe="1m",
        market_type="spot",
        since=start,
        until=start + timedelta(minutes=30),
        as_of=start + timedelta(minutes=30),
        limit=30,
    )
