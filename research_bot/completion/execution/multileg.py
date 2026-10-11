"""Sequential simulated cycles, residual inventory and bounded emergency unwind."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_CEILING
from typing import Sequence
from contextlib import contextmanager
from typing import Iterator
import copy

from ...hypothesis import HypothesisCandidate, HypothesisSide, utc_text
from ...jamalian_data import OrderBook, currencies
from ..models import MarketRules, ZERO, ONE, floor_step
from .event_driven_simulator import EventDrivenExecutionSimulator, OrderRequest, OrderState, TERMINAL


class SequentialCycleSimulator:
    def __init__(self, simulator: EventDrivenExecutionSimulator, timeout: timedelta = timedelta(seconds=5)) -> None:
        if timeout <= timedelta(0):
            raise ValueError("positive cycle timeout required")
        self.simulator, self.timeout = simulator, timeout
        self.cycles: dict[str, dict] = simulator.journal.restore("cycles") or {}

    def _persist(self) -> None:
        self.simulator.journal.checkpoint("cycles", self.cycles)

    @contextmanager
    def _atomic(self) -> Iterator[None]:
        before = copy.deepcopy(self.cycles)
        try:
            with self.simulator.atomic():
                yield
                self._persist()
        except BaseException:
            self.cycles = before
            raise

    def start(self, candidate: HypothesisCandidate, at: datetime, approved: bool) -> str:
        with self._atomic():
            return self._start(candidate, at, approved)

    def _start(self, candidate: HypothesisCandidate, at: datetime, approved: bool) -> str:
        if candidate.side is not HypothesisSide.MULTI_LEG:
            raise ValueError("multi-leg hypothesis required")
        identity = candidate.hypothesis_id
        if identity in self.cycles:
            return identity
        plans = [dict(p) for p in candidate.metadata["plan"]]
        self.cycles[identity] = {"plan": plans, "index": 0, "orders": [], "status": "RUNNING",
            "start_currency": candidate.metadata["start_currency"], "flows": {}, "seen_fills": {},
            "strategy_id": candidate.strategy_id, "not_before": utc_text(candidate.earliest_executable)}
        self._submit_leg(identity, at, approved, None)
        self._persist()
        return identity

    def _submit_leg(self, identity: str, at: datetime, approved: bool, available_input: Decimal | None) -> None:
        cycle = self.cycles[identity]
        i, plan = cycle["index"], cycle["plan"][cycle["index"]]
        rules = MarketRules(plan["quantity_step"], plan["price_tick"], plan.get("min_notional", "0"))
        quantity, protection = Decimal(plan["quantity"]), Decimal(plan["limit_price"])
        if available_input is not None:
            quantity = floor_step(min(quantity, available_input / protection if plan["side"] == "BUY" else available_input), rules.quantity_step)
        if quantity <= ZERO:
            cycle["status"] = "FAILED_DUST"
            return
        if quantity * protection < rules.min_notional:
            cycle["status"] = "FAILED_MIN_NOTIONAL"
            return
        order_id = f"{identity}:leg:{i}"
        request = OrderRequest(order_id, plan["symbol"], plan["side"], quantity, protection, at, at + self.timeout,
            rules, datetime.fromisoformat(cycle["not_before"]) if i == 0 else at, cycle["strategy_id"])
        self.simulator.submit(request, approved)
        cycle["orders"].append(order_id)

    def advance(self, at: datetime) -> None:
        with self._atomic():
            self._advance(at)

    def _advance(self, at: datetime) -> None:
        self.simulator.advance(at)
        for identity, cycle in self.cycles.items():
            for tracked_id in cycle["orders"] + cycle.get("unwind_orders", []):
                tracked = self.simulator.order(tracked_id)
                self._account(cycle, tracked_id)
                if tracked_id in cycle.get("unwind_orders", []) and tracked.state in TERMINAL and tracked_id not in cycle.setdefault("unwind_done", []):
                    cycle["unwind_done"].append(tracked_id)
                    self.simulator.journal.append(tracked_id + ":outcome", "UNWIND_OUTCOME", at,
                        {"state": tracked.state.value, "residual_inventory": self.residual(identity)})
            if cycle["status"] != "RUNNING":
                continue
            order_id = cycle["orders"][-1]
            order = self.simulator.order(order_id)
            if order.state not in TERMINAL:
                continue
            if order.state is not OrderState.FILLED:
                cycle["status"] = "FAILED_" + order.state.value
            elif cycle["index"] == len(cycle["plan"]) - 1:
                cycle["status"] = "COMPLETED"
            else:
                received = sum((Decimal(f["received"]) for f in order.fills), ZERO)
                cycle["index"] += 1
                self._submit_leg(identity, at, True, received)
            if cycle["status"] != "RUNNING":
                self.simulator.journal.append(identity + ":outcome", "CYCLE_OUTCOME", at,
                    {"cycle_id": identity, "status": cycle["status"], "flows": cycle["flows"],
                        "residual_inventory": self.residual(identity), "non_atomic": True})
        self._persist()

    def _account(self, cycle: dict, order_id: str) -> None:
        order = self.simulator.order(order_id)
        base, quote = currencies(order.request.symbol)
        previous = cycle["seen_fills"].get(order_id, 0)
        for fill in order.fills[previous:]:
            spent_currency = quote if fill["side"] == "BUY" else base
            spent = Decimal(fill["quantity"]) * Decimal(fill["price"]) if fill["side"] == "BUY" else Decimal(fill["quantity"])
            flows = cycle["flows"]
            flows[spent_currency] = str(Decimal(flows.get(spent_currency, "0")) - spent)
            received_currency = fill["received_currency"]
            flows[received_currency] = str(Decimal(flows.get(received_currency, "0")) + Decimal(fill["received"]))
        cycle["seen_fills"][order_id] = len(order.fills)

    def residual(self, identity: str) -> dict[str, str]:
        cycle = self.cycles[identity]
        return {currency: value for currency, value in cycle["flows"].items()
                if currency != cycle["start_currency"] and Decimal(value) > ZERO}

    def emergency_unwind(self, identity: str, books: Sequence[OrderBook], at: datetime) -> tuple[str, ...]:
        with self._atomic():
            return self._unwind(identity, books, at)

    def _unwind(self, identity: str, books: Sequence[OrderBook], at: datetime) -> tuple[str, ...]:
        cycle = self.cycles[identity]
        if cycle["status"] == "RUNNING":
            raise ValueError("terminate outstanding legs before unwinding")
        if cycle.get("unwind_orders"):
            return tuple(cycle["unwind_orders"])
        start = cycle["start_currency"]
        orders = []
        for asset, amount in self.residual(identity).items():
            route = next((b for b in books if set(currencies(b.symbol)) == {asset, start}), None)
            if route is None:
                self.simulator.journal.append(identity + ":unwind_missing:" + asset, "UNWIND_REJECTED", at,
                    {"asset": asset, "reason": "NO_DIRECT_LIQUIDATION_BOOK"})
                continue
            base, quote = currencies(route.symbol)
            buy = asset == quote
            plan = next((p for p in cycle["plan"] if p["symbol"] == route.symbol), {})
            rules = MarketRules(plan.get("quantity_step", "0.00000001"), plan.get("price_tick", "0.01"), plan.get("min_notional", "0"))
            limit = route.asks[-1].price * (ONE + self.simulator.policy.slippage_rate) if buy else route.bids[-1].price * (ONE - self.simulator.policy.slippage_rate)
            limit = (limit / rules.price_tick).to_integral_value(rounding=ROUND_CEILING if buy else "ROUND_DOWN") * rules.price_tick
            available = min(Decimal(amount), self.simulator.free(asset))
            quantity = floor_step(available / limit if buy else available, rules.quantity_step)
            if quantity <= ZERO:
                continue
            if quantity * limit < rules.min_notional:
                self.simulator.journal.append(identity + ":unwind_minimum:" + asset, "UNWIND_REJECTED", at,
                    {"asset": asset, "reason": "RESIDUAL_BELOW_MIN_NOTIONAL"})
                continue
            order_id = identity + ":unwind:" + asset
            self.simulator.submit(OrderRequest(order_id, route.symbol, "BUY" if buy else "SELL", quantity, limit, at,
                at + self.timeout, rules, at, cycle["strategy_id"]), True)
            orders.append(order_id)
            self.simulator.journal.append(order_id + ":requested", "UNWIND_REQUESTED", at,
                {"cycle_id": identity, "residual_currency": asset, "amount": str(available), "not_guaranteed": True})
        cycle["unwind_orders"] = orders
        self._persist()
        return tuple(orders)

    def failure_statistics(self) -> dict:
        outcomes = self.simulator.journal.events("CYCLE_OUTCOME")
        states = [e["payload"]["status"] for e in outcomes]
        return {"observed_closed_cycles": len(states), "completed": states.count("COMPLETED"),
            "failed": sum(s != "COMPLETED" for s in states), "counts": {s: states.count(s) for s in sorted(set(states))},
            "interpretation": "SIMULATION_COUNTS_NOT_MARKET_FAILURE_PROBABILITIES"}
