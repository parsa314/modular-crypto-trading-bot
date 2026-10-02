from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .hashing import canonical_json
from .market_data import MarketDataRequest, ProviderOHLCVResult
from .microstructure import OrderBookLevel, OrderBookSnapshot, PITMetric
from .microstructure_plane import MetricProviderResult, OrderBookProviderResult
from .hashing import stable_hash


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


class DeterministicOrderBookProvider:
    provider_id = "V59_DETERMINISTIC_ORDERBOOK"

    def fetch_order_book(self, *, symbol: str, limit: int) -> OrderBookProviderResult:
        observed = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        snapshot = OrderBookSnapshot(
            venue="fixture",
            symbol=symbol,
            observed_at=observed,
            bids=(
                OrderBookLevel(99.99, 10.0),
                OrderBookLevel(99.95, 20.0),
                OrderBookLevel(99.80, 30.0),
            ),
            asks=(
                OrderBookLevel(100.01, 11.0),
                OrderBookLevel(100.05, 21.0),
                OrderBookLevel(100.20, 31.0),
            ),
            source="deterministic-orderbook-fixture",
            source_hash=stable_hash({"fixture": "orderbook-v59"}),
        )
        raw = canonical_json({
            "symbol": symbol,
            "limit": limit,
            "bids": [(x.price, x.quantity) for x in snapshot.bids],
            "asks": [(x.price, x.quantity) for x in snapshot.asks],
        })
        return OrderBookProviderResult(
            provider_id=self.provider_id,
            snapshot=snapshot,
            raw_payload=raw,
            provider_metadata={"fixture": True},
        )


class DeterministicDerivativeMetricProvider:
    provider_id = "V59_DETERMINISTIC_DERIVATIVES"

    def fetch_metrics(self, *, symbol: str) -> MetricProviderResult:
        observed = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        entity = f"fixture:{symbol.replace('/', '').upper()}"
        source_hash = stable_hash({"fixture": "derivatives-v59"})
        metrics = (
            PITMetric(
                entity=entity,
                metric="open_interest_volume",
                effective_at=observed,
                available_at=observed,
                observed_at=observed,
                value=12345.0,
                source="deterministic-derivative-fixture",
                source_hash=source_hash,
            ),
            PITMetric(
                entity=entity,
                metric="taker_fee_rate",
                effective_at=observed,
                available_at=observed,
                observed_at=observed,
                value=0.0005,
                source="deterministic-derivative-fixture",
                source_hash=source_hash,
            ),
        )
        return MetricProviderResult(
            provider_id=self.provider_id,
            metrics=metrics,
            raw_payload=canonical_json({"symbol": symbol, "metrics": [m.metric for m in metrics]}),
            provider_metadata={"availability_policy": "FIXED_FIXTURE"},
        )
