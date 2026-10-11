from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from hashlib import sha256
from typing import Iterator, Mapping

from ...hypothesis import canonical_json, utc_text
from ...jamalian_data import OrderBook, available, aware, currencies, decimal_value
from ..journal import TradeJournal
from ..models import CompletionPolicy, MarketRules, ZERO, ONE, floor_step, Inventory
from ...contracts import EvidenceStamp


class OrderState(str, Enum):
    CREATED = "CREATED"
    APPROVED = "APPROVED"
    SUBMITTED_SIMULATED = "SUBMITTED_SIMULATED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


TERMINAL = {OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED, OrderState.EXPIRED}


@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str
    symbol: str
    side: str
    quantity: Decimal
    protection_price: Decimal
    created_at: datetime
    expires_at: datetime | None = None
    rules: MarketRules = MarketRules()
    not_before: datetime | None = None
    strategy_id: str = ""
    additional_impact_bps: Decimal = ZERO

    def __post_init__(self) -> None:
        currencies(self.symbol)
        object.__setattr__(self, "additional_impact_bps", decimal_value(self.additional_impact_bps))
        if self.additional_impact_bps > Decimal(25):
            raise ValueError("additional impact cannot exceed the unchanged slippage ceiling")
        aware(self.created_at)
        if not self.client_order_id.strip() or self.side not in {"BUY", "SELL"}:
            raise ValueError("order identity/side invalid")
        for name in ("quantity", "protection_price"):
            object.__setattr__(self, name, decimal_value(getattr(self, name), positive=True))
        if self.quantity % self.rules.quantity_step or self.protection_price % self.rules.price_tick:
            raise ValueError("order violates tick/lot")
        if self.quantity * self.protection_price < self.rules.min_notional:
            raise ValueError("order below minimum notional")
        if self.expires_at is not None:
            aware(self.expires_at)
            if self.expires_at <= self.created_at:
                raise ValueError("expiry must follow creation")
        if self.not_before is not None:
            aware(self.not_before)
            if self.not_before < self.created_at:
                raise ValueError("execution bound precedes creation")

    def to_dict(self) -> dict:
        return {"client_order_id": self.client_order_id, "symbol": self.symbol, "side": self.side,
            "quantity": str(self.quantity), "protection_price": str(self.protection_price),
            "created_at": utc_text(self.created_at), "expires_at": utc_text(self.expires_at) if self.expires_at else None,
            "rules": {k: str(v) for k, v in self.rules.__dict__.items()},
            "not_before": utc_text(self.not_before) if self.not_before else None, "strategy_id": self.strategy_id,
            "additional_impact_bps": str(self.additional_impact_bps)}

    @classmethod
    def from_dict(cls, row: dict) -> OrderRequest:
        return cls(row["client_order_id"], row["symbol"], row["side"], Decimal(row["quantity"]),
            Decimal(row["protection_price"]), datetime.fromisoformat(row["created_at"]),
            datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None, MarketRules(**row["rules"]),
            datetime.fromisoformat(row["not_before"]) if row.get("not_before") else None, row.get("strategy_id", ""),
            decimal_value(row.get("additional_impact_bps", "0")))


@dataclass(frozen=True)
class OrderSnapshot:
    request: OrderRequest
    state: OrderState
    filled_quantity: Decimal
    reserved: Decimal
    fills: tuple[dict, ...]
    cancel_at: datetime | None


class EventDrivenExecutionSimulator:
    def __init__(self, balances: Mapping[str, Decimal], journal: TradeJournal,
                 policy: CompletionPolicy | None = None, resume: bool = True) -> None:
        self.policy, self.journal = policy or CompletionPolicy(), journal
        self._balances = {k: decimal_value(v) for k, v in balances.items()}
        if any(not k.isalnum() or k.upper() != k for k in self._balances):
            raise ValueError("uppercase balance currencies required")
        self._initial_balances = dict(self._balances)
        self._orders: dict[str, dict] = {}
        self._scheduled: list[dict] = []
        self._books: dict[str, dict] = {}
        self._clock: datetime | None = None
        self._next_submission_at: datetime | None = None
        self._sequence = 0
        checkpoint = journal.restore("simulator")
        self.graduation = journal.restore("graduation")
        if self.policy.mode == "PAPER" and self.policy.purpose == "GRADUATED_RESEARCH" and not (self.graduation and self.graduation.get("paper_allowed") is True):
            raise ValueError("research PAPER requires a successful journaled graduation gate")
        if (self.policy.mode == "PAPER" and self.policy.purpose == "GRADUATED_RESEARCH"
                and self.graduation.get("execution_model_sha256") != self.policy.execution_model_sha256()):
            raise ValueError("graduated execution model does not match PAPER policy")
        if checkpoint is not None:
            if not resume:
                raise ValueError("existing journal requires explicit resume or a new experiment")
            if checkpoint["policy"] != self.policy.to_dict() or checkpoint["initial_balances"] != {k: str(v) for k, v in self._initial_balances.items()}:
                raise ValueError("cannot resume with different policy or initial balances")
            self._load(checkpoint)

    def _dump(self) -> dict:
        return {"policy": self.policy.to_dict(), "initial_balances": {k: str(v) for k, v in self._initial_balances.items()},
            "balances": {k: str(v) for k, v in self._balances.items()}, "orders": self._orders,
            "scheduled": self._scheduled, "books": self._books,
            "clock": utc_text(self._clock) if self._clock else None, "sequence": self._sequence,
            "next_submission_at": utc_text(self._next_submission_at) if self._next_submission_at else None}

    def _load(self, row: dict) -> None:
        self._balances = {k: Decimal(v) for k, v in row["balances"].items()}
        self._orders, self._scheduled, self._books = row["orders"], row["scheduled"], row["books"]
        self._clock = datetime.fromisoformat(row["clock"]) if row["clock"] else None
        self._sequence = row["sequence"]
        self._next_submission_at = datetime.fromisoformat(row["next_submission_at"]) if row.get("next_submission_at") else None

    @contextmanager
    def atomic(self) -> Iterator[None]:
        import json
        before = json.loads(canonical_json(self._dump()))
        try:
            with self.journal.atomic():
                yield
                self.journal.checkpoint("simulator", self._dump())
        except BaseException:
            self._load(before)
            raise

    def balances(self) -> dict[str, Decimal]:
        return dict(self._balances)

    def free(self, currency: str) -> Decimal:
        reserved = sum((Decimal(o["reserved"]) for o in self._orders.values() if o["reserve_currency"] == currency), ZERO)
        return self._balances.get(currency, ZERO) - reserved

    def inventory(self, at: datetime) -> Inventory:
        aware(at)
        if self._clock is not None and at < self._clock:
            raise ValueError("cannot timestamp current inventory in the past")
        reserved = {k: v - self.free(k) for k, v in self._balances.items()}
        digest = sha256(canonical_json([self._sequence, self._balances, reserved]).encode()).hexdigest()
        return Inventory(self._balances, reserved, EvidenceStamp("completion_simulator", at, at, lineage=digest))

    def order(self, client_order_id: str) -> OrderSnapshot:
        import json
        o = self._orders[client_order_id]
        return OrderSnapshot(OrderRequest.from_dict(o["request"]), OrderState(o["state"]), Decimal(o["filled_quantity"]),
            Decimal(o["reserved"]), tuple(json.loads(canonical_json(o["fills"]))),
            datetime.fromisoformat(o["cancel_at"]) if o["cancel_at"] else None)

    def orders(self) -> tuple[OrderSnapshot, ...]:
        return tuple(self.order(identity) for identity in sorted(self._orders))

    def _transition(self, identity: str, state: OrderState, at: datetime, reason: str = "", fill: dict | None = None) -> None:
        o = self._orders[identity]
        o["state"] = state.value
        if state is OrderState.ACKNOWLEDGED:
            o["acknowledged_at"] = utc_text(at)
        if state in TERMINAL:
            o["reserved"] = "0"
        self._sequence += 1
        self.journal.append(f"sim:{self._sequence}", "ORDER_TRANSITION", at,
            {"client_order_id": identity, "state": state.value, "reason": reason,
                "filled_quantity": o["filled_quantity"], "reserved": o["reserved"], "request": o["request"],
                "balances": {k: str(v) for k, v in self._balances.items()}, "fill": fill,
                "mode": self.policy.mode, "live_execution": False,
                "execution_model_sha256": self.policy.execution_model_sha256(),
                "displayed_depth_fraction": str(self.policy.displayed_depth_fraction),
                "min_submission_interval_ms": self.policy.min_submission_interval_ms,
                "model_status": "UNCALIBRATED_EXECUTION_ASSUMPTIONS"})

    def _schedule(self, identity: str, state: OrderState, at: datetime) -> None:
        priority = 0 if state in {OrderState.CANCELLED, OrderState.EXPIRED} else 2 if state is OrderState.ACKNOWLEDGED else 1
        self._scheduled.append({"id": identity, "state": state.value, "at": utc_text(at), "priority": priority})

    def advance(self, at: datetime) -> None:
        aware(at)
        if self._clock is not None and at < self._clock:
            raise ValueError("simulation time cannot move backwards")
        with self.atomic():
            while True:
                self._scheduled.sort(key=lambda e: (datetime.fromisoformat(e["at"]), e["priority"], e["id"], e["state"]))
                if not self._scheduled or datetime.fromisoformat(self._scheduled[0]["at"]) > at:
                    break
                event = self._scheduled.pop(0)
                identity, target = event["id"], OrderState(event["state"])
                if OrderState(self._orders[identity]["state"]) in TERMINAL:
                    continue
                event_at = datetime.fromisoformat(event["at"])
                if target is OrderState.SUBMITTED_SIMULATED:
                    if self._next_submission_at is not None and event_at < self._next_submission_at:
                        self.journal.append("submission_delay:" + identity + ":" + utc_text(event_at),
                            "SIMULATED_RATE_LIMIT", event_at,
                            {"client_order_id": identity, "retry_at": utc_text(self._next_submission_at),
                                "reason": "MODELED_VENUE_SUBMISSION_PACING", "live_execution": False})
                        self._schedule(identity, target, self._next_submission_at)
                        continue
                    self._next_submission_at = event_at + timedelta(milliseconds=self.policy.min_submission_interval_ms)
                    self._schedule(identity, OrderState.ACKNOWLEDGED,
                        event_at + timedelta(milliseconds=self.policy.ack_latency_ms))
                self._transition(identity, target, event_at)
            self._clock = at

    def submit(self, request: OrderRequest, approved: bool) -> OrderSnapshot:
        identity = request.client_order_id
        if identity in self._orders:
            if self._orders[identity]["request"] != request.to_dict():
                raise ValueError("client_order_id reused with different order")
            return self.order(identity)
        with self.atomic():
            self.advance(request.created_at)
            base, quote = currencies(request.symbol)
            reserve_currency = quote if request.side == "BUY" else base
            reservation = request.quantity * request.protection_price if request.side == "BUY" else request.quantity
            free = self.free(reserve_currency)
            self._orders[identity] = {"request": request.to_dict(), "state": "CREATED", "filled_quantity": "0",
                "reserved": "0", "reserve_currency": reserve_currency, "fills": [], "cancel_at": None,
                "acknowledged_at": None}
            self._transition(identity, OrderState.CREATED, request.created_at)
            reason = ""
            if approved is not True:
                reason = "RISK_OR_DECISION_REJECTED"
            elif self.policy.slippage_rate * Decimal(10000) + request.additional_impact_bps > Decimal(25):
                reason = "TOTAL_SLIPPAGE_EXCEEDS_UNCHANGED_LIMIT"
            elif self.policy.mode == "PAPER" and self.policy.purpose == "GRADUATED_RESEARCH" and request.strategy_id not in self.graduation.get("eligible_strategy_ids", ()):
                reason = "STRATEGY_NOT_GRADUATED"
            elif request.quantity * request.protection_price > self.policy.max_order_notional:
                reason = "MAX_ORDER_NOTIONAL"
            elif reservation > free:
                reason = "INSUFFICIENT_UNRESERVED_BALANCE"
            if reason:
                self._transition(identity, OrderState.REJECTED, request.created_at, reason)
            else:
                self._orders[identity]["reserved"] = str(reservation)
                self._transition(identity, OrderState.APPROVED, request.created_at)
                jitter = int(sha256(f"{self.policy.seed}:{identity}".encode()).hexdigest(), 16) % (self.policy.jitter_ms + 1)
                submitted = request.created_at + timedelta(milliseconds=self.policy.latency_ms + jitter)
                self._schedule(identity, OrderState.SUBMITTED_SIMULATED, submitted)
                if request.expires_at is not None:
                    self._schedule(identity, OrderState.EXPIRED, request.expires_at)
        return self.order(identity)

    def cancel(self, identity: str, at: datetime) -> OrderSnapshot:
        with self.atomic():
            self.advance(at)
            o = self._orders[identity]
            if OrderState(o["state"]) not in TERMINAL and not o["cancel_at"]:
                cancel_at = at + timedelta(milliseconds=self.policy.cancel_latency_ms)
                o["cancel_at"] = utc_text(cancel_at)
                self._sequence += 1
                self.journal.append(f"sim:{self._sequence}", "CANCEL_REQUESTED", at,
                    {"client_order_id": identity, "ack_at": o["cancel_at"]})
                self._schedule(identity, OrderState.CANCELLED, cancel_at)
                self.advance(at)
        return self.order(identity)

    def on_book(self, book: OrderBook, at: datetime | None = None) -> tuple[dict, ...]:
        at = at or book.stamp.available_at
        available(book.stamp, at, self.policy.max_book_age)
        key = book.symbol + ":" + utc_text(book.stamp.observed_at)
        digest = sha256(canonical_json([[str(l.price), str(l.quantity)] for l in (*book.bids, *book.asks)]).encode()).hexdigest()
        if key in self._books:
            if self._books[key]["digest"] != digest:
                raise ValueError("previously consumed book revised")
            self.advance(at)
            return ()
        fills = []
        with self.atomic():
            self.advance(at)
            # One conservative eligible budget per level/snapshot, shared by all
            # orders. Retries do not replenish it. This is not a maker queue model.
            depth = {"BUY": [[l.price, l.quantity * self.policy.displayed_depth_fraction] for l in book.asks],
                "SELL": [[l.price, l.quantity * self.policy.displayed_depth_fraction] for l in book.bids]}
            for identity, o in sorted(self._orders.items(), key=lambda kv: (kv[1]["request"]["created_at"], kv[0])):
                request = OrderRequest.from_dict(o["request"])
                if request.symbol != book.symbol or o["state"] not in {"ACKNOWLEDGED", "PARTIALLY_FILLED"} or book.stamp.observed_at <= request.created_at:
                    continue
                eligible_at = datetime.fromisoformat(o["acknowledged_at"])
                if request.not_before is not None:
                    eligible_at = max(eligible_at, request.not_before)
                if book.stamp.observed_at < eligible_at:
                    continue
                base, quote = currencies(request.symbol)
                for level in depth[request.side]:
                    remaining = request.quantity - Decimal(o["filled_quantity"])
                    if remaining == ZERO:
                        break
                    adverse_rate = self.policy.slippage_rate + request.additional_impact_bps / Decimal(10000)
                    price = level[0] * (ONE + adverse_rate if request.side == "BUY" else ONE - adverse_rate)
                    if (request.side == "BUY" and price > request.protection_price) or (request.side == "SELL" and price < request.protection_price):
                        break
                    quantity = floor_step(min(remaining, level[1]), request.rules.quantity_step)
                    if quantity == ZERO:
                        continue
                    received_currency = base if request.side == "BUY" else quote
                    gross_received = quantity if request.side == "BUY" else quantity * price
                    fee = gross_received * self.policy.fee_rate
                    if request.side == "BUY":
                        self._balances[quote] -= quantity * price
                        self._balances[base] = self._balances.get(base, ZERO) + quantity - fee
                    else:
                        self._balances[base] -= quantity
                        self._balances[quote] = self._balances.get(quote, ZERO) + quantity * price - fee
                    level[1] -= quantity
                    o["filled_quantity"] = str(Decimal(o["filled_quantity"]) + quantity)
                    o["reserved"] = str((request.quantity - Decimal(o["filled_quantity"])) * (request.protection_price if request.side == "BUY" else ONE))
                    fill = {"client_order_id": identity, "symbol": request.symbol, "side": request.side,
                        "quantity": str(quantity), "price": str(price), "fee": str(fee), "fee_currency": received_currency,
                        "received": str(gross_received - fee), "received_currency": received_currency,
                        "timestamp": utc_text(at), "slippage_quote": str(abs(price - level[0]) * quantity),
                        "additional_impact_bps": str(request.additional_impact_bps),
                        "additional_impact_quote": str(level[0] * quantity * request.additional_impact_bps / Decimal(10000))}
                    o["fills"].append(fill)
                    fills.append(dict(fill))
                    state = OrderState.FILLED if Decimal(o["filled_quantity"]) == request.quantity else OrderState.PARTIALLY_FILLED
                    self._transition(identity, state, at, fill=fill)
            self._books[key] = {"digest": digest}
        return tuple(fills)
