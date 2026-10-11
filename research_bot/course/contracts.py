"""Immutable research inputs; no credentials or order transport."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from ..contracts import Decision, EvidenceStamp, ResearchStatus, SignalEvidence


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, default=str, sort_keys=True, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def decimal(value: Any, *, positive: bool = False) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite() or result < 0 or (positive and result <= 0):
        raise ValueError("INVALID_FINANCIAL_VALUE")
    return result


def aware(value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError("NAIVE_TIMESTAMP")


@dataclass(frozen=True)
class MarketMetadata:
    venue: str
    symbol: str
    base: str
    quote: str
    tick_size: Decimal
    step_size: Decimal
    minimum_notional: Decimal
    quote_unit: str

    def __post_init__(self) -> None:
        if (
            not all((self.venue, self.symbol, self.base, self.quote, self.quote_unit))
            or self.base == self.quote
        ):
            raise ValueError("INVALID_MARKET_IDENTITY")
        for name in ("tick_size", "step_size", "minimum_notional"):
            if not isinstance(getattr(self, name), Decimal):
                raise TypeError("FINANCIAL_FIELDS_REQUIRE_DECIMAL")
            decimal(getattr(self, name), positive=True)
        if self.quote == "IRT" and self.quote_unit != "TOMAN":
            raise ValueError("IRT_MUST_BE_EXPLICIT_TOMAN")
        if self.quote == "IRR" and self.quote_unit != "RIAL":
            raise ValueError("IRR_MUST_BE_EXPLICIT_RIAL")

    def validate_order(self, quantity: Decimal, price: Decimal) -> None:
        decimal(quantity, positive=True)
        decimal(price, positive=True)
        if quantity % self.step_size or price % self.tick_size:
            raise ValueError("ORDER_PRECISION_MISMATCH")
        if quantity * price < self.minimum_notional:
            raise ValueError("MINIMUM_NOTIONAL_BREACH")


@dataclass(frozen=True)
class BookSnapshot:
    market: MarketMetadata
    exchange_timestamp: datetime
    observed_at: datetime
    received_at: datetime
    available_at: datetime
    sequence_id: str | None
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    source_hash: str
    data_version: str = "COURSE_BOOK_V1"
    quality_status: str = "VALID"

    def __post_init__(self) -> None:
        for name in (
            "exchange_timestamp",
            "observed_at",
            "received_at",
            "available_at",
        ):
            aware(getattr(self, name))
        if (
            not self.exchange_timestamp
            <= self.observed_at
            <= self.received_at
            <= self.available_at
        ):
            raise ValueError("INVALID_BOOK_TIMESTAMP_ORDER")
        if not self.bids or not self.asks or not self.source_hash:
            raise ValueError("INCOMPLETE_BOOK")
        for levels in (self.bids, self.asks):
            if not isinstance(levels, tuple) or any(
                not isinstance(level, tuple) for level in levels
            ):
                raise ValueError("MUTABLE_BOOK_LEVELS")
            for price, size in levels:
                if not isinstance(price, Decimal) or not isinstance(size, Decimal):
                    raise TypeError("BOOK_FIELDS_REQUIRE_DECIMAL")
                decimal(price, positive=True)
                decimal(size, positive=True)
        if any(a[0] <= b[0] for a, b in zip(self.bids, self.bids[1:])):
            raise ValueError("UNSORTED_OR_DUPLICATE_BIDS")
        if any(a[0] >= b[0] for a, b in zip(self.asks, self.asks[1:])):
            raise ValueError("UNSORTED_OR_DUPLICATE_ASKS")
        if self.bids[0][0] >= self.asks[0][0]:
            raise ValueError("CROSSED_OR_LOCKED_BOOK")

    def validate_at(
        self, decision_time: datetime, *, max_age: timedelta = timedelta(seconds=10)
    ) -> None:
        aware(decision_time)
        if self.quality_status != "VALID" or not self.sequence_id:
            raise ValueError("DATA_UNAVAILABLE_SEQUENCE_OR_QUALITY")
        if self.available_at > decision_time:
            raise ValueError("POINT_IN_TIME_BOOK_UNAVAILABLE")
        if decision_time - self.exchange_timestamp > max_age:
            raise ValueError("STALE_BOOK")

    @property
    def snapshot_hash(self) -> str:
        return digest(asdict(self))


@dataclass(frozen=True)
class HypothesisCandidate:
    event_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    venue: str
    direction: int
    decision_time: datetime
    earliest_execution_time: datetime
    feature_snapshot_id: str
    config_hash: str
    expected_edge: Decimal | None
    expected_cost: Decimal
    uncertainty: Decimal
    confidence: Decimal | None
    regime: str
    invalidation_conditions: tuple[str, ...]
    data_quality: str
    evidence_refs: tuple[str, ...]
    capital_requirements: tuple[tuple[str, Decimal], ...] = ()
    provenance: str = "REUSE_AS_CONCEPT"
    status: str = "RESEARCH_CANDIDATE"
    evidence_available_at: datetime | None = None
    source_observed_at: datetime | None = None

    def __post_init__(self) -> None:
        aware(self.decision_time)
        aware(self.earliest_execution_time)
        if (
            self.direction not in {-1, 0, 1}
            or self.earliest_execution_time <= self.decision_time
        ):
            raise ValueError("INVALID_DIRECTION_OR_SAME_CLOSE_EXECUTION")
        if not all(
            (
                self.event_id,
                self.strategy_id,
                self.strategy_version,
                self.symbol,
                self.venue,
                self.feature_snapshot_id,
                self.config_hash,
            )
        ):
            raise ValueError("INCOMPLETE_CANDIDATE_IDENTITY")
        decimal(self.expected_cost)
        decimal(self.uncertainty)
        if not isinstance(self.expected_cost, Decimal) or not isinstance(
            self.uncertainty, Decimal
        ):
            raise TypeError("CANDIDATE_FIELDS_REQUIRE_DECIMAL")
        for name in (
            "invalidation_conditions",
            "evidence_refs",
            "capital_requirements",
        ):
            if not isinstance(getattr(self, name), tuple):
                raise TypeError("IMMUTABLE_CANDIDATE_TUPLES_REQUIRED")
        if any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[1], Decimal)
            for item in self.capital_requirements
        ):
            raise TypeError("IMMUTABLE_DECIMAL_CAPITAL_REQUIRED")
        if self.expected_edge is not None and not self.expected_edge.is_finite():
            raise ValueError("NONFINITE_EDGE")
        if self.confidence is not None and not Decimal(0) <= self.confidence <= Decimal(
            1
        ):
            raise ValueError("INVALID_CONFIDENCE")
        for _, amount in self.capital_requirements:
            decimal(amount)
        if self.status != "RESEARCH_CANDIDATE":
            raise ValueError("UNAPPROVED_PROMOTION")
        if self.evidence_available_at is not None:
            aware(self.evidence_available_at)
            if self.evidence_available_at > self.decision_time:
                raise ValueError("POINT_IN_TIME_CANDIDATE_UNAVAILABLE")
        if self.source_observed_at is not None:
            aware(self.source_observed_at)
            if (
                self.evidence_available_at is None
                or self.source_observed_at > self.evidence_available_at
            ):
                raise ValueError("INVALID_CANDIDATE_SOURCE_TIME")

    def to_signal_evidence(self) -> SignalEvidence:
        # Unknown expectancy is not converted to an invented positive forecast.
        known = self.expected_edge is not None and self.confidence is not None
        return SignalEvidence(
            asset=self.symbol,
            timestamp=self.decision_time,
            expected_return=float(self.expected_edge or 0),
            expected_cost=float(self.expected_cost),
            risk_penalty=0,
            uncertainty_penalty=float(self.uncertainty),
            confidence=float(self.confidence or 0),
            decision=Decision.NO_TRADE,
            reasons=() if known else ("UNESTIMATED_EDGE_OR_CONFIDENCE",),
            evidence=()
            if self.source_observed_at is None or self.evidence_available_at is None
            else (
                EvidenceStamp(
                    self.venue,
                    self.source_observed_at,
                    self.evidence_available_at,
                    self.feature_snapshot_id,
                    status=ResearchStatus.HYPOTHESIS,
                ),
            ),
            metadata={"course_candidate": asdict(self), "direction": self.direction},
        )


class StrategyProtocol(Protocol):
    def generate(
        self, frame: Any, *, symbol: str, venue: str, decision_time: datetime
    ) -> list[HypothesisCandidate]: ...


class UnsupportedCapability(RuntimeError):
    pass
