from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Mapping

from .execution import LiquiditySnapshot
from .hashing import stable_hash


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be a canonical nonempty string")
    return value


def _positive(value: float, name: str) -> float:
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


@dataclass(frozen=True)
class OrderBookLevel:
    price: float
    quantity: float

    def __post_init__(self) -> None:
        _positive(self.price, "price")
        _positive(self.quantity, "quantity")

    @property
    def notional(self) -> float:
        return float(self.price) * float(self.quantity)


@dataclass(frozen=True)
class OrderBookSnapshot:
    venue: str
    symbol: str
    observed_at: datetime
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    source: str
    source_hash: str

    def __post_init__(self) -> None:
        _text(self.venue, "venue")
        _text(self.symbol, "symbol")
        _aware(self.observed_at, "observed_at")
        _text(self.source, "source")
        _text(self.source_hash, "source_hash")
        if not self.bids or not self.asks:
            raise ValueError("order book requires nonempty bid and ask sides")
        if any(self.bids[i].price < self.bids[i + 1].price for i in range(len(self.bids) - 1)):
            raise ValueError("bids must be sorted descending")
        if any(self.asks[i].price > self.asks[i + 1].price for i in range(len(self.asks) - 1)):
            raise ValueError("asks must be sorted ascending")
        if self.best_bid >= self.best_ask:
            raise ValueError("crossed/locked order book is rejected")

    @property
    def best_bid(self) -> float:
        return float(self.bids[0].price)

    @property
    def best_ask(self) -> float:
        return float(self.asks[0].price)

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread_bps(self) -> float:
        return (self.best_ask - self.best_bid) / self.mid * 10_000.0

    def side_depth_notional(self, *, side: str, band_bps: float = 10.0) -> float:
        if not math.isfinite(float(band_bps)) or band_bps <= 0:
            raise ValueError("band_bps must be positive")
        fraction = float(band_bps) / 10_000.0
        if side == "bid":
            floor = self.mid * (1.0 - fraction)
            return float(sum(level.notional for level in self.bids if level.price >= floor))
        if side == "ask":
            ceiling = self.mid * (1.0 + fraction)
            return float(sum(level.notional for level in self.asks if level.price <= ceiling))
        raise ValueError("side must be bid or ask")

    def validate_for(self, decision_at: datetime, *, max_age_seconds: float = 30.0) -> None:
        decision = _aware(decision_at, "decision_at")
        observed = _aware(self.observed_at, "observed_at")
        if observed > decision:
            raise ValueError("future order-book snapshot is forbidden")
        age = (decision - observed).total_seconds()
        if age > float(max_age_seconds):
            raise ValueError("order-book snapshot is stale")

    def to_liquidity_snapshot(
        self,
        *,
        decision_at: datetime,
        band_bps: float = 10.0,
        max_age_seconds: float = 30.0,
    ) -> LiquiditySnapshot:
        self.validate_for(decision_at, max_age_seconds=max_age_seconds)
        bid_depth = self.side_depth_notional(side="bid", band_bps=band_bps)
        ask_depth = self.side_depth_notional(side="ask", band_bps=band_bps)
        conservative_depth = min(bid_depth, ask_depth)
        if conservative_depth <= 0:
            raise ValueError("order-book depth inside band is empty")
        return LiquiditySnapshot(
            symbol=self.symbol,
            observed_at=_aware(self.observed_at, "observed_at"),
            bid=self.best_bid,
            ask=self.best_ask,
            depth_notional_10bps=conservative_depth,
            source=self.source,
            source_hash=self.source_hash,
        )

    @property
    def snapshot_hash(self) -> str:
        return stable_hash(
            {
                "venue": self.venue,
                "symbol": self.symbol,
                "observed_at": self.observed_at,
                "bids": [(x.price, x.quantity) for x in self.bids],
                "asks": [(x.price, x.quantity) for x in self.asks],
                "source": self.source,
                "source_hash": self.source_hash,
            }
        )


@dataclass(frozen=True)
class PITMetric:
    entity: str
    metric: str
    effective_at: datetime
    available_at: datetime
    observed_at: datetime
    value: float
    source: str
    source_hash: str
    confidence: float = 1.0

    def __post_init__(self) -> None:
        _text(self.entity, "entity")
        _text(self.metric, "metric")
        effective = _aware(self.effective_at, "effective_at")
        available = _aware(self.available_at, "available_at")
        observed = _aware(self.observed_at, "observed_at")
        if effective > observed or available > observed:
            raise ValueError("effective/available time cannot be after observed_at")
        value = float(self.value)
        if not math.isfinite(value):
            raise ValueError("metric value must be finite")
        _text(self.source, "source")
        _text(self.source_hash, "source_hash")
        confidence = float(self.confidence)
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("confidence must be in [0,1]")

    def validate_for(self, decision_at: datetime) -> None:
        decision = _aware(decision_at, "decision_at")
        if _aware(self.available_at, "available_at") > decision:
            raise ValueError("PIT availability violation")

    @property
    def metric_hash(self) -> str:
        return stable_hash(self)


@dataclass(frozen=True)
class MicrostructureEvidence:
    order_book: OrderBookSnapshot | None
    metrics: Mapping[str, PITMetric]

    def features_for(self, decision_at: datetime) -> dict[str, float | str]:
        features: dict[str, float | str] = {}
        if self.order_book is None:
            features["order_book"] = "UNAVAILABLE_DATA"
        else:
            self.order_book.validate_for(decision_at)
            features["spread_bps"] = self.order_book.spread_bps
            features["bid_depth_10bps"] = self.order_book.side_depth_notional(side="bid")
            features["ask_depth_10bps"] = self.order_book.side_depth_notional(side="ask")
        for name, metric in sorted(self.metrics.items()):
            metric.validate_for(decision_at)
            features[name] = float(metric.value)
        return features
