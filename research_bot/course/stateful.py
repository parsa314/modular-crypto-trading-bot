"""Offline stateful strategy research, all advanced logic ENGINEERING_EXTENSION.

No network, execution transport or scientific promotion. Snapshot simulations do
not establish historical fills, future liquidity or profitable arbitrage.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from .contracts import (
    BookSnapshot,
    HypothesisCandidate,
    MarketMetadata,
    aware,
    decimal,
    digest,
)
from .simulation import ZERO, OfflineOrderSimulator, SimOrderRequest, floor_step


def _book_from_dict(raw: dict[str, Any]) -> BookSnapshot:
    value = dict(raw)
    metadata = dict(value["market"])
    for field in ("tick_size", "step_size", "minimum_notional"):
        metadata[field] = Decimal(metadata[field])
    value["market"] = MarketMetadata(**metadata)
    for field in ("exchange_timestamp", "observed_at", "received_at", "available_at"):
        value[field] = datetime.fromisoformat(value[field])
    for field in ("bids", "asks"):
        value[field] = tuple((Decimal(price), Decimal(size)) for price, size in value[field])
    return BookSnapshot(**value)


@dataclass(frozen=True)
class GridConfig:
    version: str = "COURSE_GRID_RESEARCH_V1"
    max_levels: int = 10
    maker_fee_rate: Decimal = Decimal("0.001")
    taker_fee_rate: Decimal = Decimal("0.002")
    max_inventory_base: Decimal = Decimal(1)
    breakout_lower: Decimal = Decimal(1)
    breakout_upper: Decimal = Decimal(1000000)
    range_preregistration_ref: str = ""

    def __post_init__(self) -> None:
        if (not self.version or not isinstance(self.max_levels, int) or isinstance(self.max_levels, bool)
                or not 1 <= self.max_levels <= 100 or not self.range_preregistration_ref):
            raise ValueError("GRID_REQUIRES_VERSION_LEVEL_LIMIT_AND_PREREGISTRATION")
        for field in ("max_inventory_base", "breakout_lower", "breakout_upper"):
            decimal(getattr(self, field), positive=True)
        for field in ("maker_fee_rate", "taker_fee_rate"):
            value = decimal(getattr(self, field))
            if value >= 1:
                raise ValueError("INVALID_GRID_FEE")
        if self.breakout_lower >= self.breakout_upper:
            raise ValueError("INVALID_GRID_BREAKOUT_BOUNDS")


@dataclass(frozen=True)
class GridLevel:
    side: Literal["BUY", "SELL"]
    quantity: Decimal
    price: Decimal


class GridStrategyStateMachine:
    """Whole-plan reservation; no automatic recentering or unhedged replacement.

    Caller owns range classification and independent Risk Gate. The explicit
    range evidence must be available at decision time. Locking capital belongs
    to the simulated wallet; this class cannot authorize actual orders.
    """
    def __init__(self, simulator: OfflineOrderSimulator, config: GridConfig) -> None:
        self.simulator = simulator
        self.config = config
        self.state = "INITIALIZED"
        self.plan_id: str | None = None
        self.order_ids: tuple[str, ...] = ()
        self.market: MarketMetadata | None = None
        self.events: list[dict[str, Any]] = []

    def _check_time(self, now: datetime) -> None:
        aware(now)
        if self.events and now < self.events[-1]["timestamp"]:
            raise ValueError("NONMONOTONIC_GRID_TIME")
        self.simulator._check_time(now)

    def _transition(self, state: str, now: datetime, **details: Any) -> None:
        aware(now)
        if self.events and now < self.events[-1]["timestamp"]:
            raise ValueError("NONMONOTONIC_GRID_TIME")
        self.state = state
        item = {"state": state, "timestamp": now, "details": details,
                "plan_id": self.plan_id, "sequence": len(self.events)}
        self.events.append({**item, "event_id": digest(item)})

    def prepare(self, book: BookSnapshot, levels: tuple[GridLevel, ...], now: datetime, *,
                range_confirmed: bool, range_evidence_ref: str,
                range_evidence_available_at: datetime) -> str:
        self._check_time(now)
        if self.state not in {"INITIALIZED", "CANCELLED", "COMPLETED"}:
            raise ValueError("GRID_PLAN_STILL_ACTIVE_OR_HALTED")
        book.validate_at(now)
        aware(range_evidence_available_at)
        if (range_confirmed is not True or not range_evidence_ref
                or range_evidence_available_at > now):
            raise ValueError("RANGE_PREREGISTERED_EVIDENCE_REQUIRED_POINT_IN_TIME")
        if not isinstance(levels, tuple) or not 1 <= len(levels) <= self.config.max_levels:
            raise ValueError("GRID_LEVEL_LIMIT")
        if len(set(levels)) != len(levels):
            raise ValueError("DUPLICATE_GRID_LEVEL")
        for level in levels:
            if level.side not in {"BUY", "SELL"}:
                raise ValueError("INVALID_GRID_SIDE")
            book.market.validate_order(level.quantity, level.price)
            if not self.config.breakout_lower < level.price < self.config.breakout_upper:
                raise ValueError("GRID_LEVEL_OUTSIDE_BREAKOUT_BOUNDS")
        prospective_base = self.simulator.balances.get(book.market.base, ZERO)
        prospective_base += sum((level.quantity for level in levels if level.side == "BUY"), ZERO)
        if prospective_base > self.config.max_inventory_base:
            raise ValueError("GRID_INVENTORY_CONCENTRATION")
        self.market = book.market
        self.plan_id = digest((asdict(self.config), book.snapshot_hash, levels, now, range_evidence_ref))
        self.order_ids = tuple(digest((self.plan_id, index, level)) for index, level in enumerate(levels))
        self._levels = levels
        self._decision_book = book
        self._decision_time = now
        self._range_evidence_ref = range_evidence_ref
        self._range_available_at = range_evidence_available_at
        self._transition("READY", now, range_evidence_ref=range_evidence_ref,
                         snapshot_hash=book.snapshot_hash, config_hash=digest(asdict(self.config)))
        return self.plan_id

    def to_candidate(self) -> HypothesisCandidate:
        if self.plan_id is None or self.market is None:
            raise ValueError("GRID_NOT_PREPARED")
        capital: dict[str, Decimal] = {}
        for level in self._levels:
            currency = self.market.quote if level.side == "BUY" else self.market.base
            amount = level.quantity * level.price * (1 + self.config.maker_fee_rate) if level.side == "BUY" else level.quantity
            capital[currency] = capital.get(currency, ZERO) + amount
        book = self._decision_book
        return HypothesisCandidate(
            event_id=self.plan_id, strategy_id="COURSE_GRID", strategy_version=self.config.version,
            symbol=self.market.symbol, venue=self.market.venue, direction=0,
            decision_time=self._decision_time, earliest_execution_time=self._decision_time + timedelta(microseconds=1),
            feature_snapshot_id=book.snapshot_hash, config_hash=digest(asdict(self.config)),
            expected_edge=None, expected_cost=ZERO, uncertainty=Decimal(1), confidence=None,
            regime="PREREGISTERED_RANGE", invalidation_conditions=("BREAKOUT", "REGIME_CHANGE", "INVENTORY_LIMIT"),
            data_quality=book.quality_status, evidence_refs=(self.config.range_preregistration_ref,
                self._range_evidence_ref, "ENGINEERING_EXTENSION:OFFLINE_GRID_V1"),
            capital_requirements=tuple(sorted(capital.items())), provenance="ENGINEERING_EXTENSION",
            evidence_available_at=max(book.available_at, self._range_available_at), source_observed_at=book.observed_at)

    def reserve(self, book: BookSnapshot, now: datetime, *, risk_approved: bool) -> str:
        self._check_time(now)
        if self.state != "READY":
            return self.state
        book.validate_at(now)
        if book.market != self.market:
            raise ValueError("GRID_RESERVATION_MARKET_MISMATCH")
        if risk_approved is not True:
            self._transition("HALTED", now, reason="INDEPENDENT_RISK_APPROVAL_REQUIRED")
            return self.state
        checkpoint = self.simulator.export_state()
        try:
            for order_id, level in zip(self.order_ids, self._levels):
                request = SimOrderRequest(order_id, book.market, level.side, level.quantity,
                                          "LIMIT", level.price, self.config.maker_fee_rate,
                                          book.market.quote)
                self.simulator.create(request, now)
                if self.simulator.approve(order_id, book, now, risk_approved=risk_approved) != "APPROVED":
                    raise ValueError("GRID_TOTAL_CAPITAL_OR_INVENTORY_UNAVAILABLE")
        except Exception:
            restored = OfflineOrderSimulator.from_state(checkpoint)
            self.simulator.__dict__.update(restored.__dict__)
            self._transition("HALTED", now, reason="GRID_RESERVATION_FAILED_ROLLED_BACK")
            raise
        self._transition("RESERVED", now, dependencies=self.order_ids)
        return self.state

    def activate(self, now: datetime) -> str:
        self._check_time(now)
        if self.state != "RESERVED":
            return self.state
        for order_id in self.order_ids:
            self.simulator.submit(order_id, now)
        self._transition("ACTIVE", now)
        return self.state

    def record_fill(self, order_id: str, book: BookSnapshot, now: datetime, *,
                    queue_evidence_id: str, quantity: Decimal) -> str:
        self._check_time(now)
        if self.state not in {"ACTIVE", "PARTIALLY_FILLED"} or order_id not in self.order_ids:
            raise ValueError("GRID_FILL_OUTSIDE_ACTIVE_PLAN")
        self.check_market(book, now, range_still_valid=True)
        if self.state == "HALTED":
            return self.state
        previously_filled = self.simulator.orders[order_id]["filled"]
        self.simulator.fill(order_id, book, now, queue_evidence_id=queue_evidence_id,
                            queue_fill_quantity=quantity)
        if self.simulator.orders[order_id]["filled"] == previously_filled:
            return self.state
        complete = all(self.simulator.orders[item]["state"] == "FILLED" for item in self.order_ids)
        self._transition("COMPLETED" if complete else "PARTIALLY_FILLED", now,
                         filled_order=order_id, queue_evidence_ref=queue_evidence_id)
        return self.state

    def check_market(self, book: BookSnapshot, now: datetime, *, range_still_valid: bool) -> str:
        self._check_time(now)
        if self.state not in {"RESERVED", "ACTIVE", "PARTIALLY_FILLED"}:
            return self.state
        try:
            book.validate_at(now)
            if book.market != self.market:
                raise ValueError("GRID_MARKET_MISMATCH")
            if (range_still_valid is not True or book.bids[0][0] <= self.config.breakout_lower
                    or book.asks[0][0] >= self.config.breakout_upper):
                raise ValueError("GRID_BREAKOUT_OR_REGIME_CHANGE")
        except ValueError as error:
            self.cancel(now, halt=True, reason=str(error))
        return self.state

    def cancel(self, now: datetime, *, halt: bool = False, reason: str = "OPERATOR_CANCEL") -> str:
        self._check_time(now)
        if self.state in {"COMPLETED", "CANCELLED", "HALTED"}:
            return self.state
        for order_id in self.order_ids:
            if order_id in self.simulator.orders:
                self.simulator.cancel(order_id, now)
        self._transition("HALTED" if halt else "CANCELLED", now, reason=reason,
                         residual_inventory=dict(self.simulator.balances))
        return self.state

    def recenter(self, now: datetime) -> str:
        self._check_time(now)
        if self.state not in {"ACTIVE", "PARTIALLY_FILLED"}:
            raise ValueError("RECENTER_REQUIRES_ACTIVE_PLAN")
        self._transition("REBALANCING", now, reason="CANCEL_THEN_REASSESS_NEW_RANGE_REQUIRED")
        return self.cancel(now, reason="RECENTER_CANCELS_OLD_PLAN_NO_AUTOMATIC_REPLACEMENT")

    def export_state(self) -> dict[str, Any]:
        import json
        payload = {"version": "OFFLINE_GRID_V1", "config": asdict(self.config), "state": self.state,
                   "plan_id": self.plan_id, "order_ids": self.order_ids,
                   "market": asdict(self.market) if self.market else None,
                   "levels": [asdict(level) for level in getattr(self, "_levels", ())],
                   "events": self.events, "simulator": self.simulator.export_state(),
                   "decision_book": asdict(self._decision_book) if hasattr(self, "_decision_book") else None,
                   "decision_time": getattr(self, "_decision_time", None),
                   "range_evidence_ref": getattr(self, "_range_evidence_ref", None),
                   "range_available_at": getattr(self, "_range_available_at", None)}
        serial = json.loads(json.dumps(payload, default=str, sort_keys=True))
        return {"payload": serial, "sha256": digest(serial)}

    @classmethod
    def from_state(cls, artifact: dict[str, Any]) -> GridStrategyStateMachine:
        payload = artifact["payload"]
        if digest(payload) != artifact["sha256"] or payload["version"] != "OFFLINE_GRID_V1":
            raise ValueError("GRID_STATE_INTEGRITY_FAILURE")
        config = dict(payload["config"])
        for field in ("maker_fee_rate", "taker_fee_rate", "max_inventory_base", "breakout_lower", "breakout_upper"):
            config[field] = Decimal(config[field])
        result = cls(OfflineOrderSimulator.from_state(payload["simulator"]), GridConfig(**config))
        result.state, result.plan_id = payload["state"], payload["plan_id"]
        result.order_ids = tuple(payload["order_ids"])
        metadata = payload["market"]
        if metadata:
            for field in ("tick_size", "step_size", "minimum_notional"):
                metadata[field] = Decimal(metadata[field])
            result.market = MarketMetadata(**metadata)
        result._levels = tuple(GridLevel(level["side"], Decimal(level["quantity"]), Decimal(level["price"]))
                               for level in payload["levels"])
        result.events = [{**event, "timestamp": datetime.fromisoformat(event["timestamp"])}
                         for event in payload["events"]]
        if payload["decision_book"]:
            result._decision_book = _book_from_dict(payload["decision_book"])
            result._decision_time = datetime.fromisoformat(payload["decision_time"])
            result._range_evidence_ref = payload["range_evidence_ref"]
            result._range_available_at = datetime.fromisoformat(payload["range_available_at"])
        return result


@dataclass(frozen=True)
class ConversionLeg:
    book: BookSnapshot
    side: Literal["BUY", "SELL"]
    fee_rate: Decimal
    fee_currency: str = "OUTPUT"

    def __post_init__(self) -> None:
        decimal(self.fee_rate)
        if self.side not in {"BUY", "SELL"} or self.fee_rate >= 1:
            raise ValueError("INVALID_CONVERSION_LEG")
        if self.fee_currency != "OUTPUT":
            raise ValueError("ARBITRAGE_ONLY_DOCUMENTED_OUTPUT_FEE_CONVENTION")

    @property
    def input_currency(self) -> str:
        return self.book.market.quote if self.side == "BUY" else self.book.market.base

    @property
    def output_currency(self) -> str:
        return self.book.market.base if self.side == "BUY" else self.book.market.quote


@dataclass(frozen=True)
class ConversionQuote:
    input_currency: str
    output_currency: str
    input_amount: Decimal
    input_spent: Decimal
    residual_input: Decimal
    base_quantity: Decimal
    executable_vwap: Decimal | None
    gross_output: Decimal
    fee_output: Decimal
    net_output: Decimal
    depth_complete: bool


def quote_conversion(leg: ConversionLeg, amount: Decimal) -> ConversionQuote:
    """Directional depth walk with output-currency fees; residual is preserved."""
    decimal(amount, positive=True)
    market = leg.book.market
    remaining, quantity, notional = amount, ZERO, ZERO
    levels = leg.book.asks if leg.side == "BUY" else leg.book.bids
    if any(price % market.tick_size for price, _ in levels):
        raise ValueError("ARBITRAGE_BOOK_PRICE_TICK_MISMATCH")
    for price, size in levels:
        available = remaining / price if leg.side == "BUY" else remaining
        traded = floor_step(min(size, available), market.step_size)
        if traded:
            quantity += traded
            notional += traded * price
            remaining -= traded * price if leg.side == "BUY" else traded
    if quantity == 0 or notional < market.minimum_notional:
        raise ValueError("ARBITRAGE_MINIMUM_NOTIONAL_OR_PRECISION")
    gross = quantity if leg.side == "BUY" else notional
    fee = gross * leg.fee_rate
    # Dust smaller than one tradable unit is a residual, not depth failure.
    smallest_input_unit = market.step_size * levels[-1][0] if leg.side == "BUY" else market.step_size
    complete = remaining < smallest_input_unit
    return ConversionQuote(leg.input_currency, leg.output_currency, amount, amount - remaining,
                           remaining, quantity, notional / quantity, gross, fee, gross - fee, complete)


@dataclass(frozen=True)
class AtomicityRiskAssessment:
    atomic: bool
    possible_unhedged_currencies: tuple[str, ...]
    max_snapshot_skew_seconds: Decimal
    modeled_latency_seconds: Decimal
    risk_buffer: Decimal
    failure_paths: tuple[str, ...]
    status: str = "NON_ATOMIC_EXECUTION_RISK_NOT_ELIMINATED"

    def __post_init__(self) -> None:
        if self.atomic is not False or not self.failure_paths:
            raise ValueError("ARBITRAGE_ATOMICITY_CANNOT_BE_ASSUMED")
        for field in ("max_snapshot_skew_seconds", "modeled_latency_seconds", "risk_buffer"):
            decimal(getattr(self, field))


@dataclass(frozen=True)
class ArbitrageOpportunityHypothesis:
    event_id: str
    decision_time: datetime
    earliest_execution_time: datetime
    initial_currency: str
    initial_capital: Decimal
    executable_proceeds: Decimal
    expected_net_edge: Decimal
    risk_buffer: Decimal
    legs: tuple[ConversionLeg, ...]
    quotes: tuple[ConversionQuote, ...]
    residual_inventory: tuple[tuple[str, Decimal], ...]
    fees_by_currency: tuple[tuple[str, Decimal], ...]
    snapshot_hashes: tuple[str, ...]
    atomicity: AtomicityRiskAssessment
    status: str = "RESEARCH_CANDIDATE"
    provenance: str = "ENGINEERING_EXTENSION"

    def __post_init__(self) -> None:
        aware(self.decision_time)
        aware(self.earliest_execution_time)
        decimal(self.initial_capital, positive=True)
        decimal(self.executable_proceeds)
        decimal(self.risk_buffer)
        if (not self.event_id or not self.initial_currency or len(self.legs) != 3
                or len(self.quotes) != 3 or self.earliest_execution_time <= self.decision_time
                or not self.expected_net_edge.is_finite()
                or self.expected_net_edge != self.executable_proceeds - self.initial_capital - self.risk_buffer
                or self.status != "RESEARCH_CANDIDATE"):
            raise ValueError("INVALID_ARBITRAGE_HYPOTHESIS")

    def to_candidate(self) -> HypothesisCandidate:
        books = tuple(leg.book for leg in self.legs)
        return HypothesisCandidate(
            event_id=self.event_id, strategy_id="COURSE_TRIANGULAR_ARBITRAGE",
            strategy_version="COURSE_TRIANGLE_RESEARCH_V1", symbol="->".join(leg.input_currency for leg in self.legs) + "->" + self.initial_currency,
            venue=books[0].market.venue, direction=0, decision_time=self.decision_time,
            earliest_execution_time=self.earliest_execution_time, feature_snapshot_id=digest(self.snapshot_hashes),
            config_hash=digest((self.atomicity, tuple((leg.side, leg.fee_rate, leg.fee_currency) for leg in self.legs))),
            expected_edge=(self.expected_net_edge + self.risk_buffer) / self.initial_capital,
            expected_cost=ZERO, uncertainty=self.risk_buffer / self.initial_capital, confidence=None,
            regime="UNESTIMATED", invalidation_conditions=self.atomicity.failure_paths,
            data_quality="VALID", evidence_refs=self.snapshot_hashes + ("ENGINEERING_EXTENSION:DEPTH_FEES_INCLUDED_IN_NET_EDGE",),
            capital_requirements=((self.initial_currency, self.initial_capital),), provenance="ENGINEERING_EXTENSION",
            evidence_available_at=max(book.available_at for book in books),
            source_observed_at=max(book.observed_at for book in books))


class TriangularArbitrageEngine:
    def __init__(self, *, max_snapshot_age: timedelta = timedelta(seconds=10),
                 max_snapshot_skew: timedelta = timedelta(seconds=1),
                 latency: timedelta = timedelta(milliseconds=100),
                 risk_buffer_bps: Decimal = Decimal(10)) -> None:
        decimal(risk_buffer_bps)
        if max_snapshot_age <= timedelta(0) or max_snapshot_skew < timedelta(0) or latency <= timedelta(0):
            raise ValueError("INVALID_ARBITRAGE_TIME_POLICY")
        self.max_snapshot_age = max_snapshot_age
        self.max_snapshot_skew = max_snapshot_skew
        self.latency = latency
        self.risk_buffer_bps = risk_buffer_bps

    @staticmethod
    def discover_cycles(books: tuple[BookSnapshot, ...], start_currency: str, *,
                        fee_rate: Decimal) -> tuple[tuple[ConversionLeg, ...], ...]:
        edges = tuple(ConversionLeg(book, side, fee_rate) for book in books for side in ("BUY", "SELL"))
        cycles: list[tuple[ConversionLeg, ...]] = []
        for first in edges:
            if first.input_currency != start_currency:
                continue
            for second in edges:
                if second.input_currency != first.output_currency or second.output_currency == start_currency:
                    continue
                for third in edges:
                    if (third.input_currency == second.output_currency and third.output_currency == start_currency
                            and len({first.book.market.symbol, second.book.market.symbol, third.book.market.symbol}) == 3):
                        cycles.append((first, second, third))
        return tuple(cycles)

    def evaluate(self, legs: tuple[ConversionLeg, ...], capital: Decimal,
                 now: datetime) -> ArbitrageOpportunityHypothesis:
        aware(now)
        decimal(capital, positive=True)
        if len(legs) != 3 or len({leg.book.market.venue for leg in legs}) != 1:
            raise ValueError("ARBITRAGE_REQUIRES_THREE_SAME_VENUE_LEGS")
        if len({leg.input_currency for leg in legs}) != 3:
            raise ValueError("ARBITRAGE_REQUIRES_THREE_DISTINCT_CURRENCIES")
        for index, leg in enumerate(legs):
            if leg.output_currency != legs[(index + 1) % 3].input_currency:
                raise ValueError("ARBITRAGE_CURRENCY_DIRECTION_MISMATCH")
            leg.book.validate_at(now, max_age=self.max_snapshot_age)
        stamps = [leg.book.exchange_timestamp for leg in legs]
        skew = max(stamps) - min(stamps)
        if skew > self.max_snapshot_skew:
            raise ValueError("ARBITRAGE_SNAPSHOT_TIME_SKEW")
        amount = capital
        quotes: list[ConversionQuote] = []
        residual: dict[str, Decimal] = {}
        fees: dict[str, Decimal] = {}
        for leg in legs:
            quote = quote_conversion(leg, amount)
            quotes.append(quote)
            if not quote.depth_complete:
                raise ValueError("ARBITRAGE_INSUFFICIENT_DEPTH")
            residual[quote.input_currency] = residual.get(quote.input_currency, ZERO) + quote.residual_input
            fees[quote.output_currency] = fees.get(quote.output_currency, ZERO) + quote.fee_output
            amount = quote.net_output
        initial_currency = legs[0].input_currency
        proceeds = amount + residual.get(initial_currency, ZERO)
        buffer = capital * self.risk_buffer_bps / Decimal(10000)
        risk = AtomicityRiskAssessment(False, tuple(leg.output_currency for leg in legs[:-1]),
                                      Decimal(str(skew.total_seconds())),
                                      Decimal(str(self.latency.total_seconds())), buffer,
                                      ("LEG_REJECTION", "PARTIAL_FILL", "DEPTH_DEPLETION", "PRICE_CHANGE",
                                       "TIMEOUT_UNKNOWN", "UNWIND_LOSS", "RESIDUAL_INVENTORY"))
        identity = digest((tuple(leg.book.snapshot_hash for leg in legs), capital, now,
                           self.max_snapshot_age, self.max_snapshot_skew, self.latency, self.risk_buffer_bps,
                           tuple((leg.side, leg.fee_rate, leg.fee_currency) for leg in legs)))
        return ArbitrageOpportunityHypothesis(identity, now, now + self.latency, initial_currency,
                                             capital, proceeds, proceeds - capital - buffer, buffer,
                                             legs, tuple(quotes), tuple(sorted(residual.items())),
                                             tuple(sorted(fees.items())),
                                             tuple(leg.book.snapshot_hash for leg in legs), risk)


class ArbitrageReplayPlan:
    """Three dependent simulated legs; unknown/partial leg cancels downstream.

    Actual net filled output determines the next order. Reservations and fills
    are provided by the shared offline wallet. No claim of atomic execution.
    """
    def __init__(self, hypothesis: ArbitrageOpportunityHypothesis,
                 simulator: OfflineOrderSimulator) -> None:
        self.hypothesis = hypothesis
        self.simulator = simulator
        self.state = "CREATED"
        self.next_leg = 0
        self.amount = hypothesis.initial_capital
        self.order_ids: list[str] = []
        self.failure_reason: str | None = None
        self.events: list[dict[str, Any]] = []
        self.current_currency = hypothesis.initial_currency

    def execute_leg(self, book: BookSnapshot, now: datetime, *, risk_approved: bool,
                    reject: bool = False) -> str:
        aware(now)
        if self.state in {"HALTED", "COMPLETED"}:
            return self.state
        if now < self.hypothesis.earliest_execution_time:
            raise ValueError("ARBITRAGE_EXECUTION_BEFORE_ALLOWED_TIME")
        if self.events and now < self.events[-1]["timestamp"] + timedelta(seconds=float(self.hypothesis.atomicity.modeled_latency_seconds)):
            raise ValueError("ARBITRAGE_INTER_LEG_LATENCY_NOT_ELAPSED")
        expected = self.hypothesis.legs[self.next_leg]
        if book.market != expected.book.market:
            raise ValueError("ARBITRAGE_LEG_MARKET_MISMATCH")
        # A newer snapshot may change size; no reliance on the original estimate.
        book.validate_at(now)
        leg = ConversionLeg(book, expected.side, expected.fee_rate)
        order_id = digest((self.hypothesis.event_id, "ACTUAL_FILLED_LEG", self.next_leg))
        try:
            quote = quote_conversion(leg, self.amount)
            request = SimOrderRequest(order_id, book.market, leg.side, quote.base_quantity,
                                      fee_rate=leg.fee_rate)
            self.simulator.create(request, now)
            approved = self.simulator.approve(order_id, book, now, risk_approved=risk_approved and not reject)
            self.order_ids.append(order_id)
            if approved != "APPROVED":
                raise ValueError("LEG_REJECTED")
            self.simulator.submit(order_id, now)
            state = self.simulator.fill(order_id, book, now)
            order = self.simulator.orders[order_id]
            if order["filled"] > 0:
                self.amount = order["net_output"]
                self.current_currency = leg.output_currency
            if not quote.depth_complete or state != "FILLED":
                self.simulator.cancel(order_id, now)
                raise ValueError("PARTIAL_LEG_UNHEDGED_INVENTORY")
            self.next_leg += 1
            self.state = "COMPLETED" if self.next_leg == 3 else "AWAITING_NEXT_LEG"
        except ValueError as error:
            self.failure_reason = str(error)
            self.state = "HALTED"
        record = {"leg": self.next_leg, "order_id": order_id, "timestamp": now, "state": self.state,
                  "amount": self.amount, "currency": self.current_currency,
                  "failure_reason": self.failure_reason, "balances": dict(self.simulator.balances)}
        self.events.append({**record, "event_id": digest(record)})
        return self.state

    def plan_inventory(self) -> dict[str, Decimal]:
        """Order-accounted inventory excludes other strategies' wallet funds."""
        inventory = {self.hypothesis.initial_currency: self.hypothesis.initial_capital}
        for order_id in self.order_ids:
            order = self.simulator.orders[order_id]
            request = order["request"]
            input_currency = request.market.quote if request.side == "BUY" else request.market.base
            output_currency = request.market.base if request.side == "BUY" else request.market.quote
            spent = order["notional"] if request.side == "BUY" else order["filled"]
            inventory[input_currency] = inventory.get(input_currency, ZERO) - spent
            inventory[output_currency] = inventory.get(output_currency, ZERO) + order["net_output"]
        return inventory

    def assess_unwind(self, reverse_legs: tuple[ConversionLeg, ...], now: datetime) -> dict[str, Any]:
        if self.state != "HALTED":
            raise ValueError("UNWIND_ASSESSMENT_REQUIRES_FAILED_PLAN")
        estimates: dict[str, dict[str, Any]] = {}
        inventory = self.plan_inventory()
        for leg in reverse_legs:
            leg.book.validate_at(now)
            amount = inventory.get(leg.input_currency, ZERO)
            if amount <= 0 or leg.output_currency != self.hypothesis.initial_currency:
                continue
            try:
                quote = quote_conversion(leg, amount)
                estimates[leg.input_currency] = {"initial_currency_proceeds": quote.net_output,
                                                "fee_output": quote.fee_output,
                                                "residual_input": quote.residual_input,
                                                "depth_complete": quote.depth_complete}
            except ValueError as error:
                estimates[leg.input_currency] = {"status": "UNWIND_UNAVAILABLE", "reason": str(error)}
        proceeds = inventory.get(self.hypothesis.initial_currency, ZERO)
        proceeds += sum((item.get("initial_currency_proceeds", ZERO) for item in estimates.values()), ZERO)
        return {"status": "HYPOTHETICAL_UNWIND_NOT_EXECUTED", "estimates": estimates,
                "estimated_proceeds": proceeds,
                "estimated_shortfall": max(ZERO, self.hypothesis.initial_capital - proceeds),
                "unvalued_currencies": tuple(currency for currency, balance in inventory.items()
                                              if balance > 0 and currency != self.hypothesis.initial_currency
                                              and currency not in estimates)}


    def export_state(self) -> dict[str, Any]:
        import json
        payload = {"version": "OFFLINE_ARBITRAGE_PLAN_V1", "hypothesis": asdict(self.hypothesis),
                   "simulator": self.simulator.export_state(), "state": self.state,
                   "next_leg": self.next_leg, "amount": self.amount, "order_ids": self.order_ids,
                   "failure_reason": self.failure_reason, "events": self.events,
                   "current_currency": self.current_currency}
        serial = json.loads(json.dumps(payload, default=str, sort_keys=True))
        return {"payload": serial, "sha256": digest(serial)}

    @classmethod
    def from_state(cls, artifact: dict[str, Any]) -> ArbitrageReplayPlan:
        payload = artifact["payload"]
        if digest(payload) != artifact["sha256"] or payload["version"] != "OFFLINE_ARBITRAGE_PLAN_V1":
            raise ValueError("ARBITRAGE_STATE_INTEGRITY_FAILURE")
        hypothesis = dict(payload["hypothesis"])
        hypothesis["legs"] = tuple(ConversionLeg(_book_from_dict(leg["book"]), leg["side"],
                                                Decimal(leg["fee_rate"]), leg["fee_currency"])
                                   for leg in hypothesis["legs"])
        quotes = []
        for raw in hypothesis["quotes"]:
            quote = dict(raw)
            for field in ("input_amount", "input_spent", "residual_input", "base_quantity",
                          "executable_vwap", "gross_output", "fee_output", "net_output"):
                quote[field] = None if quote[field] is None else Decimal(quote[field])
            quotes.append(ConversionQuote(**quote))
        hypothesis["quotes"] = tuple(quotes)
        for field in ("decision_time", "earliest_execution_time"):
            hypothesis[field] = datetime.fromisoformat(hypothesis[field])
        for field in ("initial_capital", "executable_proceeds", "expected_net_edge", "risk_buffer"):
            hypothesis[field] = Decimal(hypothesis[field])
        for field in ("residual_inventory", "fees_by_currency"):
            hypothesis[field] = tuple((currency, Decimal(amount)) for currency, amount in hypothesis[field])
        hypothesis["snapshot_hashes"] = tuple(hypothesis["snapshot_hashes"])
        risk = dict(hypothesis["atomicity"])
        for field in ("max_snapshot_skew_seconds", "modeled_latency_seconds", "risk_buffer"):
            risk[field] = Decimal(risk[field])
        for field in ("possible_unhedged_currencies", "failure_paths"):
            risk[field] = tuple(risk[field])
        hypothesis["atomicity"] = AtomicityRiskAssessment(**risk)
        result = cls(ArbitrageOpportunityHypothesis(**hypothesis),
                     OfflineOrderSimulator.from_state(payload["simulator"]))
        result.state, result.next_leg = payload["state"], payload["next_leg"]
        result.amount = Decimal(payload["amount"])
        result.order_ids = payload["order_ids"]
        result.failure_reason, result.current_currency = payload["failure_reason"], payload["current_currency"]
        result.events = [{**event, "timestamp": datetime.fromisoformat(event["timestamp"])}
                         for event in payload["events"]]
        return result
