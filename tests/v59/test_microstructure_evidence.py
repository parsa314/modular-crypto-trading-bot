from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from research_bot.v59.artifacts import ImmutableArtifactStore
from research_bot.v59.hashing import canonical_json, stable_hash
from research_bot.v59.microstructure import (
    MicrostructureEvidence,
    OrderBookLevel,
    OrderBookSnapshot,
    PITMetric,
)
from research_bot.v59.microstructure_plane import (
    MetricProviderResult,
    OrderBookProviderResult,
    ingest_metrics,
    ingest_order_book,
    metric_feature_snapshot,
)


UTC = timezone.utc
NOW = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


def book(**changes):
    base = dict(
        venue="fixture",
        symbol="BTC/USDT",
        observed_at=NOW,
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
        source="fixture-book",
        source_hash=stable_hash({"book": 4}),
    )
    base.update(changes)
    return OrderBookSnapshot(**base)


def metric(**changes):
    base = dict(
        entity="coinex:BTCUSDT",
        metric="open_interest_volume",
        effective_at=NOW,
        available_at=NOW,
        observed_at=NOW,
        value=12345.0,
        source="fixture-metric",
        source_hash=stable_hash({"metric": 4}),
        confidence=1.0,
    )
    base.update(changes)
    return PITMetric(**base)


def test_order_book_requires_sorted_uncrossed_sides():
    with pytest.raises(ValueError, match="descending"):
        book(bids=(OrderBookLevel(99.95, 1), OrderBookLevel(99.99, 1)))
    with pytest.raises(ValueError, match="crossed"):
        book(
            bids=(OrderBookLevel(100.02, 1),),
            asks=(OrderBookLevel(100.01, 1),),
        )


def test_depth_and_spread_are_deterministic():
    snapshot = book()
    assert snapshot.best_bid == pytest.approx(99.99)
    assert snapshot.best_ask == pytest.approx(100.01)
    assert snapshot.spread_bps == pytest.approx(2.0, abs=0.01)
    assert snapshot.side_depth_notional(side="bid", band_bps=10) > 0
    assert snapshot.side_depth_notional(side="ask", band_bps=10) > 0
    assert snapshot.snapshot_hash == snapshot.snapshot_hash


def test_order_book_to_execution_liquidity_uses_conservative_two_side_depth():
    snapshot = book()
    liquidity = snapshot.to_liquidity_snapshot(decision_at=NOW)
    bid_depth = snapshot.side_depth_notional(side="bid")
    ask_depth = snapshot.side_depth_notional(side="ask")
    assert liquidity.depth_notional_10bps == pytest.approx(min(bid_depth, ask_depth))
    assert liquidity.bid == snapshot.best_bid
    assert liquidity.ask == snapshot.best_ask


def test_future_and_stale_orderbook_fail_closed():
    with pytest.raises(ValueError, match="future"):
        book(observed_at=NOW + timedelta(seconds=1)).validate_for(NOW)
    with pytest.raises(ValueError, match="stale"):
        book(observed_at=NOW - timedelta(seconds=31)).validate_for(NOW, max_age_seconds=30)


def test_pit_metric_cannot_be_backfilled_into_earlier_decision():
    m = metric(
        effective_at=NOW - timedelta(hours=1),
        available_at=NOW,
        observed_at=NOW,
    )
    with pytest.raises(ValueError, match="availability"):
        m.validate_for(NOW - timedelta(seconds=1))
    m.validate_for(NOW)


def test_pit_metric_rejects_claimed_availability_after_observation():
    with pytest.raises(ValueError):
        metric(available_at=NOW + timedelta(seconds=1))


def test_microstructure_evidence_reports_missing_orderbook_explicitly():
    evidence = MicrostructureEvidence(order_book=None, metrics={"oi": metric()})
    features = evidence.features_for(NOW)
    assert features["order_book"] == "UNAVAILABLE_DATA"
    assert features["oi"] == pytest.approx(12345.0)


class BookProvider:
    provider_id = "FIXTURE_BOOK"

    def fetch_order_book(self, *, symbol: str, limit: int):
        snapshot = book(symbol=symbol)
        payload = canonical_json(
            {
                "symbol": symbol,
                "limit": limit,
                "bids": [(x.price, x.quantity) for x in snapshot.bids],
                "asks": [(x.price, x.quantity) for x in snapshot.asks],
            }
        )
        return OrderBookProviderResult(
            provider_id=self.provider_id,
            snapshot=snapshot,
            raw_payload=payload,
            provider_metadata={"fixture": True},
        )


class MetricProvider:
    provider_id = "FIXTURE_METRICS"

    def fetch_metrics(self, *, symbol: str):
        entity = f"fixture:{symbol.replace('/', '')}"
        m1 = metric(entity=entity)
        m2 = metric(
            entity=entity,
            metric="taker_fee_rate",
            value=0.0005,
            source_hash=stable_hash({"metric": "fee"}),
        )
        payload = canonical_json({"symbol": symbol, "metrics": [m1.metric, m2.metric]})
        return MetricProviderResult(
            provider_id=self.provider_id,
            metrics=(m1, m2),
            raw_payload=payload,
            provider_metadata={"availability_policy": "OBSERVED_NOW_ONLY"},
        )


def test_orderbook_ingestion_is_create_only_and_hash_bound(tmp_path):
    store = ImmutableArtifactStore(tmp_path)
    bundle = ingest_order_book(
        provider=BookProvider(),
        symbol="BTC/USDT",
        limit=50,
        store=store,
        run_id="book-1",
    )
    assert bundle.snapshot_hash == book().snapshot_hash
    assert (tmp_path / "book-1/raw/order_book_payload.bin").exists()
    assert (tmp_path / "book-1/order_book_manifest.json").exists()
    with pytest.raises(FileExistsError):
        ingest_order_book(
            provider=BookProvider(),
            symbol="BTC/USDT",
            limit=50,
            store=store,
            run_id="book-1",
        )


def test_prospective_metric_ingestion_preserves_availability(tmp_path):
    store = ImmutableArtifactStore(tmp_path)
    summary = ingest_metrics(
        provider=MetricProvider(),
        symbol="BTC/USDT",
        store=store,
        run_id="metrics-1",
    )
    assert summary["metric_count"] == 2
    assert (tmp_path / "metrics-1/raw/metric_payload.bin").exists()
    assert (tmp_path / "metrics-1/metric_manifest.json").exists()


def test_metric_feature_snapshot_is_pit_and_hash_bound():
    rows = [metric(), metric(metric="taker_fee_rate", value=0.0005)]
    snapshot = metric_feature_snapshot(rows, decision_at=NOW)
    assert snapshot["open_interest_volume"]["value"] == 12345.0
    assert snapshot["taker_fee_rate"]["value"] == pytest.approx(0.0005)
    assert len(snapshot["feature_snapshot_hash"]) == 64
