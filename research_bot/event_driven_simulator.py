"""Offline crossing-book simulator with explicit inventory and order lifecycle.

This is a BACKTEST research primitive, never a running PAPER/LIVE service.
Passive queue priority, hidden liquidity and price impact are not modeled.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Mapping

from .contracts import EvidenceStamp
from .jamalian_data import OrderBook, available, aware, currencies, decimal_value


class OrderState(str, Enum):
    CREATED = "CREATED"
    WORKING = "WORKING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


TERMINAL = frozenset({OrderState.FILLED, OrderState.CANCELLED, OrderState.EXPIRED, OrderState.REJECTED})


@dataclass(frozen=True)
class SimOrder:
    client_order_id: str
    symbol: str
    side: str
    quantity: Decimal
    limit_price: Decimal  # MARKET uses this as a hard protection price.
    created_at: datetime
    order_type: str = "LIMIT"
    stop_price: Decimal | None = None
    expires_at: datetime | None = None
    quantity_step: Decimal = Decimal("0.00000001")
    price_tick: Decimal = Decimal("0.01")

    def __post_init__(self) -> None:
        currencies(self.symbol)
        aware(self.created_at)
        if not isinstance(self.client_order_id, str) or not self.client_order_id.strip():
            raise ValueError("client_order_id required")
        if self.side not in {"BUY", "SELL"} or self.order_type not in {"LIMIT", "MARKET", "STOP_LIMIT"}:
            raise ValueError("invalid side or order type")
        for name in ("quantity", "limit_price", "quantity_step", "price_tick"):
            object.__setattr__(self, name, decimal_value(getattr(self, name), positive=True))
        if self.quantity % self.quantity_step or self.limit_price % self.price_tick:
            raise ValueError("order violates supplied market increments")
        if self.order_type == "STOP_LIMIT":
            object.__setattr__(self, "stop_price", decimal_value(self.stop_price, positive=True))
            if self.stop_price % self.price_tick:
                raise ValueError("stop violates supplied market increment")
        elif self.stop_price is not None:
            raise ValueError("stop_price only applies to STOP_LIMIT")
        if self.expires_at is not None:
            aware(self.expires_at)
            if self.expires_at <= self.created_at:
                raise ValueError("expiry must follow creation")


@dataclass(frozen=True)
class OrderSnapshot:
    request: SimOrder
    state: OrderState
    filled_quantity: Decimal = Decimal("0")
    reserved: Decimal = Decimal("0")
    triggered: bool = False
    cancel_at: datetime | None = None


@dataclass(frozen=True)
class SimEvent:
    event_id: str
    client_order_id: str
    state: OrderState
    timestamp: datetime
    quantity: Decimal = Decimal("0")
    price: Decimal = Decimal("0")
    fee: Decimal = Decimal("0")
    reason: str = ""
    evidence: tuple[EvidenceStamp, ...] = ()


class EventDrivenExecutionSimulator:
    def __init__(self, balances: Mapping[str, Decimal], *, fee_rate=Decimal("0.001"),
                 slippage_rate=Decimal("0"), latency=timedelta(0),
                 cancel_latency=timedelta(0), max_book_age=timedelta(seconds=30)):
        self.fee_rate = decimal_value(fee_rate)
        self.slippage_rate = decimal_value(slippage_rate)
        if self.fee_rate >= 1 or self.slippage_rate >= 1:
            raise ValueError("fee and slippage rates must be below one")
        if latency < timedelta(0) or cancel_latency < timedelta(0) or max_book_age <= timedelta(0):
            raise ValueError("invalid latency or maximum age")
        self.latency, self.cancel_latency, self.max_book_age = latency, cancel_latency, max_book_age
        if any(not isinstance(k, str) or not k.isalnum() or k != k.upper() for k in balances):
            raise ValueError("balances must use uppercase asset codes")
        self._balances = {k: decimal_value(v) for k, v in balances.items()}
        self._orders: dict[str, OrderSnapshot] = {}
        self._events: list[SimEvent] = []
        self._clock: datetime | None = None
        self._books: dict[tuple, tuple] = {}

    @property
    def events(self) -> tuple[SimEvent, ...]:
        return tuple(self._events)

    def balances(self) -> dict[str, Decimal]:
        return dict(self._balances)

    def order(self, client_order_id: str) -> OrderSnapshot:
        return self._orders[client_order_id]

    def available_balance(self, asset: str) -> Decimal:
        reserved = sum((o.reserved for o in self._orders.values()
            if self._reserve_asset(o.request) == asset), Decimal("0"))
        return self._balances.get(asset, Decimal("0")) - reserved

    @staticmethod
    def _reserve_asset(request: SimOrder) -> str:
        base, quote = currencies(request.symbol)
        return quote if request.side == "BUY" else base

    def _record(self, order: OrderSnapshot, at: datetime, **kwargs) -> None:
        self._orders[order.request.client_order_id] = order
        self._events.append(SimEvent(str(len(self._events) + 1), order.request.client_order_id,
            order.state, at, **kwargs))

    def advance(self, at: datetime) -> None:
        aware(at)
        if self._clock is not None and at < self._clock:
            raise ValueError("events must be processed chronologically")
        self._clock = at
        pending = []
        for order in tuple(self._orders.values()):
            if order.state in TERMINAL:
                continue
            deadlines = []
            if order.request.expires_at is not None and order.request.expires_at <= at:
                deadlines.append((order.request.expires_at, OrderState.EXPIRED))
            if order.cancel_at is not None and order.cancel_at <= at:
                deadlines.append((order.cancel_at, OrderState.CANCELLED))
            if deadlines:
                deadline, state = min(deadlines, key=lambda item: (item[0], item[1].value))
                pending.append((deadline, order.request.client_order_id, state, order))
        for deadline, _, state, order in sorted(pending, key=lambda item: (item[0], item[1])):
            self._record(replace(order, state=state, reserved=Decimal("0")), deadline)

    def submit(self, request: SimOrder) -> OrderSnapshot:
        existing = self._orders.get(request.client_order_id)
        if existing is not None:
            if existing.request != request:
                raise ValueError("idempotency key reused with different order")
            return existing
        self.advance(request.created_at)
        reserved = request.quantity
        if request.side == "BUY":
            reserved *= request.limit_price * (1 + self.fee_rate)
        approved = reserved <= self.available_balance(self._reserve_asset(request))
        state = OrderState.CREATED if approved else OrderState.REJECTED
        snapshot = OrderSnapshot(request, state, reserved=reserved if approved else Decimal("0"))
        self._record(snapshot, request.created_at, reason="" if approved else "INSUFFICIENT_UNRESERVED_INVENTORY")
        return snapshot

    def cancel(self, client_order_id: str, at: datetime) -> OrderSnapshot:
        self.advance(at)
        order = self.order(client_order_id)
        if order.state in TERMINAL or order.cancel_at is not None:
            return order
        order = replace(order, state=OrderState.CANCEL_PENDING, cancel_at=at + self.cancel_latency)
        self._record(order, at)
        self.advance(at)
        return self.order(client_order_id)

    def on_book(self, book: OrderBook) -> tuple[SimEvent, ...]:
        at = book.stamp.available_at
        available(book.stamp, at, self.max_book_age)
        identity = (book.symbol, book.stamp.source, book.stamp.observed_at)
        payload = (book.bids, book.asks, book.stamp.lineage)
        if identity in self._books:
            if self._books[identity] != payload:
                raise ValueError("revision to a consumed book identity")
            return ()
        self.advance(at)
        self._books[identity] = payload
        first_event = len(self._events)
        liquidity = {"BUY": [[x.price, x.quantity] for x in book.asks],
                     "SELL": [[x.price, x.quantity] for x in book.bids]}
        orders = sorted(self._orders.values(), key=lambda o: (o.request.created_at, o.request.client_order_id))
        for original in orders:
            order = self.order(original.request.client_order_id)
            req = order.request
            if req.symbol != book.symbol or order.state in TERMINAL:
                continue
            if book.stamp.observed_at <= req.created_at or book.stamp.observed_at < req.created_at + self.latency:
                continue
            if req.order_type == "STOP_LIMIT" and not order.triggered:
                trigger_price = book.asks[0].price if req.side == "BUY" else book.bids[0].price
                triggered = trigger_price >= req.stop_price if req.side == "BUY" else trigger_price <= req.stop_price
                if not triggered:
                    continue
                order = replace(order, triggered=True)
            if order.state == OrderState.CREATED:
                order = replace(order, state=OrderState.WORKING)
                self._record(order, at, evidence=(book.stamp,))
            base, quote = currencies(req.symbol)
            for level in liquidity[req.side]:
                raw_price, remaining_depth = level
                price = raw_price * (1 + self.slippage_rate if req.side == "BUY" else 1 - self.slippage_rate)
                crosses = price <= req.limit_price if req.side == "BUY" else price >= req.limit_price
                if not crosses:
                    break
                quantity = min(req.quantity - order.filled_quantity, remaining_depth)
                quantity = (quantity // req.quantity_step) * req.quantity_step
                if quantity <= 0:
                    continue
                fee = price * quantity * self.fee_rate  # fee charged in quote currency
                if req.side == "BUY":
                    self._balances[quote] -= price * quantity + fee
                    self._balances[base] = self._balances.get(base, Decimal("0")) + quantity
                else:
                    self._balances[base] -= quantity
                    self._balances[quote] = self._balances.get(quote, Decimal("0")) + price * quantity - fee
                level[1] -= quantity
                filled = order.filled_quantity + quantity
                reserved = (req.quantity - filled)
                if req.side == "BUY":
                    reserved *= req.limit_price * (1 + self.fee_rate)
                state = OrderState.FILLED if filled == req.quantity else OrderState.PARTIALLY_FILLED
                if order.cancel_at is not None and state != OrderState.FILLED:
                    state = OrderState.CANCEL_PENDING
                order = replace(order, state=state, filled_quantity=filled, reserved=reserved)
                self._record(order, at, quantity=quantity, price=price, fee=fee, evidence=(book.stamp,))
                if state == OrderState.FILLED:
                    break
            # Persist a stop trigger even when no executable depth is available.
            self._orders[req.client_order_id] = order
        return tuple(self._events[first_event:])
