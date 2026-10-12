"""Decimal directed conversion graph with depth, fees and non-atomic scenarios."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Mapping, Sequence

from ...hypothesis import HypothesisCandidate, HypothesisFamily, HypothesisSide, utc_text
from ...jamalian_data import OrderBook, currencies, available, decimal_value
from ..models import MarketRules, ScenarioEconomics, ZERO, ONE, floor_step, seconds


@dataclass(frozen=True)
class DirectedEdge:
    source: str
    target: str
    symbol: str
    side: str
    mid_rate: Decimal
    book: OrderBook


@dataclass(frozen=True)
class LegEstimate:
    edge: DirectedEdge
    input_amount: Decimal
    spent: Decimal
    gross_received: Decimal
    received: Decimal
    fee: Decimal
    base_quantity: Decimal
    vwap: Decimal
    depth_slippage_fraction: Decimal
    residual_input: Decimal
    depth_exhausted: bool

    def to_dict(self) -> dict:
        return {"symbol": self.edge.symbol, "side": self.edge.side,
            "source": self.edge.source, "target": self.edge.target,
            **{name: str(getattr(self, name)) for name in ("input_amount", "spent", "gross_received",
                "received", "fee", "base_quantity", "vwap", "depth_slippage_fraction", "residual_input")},
            "depth_exhausted": self.depth_exhausted}


def conversion_graph(books: Sequence[OrderBook]) -> dict[tuple[str, str], DirectedEdge]:
    graph = {}
    for book in books:
        base, quote = currencies(book.symbol)
        mid = (book.bids[0].price + book.asks[0].price) / 2
        for source, target, side, rate in ((quote, base, "BUY", ONE / mid), (base, quote, "SELL", mid)):
            if (source, target) in graph:
                raise ValueError("duplicate directed market edge")
            graph[source, target] = DirectedEdge(source, target, book.symbol, side, rate, book)
    return graph


def walk_book(edge: DirectedEdge, amount: Decimal, rules: MarketRules,
              fee_rate: Decimal, slippage_buffer: Decimal) -> LegEstimate:
    amount, fee_rate, slippage_buffer = decimal_value(amount, positive=True), decimal_value(fee_rate), decimal_value(slippage_buffer)
    if fee_rate >= ONE or slippage_buffer >= ONE:
        raise ValueError("invalid fee/slippage")
    buy = edge.side == "BUY"
    levels = edge.book.asks if buy else edge.book.bids
    remaining, spent, gross, quantity = amount, ZERO, ZERO, ZERO
    for level in levels:
        price = level.price * (ONE + slippage_buffer if buy else ONE - slippage_buffer)
        q = floor_step(min(level.quantity, remaining / price if buy else remaining), rules.quantity_step)
        if q <= ZERO:
            continue
        cost = q * price if buy else q
        received = q if buy else q * price
        spent += cost
        gross += received
        quantity += q
        remaining -= cost
        if remaining <= ZERO:
            break
    vwap = (spent / quantity if buy else gross / quantity) if quantity else ZERO
    if quantity == ZERO or quantity * vwap < rules.min_notional:
        raise ValueError("MIN_NOTIONAL_OR_LOT_UNSATISFIED")
    # Lot dust is residual, not lack of liquidity; distinguish it from exhausted depth.
    last_price = levels[-1].price * (ONE + slippage_buffer if buy else ONE - slippage_buffer)
    tolerance = rules.quantity_step * last_price if buy else rules.quantity_step
    exhausted = remaining >= tolerance
    mid = (edge.book.bids[0].price + edge.book.asks[0].price) / 2
    depth_slippage = max(ZERO, vwap / mid - ONE if buy else ONE - vwap / mid)
    fee = gross * fee_rate
    return LegEstimate(edge, amount, spent, gross, gross - fee, fee, quantity, vwap, depth_slippage, remaining, exhausted)


@dataclass(frozen=True)
class ArbitrageParameters:
    cycle: tuple[str, ...] = ("IRT", "USDT", "BTC", "IRT")
    fee_rate: Decimal = Decimal("0.001")
    slippage_buffer: Decimal = Decimal("0.0002")
    non_atomic_penalty: Decimal = Decimal("0.003")
    residual_penalty: Decimal = Decimal("0.003")
    uncertainty_penalty: Decimal = Decimal("0.001")
    threshold: Decimal = Decimal("0")
    max_age: timedelta = timedelta(seconds=5)
    max_skew: timedelta = timedelta(seconds=1)
    execution_delay: timedelta = timedelta(milliseconds=20)

    def __post_init__(self) -> None:
        object.__setattr__(self, "cycle", tuple(self.cycle))
        if len(self.cycle) != 4 or self.cycle[0] != self.cycle[-1] or len(set(self.cycle[:-1])) != 3:
            raise ValueError("three unique currencies in a closed cycle required")
        for name in ("fee_rate", "slippage_buffer", "non_atomic_penalty", "residual_penalty", "uncertainty_penalty", "threshold"):
            object.__setattr__(self, name, decimal_value(getattr(self, name)))
        if self.fee_rate >= ONE or self.slippage_buffer >= ONE or self.max_age <= timedelta(0) or self.max_skew < timedelta(0) or self.execution_delay <= timedelta(0):
            raise ValueError("invalid conversion timing/rates")


class TriangularArbitrageHypothesis:
    def __init__(self, parameters: ArbitrageParameters | None = None,
                 rules: Mapping[str, MarketRules] | None = None) -> None:
        self.parameters = parameters or ArbitrageParameters()
        self.rules = dict(rules or {})
        self.open_since: datetime | None = None
        self.last_at: datetime | None = None
        self.diagnostics: dict = {}

    def generate(self, books: Sequence[OrderBook], budget: Decimal, at: datetime,
                 first_seen: Sequence[Mapping] = ()) -> list[HypothesisCandidate]:
        p = self.parameters
        budget = decimal_value(budget, positive=True)
        if self.last_at is not None and at < self.last_at:
            raise ValueError("opportunity observations must be chronological")
        graph = conversion_graph(books)
        try:
            edges = [graph[a, b] for a, b in zip(p.cycle, p.cycle[1:])]
        except KeyError:
            raise ValueError("cycle edge unavailable") from None
        stamps = tuple(e.book.stamp for e in edges)
        for stamp in stamps:
            available(stamp, at, p.max_age)
        if max(s.observed_at for s in stamps) - min(s.observed_at for s in stamps) > p.max_skew:
            raise ValueError("cycle books exceed synchronization bound")

        def estimate(size: Decimal) -> tuple[LegEstimate, ...]:
            output, legs = size, []
            for edge in edges:
                leg = walk_book(edge, output, self.rules.get(edge.symbol, MarketRules()), p.fee_rate, p.slippage_buffer)
                legs.append(leg)
                output = leg.received
            return tuple(legs)

        size, legs = budget, ()
        # Bounded size search is conservative, not an optimizer or profit guarantee.
        for _ in range(32):
            try:
                legs = estimate(size)
            except ValueError:
                legs = ()
                break
            if not any(leg.depth_exhausted for leg in legs):
                break
            size /= 2
        if not legs or any(leg.depth_exhausted for leg in legs):
            self._close(at, "DEPTH_OR_MARKET_RULES_UNSATISFIED")
            return []
        final = legs[-1].received + legs[0].residual_input
        gross_rate = ONE
        for edge in edges:
            gross_rate *= edge.mid_rate
        gross_return = gross_rate - ONE
        after_cost_return = final / size - ONE
        cost = max(ZERO, gross_return - after_cost_return)
        economics = ScenarioEconomics(gross_return, cost, p.non_atomic_penalty + p.residual_penalty,
            p.uncertainty_penalty, max(ZERO, ONE - (p.non_atomic_penalty + p.residual_penalty + p.uncertainty_penalty)))
        if economics.net_edge <= p.threshold:
            self._close(at, "NET_EDGE_BELOW_THRESHOLD")
            return []
        if self.open_since is None:
            self.open_since = at
        self.last_at = at
        failure_paths = []
        for i, leg in enumerate(legs):
            failure_paths.append({"failed_leg": i + 1, "scenario": "REJECTION_AFTER_PREVIOUS_LEGS",
                "stuck_currency": leg.edge.source, "stuck_amount": str(leg.input_amount),
                "empirical_probability": None})
            failure_paths.append({"failed_leg": i + 1, "scenario": "HALF_FILL",
                "stuck_input": str(leg.input_amount / 2), "received_output": str(leg.received / 2),
                "empirical_probability": None})
        self.diagnostics = {"status": "OPEN", "open_since": utc_text(self.open_since),
            "observed_duration_seconds": str(seconds(at - self.open_since)),
            "net_edge": str(economics.net_edge), "measurement": "SAMPLED_NOT_CONTINUOUS"}
        records = tuple(dict(r) for r in first_seen)
        plans = []
        for leg in legs:
            rules = self.rules.get(leg.edge.symbol, MarketRules())
            protection = leg.edge.book.asks[-1].price * (ONE + p.slippage_buffer) if leg.edge.side == "BUY" else leg.edge.book.bids[-1].price * (ONE - p.slippage_buffer)
            # Protection rounding is outward; submitted quantities are stepped.
            tick = rules.price_tick
            from decimal import ROUND_CEILING
            protection = (protection / tick).to_integral_value(rounding=ROUND_CEILING if leg.edge.side == "BUY" else "ROUND_DOWN") * tick
            plans.append({"symbol": leg.edge.symbol, "side": leg.edge.side,
                "quantity": str(leg.base_quantity), "limit_price": str(protection),
                "quantity_step": str(rules.quantity_step), "price_tick": str(tick),
                "min_notional": str(rules.min_notional),
                "source": leg.edge.source, "target": leg.edge.target})
        return [HypothesisCandidate("JAMALIAN_TRIANGLE_COMPLETION_V1", HypothesisFamily.TRIANGULAR_ARBITRAGE,
            edges[0].symbol, HypothesisSide.MULTI_LEG, at, at + p.execution_delay,
            ("POSITIVE_NONATOMIC_MODEL_SCENARIO",), stamps,
            {"cycle": list(p.cycle), "fee_rate": str(p.fee_rate), "slippage_buffer": str(p.slippage_buffer)},
            {"economics": economics.to_dict(), "cycle": list(p.cycle), "budget": str(size),
                "market_spread_fraction": str(max((e.book.asks[0].price - e.book.bids[0].price) / ((e.book.asks[0].price + e.book.bids[0].price) / 2) for e in edges)),
                "depth_slippage_fraction": str(max(l.depth_slippage_fraction for l in legs)),
                "start_currency": p.cycle[0], "final_currency_amount_scenario": str(final),
                "legs": [leg.to_dict() for leg in legs], "plan": plans, "failure_paths": failure_paths,
                "non_atomic": True, "historical_backfill_is_prospective": False,
                "prospective_evidence": len(records) == 3 and all(r.get("prospective_clean") for r in records),
                "first_seen": list(records), "opportunity": dict(self.diagnostics),
                "execution_authorized": False, "sizing_method": "BOUNDED_HALVING_CONSERVATIVE"})]

    def _close(self, at: datetime, reason: str) -> None:
        self.diagnostics = {"status": "CLOSED", "reason": reason,
            "observed_duration_seconds": str(seconds(at - self.open_since)) if self.open_since is not None else "0",
            "measurement": "SAMPLED_NOT_CONTINUOUS"}
        self.open_since, self.last_at = None, at
