"""Offline event replay only: no exchange, network, paper or live transport.

ENGINEERING_EXTENSION. Acknowledgement is never a fill. Limit fills require
explicit external queue/trade evidence; an OHLC touch is deliberately unusable.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any, Literal

from .contracts import (
    BookSnapshot,
    MarketMetadata,
    UnsupportedCapability,
    aware,
    decimal,
    digest,
)

ZERO = Decimal(0)
TERMINAL = frozenset({"FILLED", "CANCELLED", "REJECTED", "EXPIRED"})


def floor_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


@dataclass(frozen=True)
class SimOrderRequest:
    order_id: str
    market: MarketMetadata
    side: Literal["BUY", "SELL"]
    quantity: Decimal
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    limit_price: Decimal | None = None
    fee_rate: Decimal = ZERO
    fee_currency: str = "OUTPUT"
    latency: timedelta = timedelta(0)

    def __post_init__(self) -> None:
        if not self.order_id or self.side not in {"BUY", "SELL"}:
            raise ValueError("INVALID_SIMULATED_ORDER")
        decimal(self.quantity, positive=True)
        decimal(self.fee_rate)
        if self.fee_rate >= 1 or self.latency < timedelta(0):
            raise ValueError("INVALID_FEE_OR_LATENCY")
        if self.quantity % self.market.step_size:
            raise ValueError("ORDER_PRECISION_MISMATCH")
        if self.order_type not in {"MARKET", "LIMIT"}:
            raise UnsupportedCapability("SIMULATOR_MARKET_LIMIT_ONLY")
        if self.order_type == "LIMIT":
            if self.limit_price is None:
                raise ValueError("LIMIT_PRICE_REQUIRED")
            self.market.validate_order(self.quantity, self.limit_price)
        elif self.limit_price is not None:
            raise ValueError("MARKET_LIMIT_AMBIGUITY")
        if self.fee_currency not in {"OUTPUT", self.market.quote}:
            raise UnsupportedCapability("FEE_CURRENCY_OUTPUT_OR_QUOTE_ONLY")


@dataclass(frozen=True)
class SimEvent:
    event_id: str
    order_id: str
    timestamp: datetime
    state: str
    details: dict[str, Any]


class OfflineOrderSimulator:
    """Conservatively reserve inventory, deplete snapshot depth, persist replay.

    It models a finite offline wallet, not a second trading Risk Engine. Caller
    must explicitly supply independent risk approval. All state is local.
    """
    mode = "OFFLINE_REPLAY_NOT_PAPER"

    def __init__(self, balances: dict[str, Decimal]) -> None:
        if not balances or any(not currency for currency in balances):
            raise ValueError("INVALID_WALLET")
        self.balances = {k: decimal(v) for k, v in balances.items()}
        self.reserved = {k: ZERO for k in balances}
        self.orders: dict[str, dict[str, Any]] = {}
        self.events: list[SimEvent] = []
        self.consumed: dict[str, Decimal] = {}
        self.witnesses: set[str] = set()
        self.book_versions: dict[str, str] = {}

    def _check_time(self, now: datetime) -> None:
        aware(now)
        if self.events and now < self.events[-1].timestamp:
            raise ValueError("NONMONOTONIC_REPLAY_TIME")

    @staticmethod
    def _validate_book(book: BookSnapshot, now: datetime) -> None:
        book.validate_at(now)
        if any(price % book.market.tick_size for price, _ in book.bids + book.asks):
            raise ValueError("BOOK_PRICE_TICK_MISMATCH")

    def _book_identity(self, book: BookSnapshot) -> str:
        identity = digest((book.market.venue, book.market.symbol, book.data_version, book.sequence_id))
        content = digest((book.source_hash, book.exchange_timestamp, book.bids, book.asks))
        if identity in self.book_versions and self.book_versions[identity] != content:
            raise ValueError("IMMUTABLE_BOOK_SEQUENCE_CONTENT_COLLISION")
        self.book_versions[identity] = content
        return identity

    def available(self, currency: str) -> Decimal:
        return self.balances.get(currency, ZERO) - self.reserved.get(currency, ZERO)

    def _event(self, order_id: str, state: str, now: datetime, **details: Any) -> None:
        aware(now)
        if self.events and now < self.events[-1].timestamp:
            raise ValueError("NONMONOTONIC_REPLAY_TIME")
        event_id = digest((len(self.events), order_id, state, now, details))
        self.events.append(SimEvent(event_id, order_id, now, state, details))
        self.orders[order_id]["state"] = state

    def create(self, request: SimOrderRequest, now: datetime) -> str:
        self._check_time(now)
        if request.order_id in self.orders:
            if self.orders[request.order_id]["request"] != request:
                raise ValueError("ORDER_ID_CONTENT_COLLISION")
            return self.orders[request.order_id]["state"]
        if self.events and now < self.events[-1].timestamp:
            raise ValueError("NONMONOTONIC_REPLAY_TIME")
        self.orders[request.order_id] = {"request": request, "state": "CREATED", "created_at": now,
                                       "filled": ZERO, "notional": ZERO, "fees": {},
                                       "reservation_currency": None, "reservation": ZERO,
                                       "reservation_unit": ZERO, "net_output": ZERO}
        self._event(request.order_id, "CREATED", now)
        return "CREATED"

    def approve(self, order_id: str, book: BookSnapshot, now: datetime, *, risk_approved: bool) -> str:
        aware(now)
        if self.events and now < self.events[-1].timestamp:
            raise ValueError("NONMONOTONIC_REPLAY_TIME")
        order = self.orders[order_id]
        if order["state"] != "CREATED":
            return str(order["state"])
        if risk_approved is not True:
            self._event(order_id, "REJECTED", now, reason="INDEPENDENT_RISK_APPROVAL_REQUIRED")
            return "REJECTED"
        request: SimOrderRequest = order["request"]
        self._validate_book(book, now)
        if book.market != request.market:
            raise ValueError("MARKET_MISMATCH")
        # Worst visible ask reserves against depth, never an assumed midprice.
        price = request.limit_price or (book.asks[-1][0] if request.side == "BUY" else book.bids[0][0])
        if request.quantity * price < request.market.minimum_notional:
            self._event(order_id, "REJECTED", now, reason="MINIMUM_NOTIONAL_BREACH")
            return "REJECTED"
        currency = request.market.quote if request.side == "BUY" else request.market.base
        unit = price if request.side == "BUY" else Decimal(1)
        if request.side == "BUY" and request.fee_currency == request.market.quote:
            unit *= 1 + request.fee_rate
        reservation = request.quantity * unit
        if self.available(currency) < reservation:
            self._event(order_id, "REJECTED", now, reason="INSUFFICIENT_BALANCE")
            return "REJECTED"
        self.reserved[currency] = self.reserved.get(currency, ZERO) + reservation
        order.update(reservation_currency=currency, reservation=reservation, reservation_unit=unit)
        self._event(order_id, "APPROVED", now, reservation=reservation, currency=currency)
        return "APPROVED"

    def submit(self, order_id: str, now: datetime) -> str:
        aware(now)
        if self.events and now < self.events[-1].timestamp:
            raise ValueError("NONMONOTONIC_REPLAY_TIME")
        order = self.orders[order_id]
        if order["state"] != "APPROVED":
            return str(order["state"])
        if now < order["created_at"]:
            raise ValueError("NONMONOTONIC_REPLAY_TIME")
        order["executable_at"] = now + order["request"].latency
        self._event(order_id, "SUBMITTED_SIMULATED", now)
        self._event(order_id, "ACKNOWLEDGED", now)
        return "ACKNOWLEDGED"

    def fill(self, order_id: str, book: BookSnapshot, now: datetime, *,
             queue_evidence_id: str | None = None, queue_fill_quantity: Decimal | None = None) -> str:
        self._check_time(now)
        order = self.orders[order_id]
        if order["state"] in TERMINAL:
            return str(order["state"])
        if order["state"] not in {"ACKNOWLEDGED", "PARTIALLY_FILLED"}:
            raise ValueError("FILL_REQUIRES_ACKNOWLEDGEMENT")
        request: SimOrderRequest = order["request"]
        self._validate_book(book, now)
        if book.market != request.market or now < order["executable_at"]:
            raise ValueError("MARKET_MISMATCH_OR_LATENCY_NOT_ELAPSED")
        if self.events and now < self.events[-1].timestamp:
            raise ValueError("NONMONOTONIC_REPLAY_TIME")
        identity = self._book_identity(book)
        remaining = request.quantity - order["filled"]
        fills: list[tuple[Decimal, Decimal, str]] = []
        if request.order_type == "LIMIT":
            if not queue_evidence_id or queue_fill_quantity is None:
                raise UnsupportedCapability("LIMIT_FILL_REQUIRES_QUEUE_TRADE_EVIDENCE_NOT_OHLC")
            decimal(queue_fill_quantity, positive=True)
            if queue_evidence_id in self.witnesses:
                return str(order["state"])
            quantity = floor_step(min(remaining, queue_fill_quantity), request.market.step_size)
            assert request.limit_price is not None
            # Evidence asserts a genuine fill at the resting limit, not a touch.
            fills = [(request.limit_price, quantity, "QUEUE:" + queue_evidence_id)]
        else:
            levels = book.asks if request.side == "BUY" else book.bids
            for index, (price, depth) in enumerate(levels):
                key = digest((identity, request.side, index))
                available = max(ZERO, depth - self.consumed.get(key, ZERO))
                quantity = floor_step(min(remaining, available), request.market.step_size)
                if quantity:
                    fills.append((price, quantity, key))
                    remaining -= quantity
                if remaining <= 0:
                    break
        filled = sum((quantity for _, quantity, _ in fills), ZERO)
        if not filled:
            return str(order["state"])
        notional = sum((price * quantity for price, quantity, _ in fills), ZERO)
        input_currency = request.market.quote if request.side == "BUY" else request.market.base
        output_currency = request.market.base if request.side == "BUY" else request.market.quote
        output = filled if request.side == "BUY" else notional
        fee_currency = output_currency if request.fee_currency == "OUTPUT" else request.market.quote
        fee = (output if fee_currency == output_currency else notional) * request.fee_rate
        input_spend = notional if request.side == "BUY" else filled
        if fee_currency == input_currency:
            input_spend += fee
        released = filled * order["reservation_unit"]
        if input_spend > released or input_spend > self.balances.get(input_currency, ZERO):
            raise ValueError("PRICE_MOVED_BEYOND_RESERVED_CAPITAL_NO_FILL")
        self.balances[input_currency] -= input_spend
        self.balances[output_currency] = self.balances.get(output_currency, ZERO) + output
        if fee_currency == output_currency:
            self.balances[output_currency] -= fee
        self.reserved[input_currency] -= released
        order["reservation"] -= released
        order["filled"] += filled
        order["notional"] += notional
        order["fees"][fee_currency] = order["fees"].get(fee_currency, ZERO) + fee
        order["net_output"] += output - (fee if fee_currency == output_currency else ZERO)
        for _, quantity, key in fills:
            if key.startswith("QUEUE:"):
                self.witnesses.add(key[6:])
            else:
                self.consumed[key] = self.consumed.get(key, ZERO) + quantity
        state = "FILLED" if order["filled"] == request.quantity else "PARTIALLY_FILLED"
        self._event(order_id, state, now, quantity=filled, notional=notional, fee=fee,
                    fee_currency=fee_currency, snapshot_hash=book.snapshot_hash)
        self.assert_invariants()
        return state

    def cancel(self, order_id: str, now: datetime, *, expired: bool = False) -> str:
        self._check_time(now)
        order = self.orders[order_id]
        if order["state"] in TERMINAL:
            return str(order["state"])
        if self.events and now < self.events[-1].timestamp:
            raise ValueError("NONMONOTONIC_REPLAY_TIME")
        currency = order["reservation_currency"]
        if currency is not None:
            self.reserved[currency] -= order["reservation"]
        order["reservation"] = ZERO
        state = "EXPIRED" if expired else "CANCELLED"
        self._event(order_id, state, now)
        self.assert_invariants()
        return state

    def assert_invariants(self) -> None:
        for currency, balance in self.balances.items():
            decimal(balance)
            reserved = self.reserved.get(currency, ZERO)
            decimal(reserved)
            expected = sum((order["reservation"] for order in self.orders.values()
                            if order["reservation_currency"] == currency), ZERO)
            if reserved > balance or reserved != expected:
                raise ValueError("WALLET_RESERVATION_INVARIANT")

    def export_state(self) -> dict[str, Any]:
        payload = {"version": "OFFLINE_REPLAY_V1", "mode": self.mode,
                   "balances": self.balances, "reserved": self.reserved,
                   "orders": {key: {**value, "request": {**asdict(value["request"]),
                              "latency": value["request"].latency.total_seconds()}}
                              for key, value in self.orders.items()},
                   "events": [asdict(event) for event in self.events],
                   "consumed": self.consumed, "witnesses": sorted(self.witnesses),
                   "book_versions": self.book_versions}
        # JSON-compatible representations are hashed identically before/after persistence.
        import json
        serial = json.loads(json.dumps(payload, default=str, sort_keys=True))
        return {"payload": serial, "sha256": digest(serial)}

    @classmethod
    def from_state(cls, artifact: dict[str, Any]) -> OfflineOrderSimulator:
        payload = artifact["payload"]
        if digest(payload) != artifact["sha256"] or payload["version"] != "OFFLINE_REPLAY_V1" or payload["mode"] != cls.mode:
            raise ValueError("SIMULATOR_STATE_INTEGRITY_FAILURE")
        result = cls({k: Decimal(v) for k, v in payload["balances"].items()})
        result.reserved = {k: Decimal(v) for k, v in payload["reserved"].items()}
        for key, raw in payload["orders"].items():
            value = dict(raw)
            req = dict(value["request"])
            metadata = dict(req.pop("market"))
            for field in ("tick_size", "step_size", "minimum_notional"):
                metadata[field] = Decimal(metadata[field])
            req["market"] = MarketMetadata(**metadata)
            req["quantity"] = Decimal(req["quantity"])
            req["fee_rate"] = Decimal(req["fee_rate"])
            req["limit_price"] = None if req["limit_price"] is None else Decimal(req["limit_price"])
            req["latency"] = timedelta(seconds=req["latency"])
            value["request"] = SimOrderRequest(**req)
            for field in ("filled", "notional", "reservation", "reservation_unit", "net_output"):
                value[field] = Decimal(value[field])
            value["fees"] = {k: Decimal(v) for k, v in value["fees"].items()}
            for field in ("created_at", "executable_at"):
                if field in value:
                    value[field] = datetime.fromisoformat(value[field])
            result.orders[key] = value
        result.events = [SimEvent(e["event_id"], e["order_id"], datetime.fromisoformat(e["timestamp"]),
                                  e["state"], e["details"]) for e in payload["events"]]
        result.consumed = {k: Decimal(v) for k, v in payload["consumed"].items()}
        result.witnesses = set(payload["witnesses"])
        result.book_versions = dict(payload["book_versions"])
        allowed = {"CREATED", "APPROVED", "SUBMITTED_SIMULATED", "ACKNOWLEDGED", "PARTIALLY_FILLED"} | TERMINAL
        for key, order in result.orders.items():
            if key != order["request"].order_id or order["state"] not in allowed:
                raise ValueError("INVALID_PERSISTED_ORDER_ID_OR_STATE")
            for field in ("filled", "notional", "reservation", "reservation_unit", "net_output"):
                decimal(order[field])
            if order["filled"] > order["request"].quantity:
                raise ValueError("PERSISTED_ORDER_OVERFILLED")
            if order["state"] in TERMINAL and order["reservation"] != 0:
                raise ValueError("TERMINAL_ORDER_STILL_RESERVED")
            if order["state"] == "FILLED" and order["filled"] != order["request"].quantity:
                raise ValueError("FILLED_STATE_WITHOUT_FULL_FILL")
            for amount in order["fees"].values():
                decimal(amount)
            for field in ("created_at", "executable_at"):
                if field in order:
                    aware(order[field])
        for index, event in enumerate(result.events):
            aware(event.timestamp)
            if (event.order_id not in result.orders or event.state not in allowed
                    or (index and event.timestamp < result.events[index - 1].timestamp)
                    or event.event_id != digest((index, event.order_id, event.state, event.timestamp, event.details))):
                raise ValueError("PERSISTED_EVENT_SEQUENCE_INTEGRITY_FAILURE")
        for amount in result.consumed.values():
            decimal(amount)
        result.assert_invariants()
        return result
