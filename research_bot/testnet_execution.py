from __future__ import annotations

"""Sandbox-only execution gateway for TESTNET / demo trading.

The gateway deliberately separates strategy/risk decisions from venue I/O.
It never accepts LIVE mode and never stores exchange credentials.  Order intent
is persisted before submission so a timeout cannot be answered with a blind
retry that duplicates exposure.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import json
from typing import Any, Protocol

from .contracts import ExecutionMode
from .execution import OrderSide
from .live_readiness import LiveReadinessDecision
from .order_reconciliation_v52 import (
    OrderLifecycle,
    ReconciledOrder,
    TERMINAL_STATES,
    mark_submit_ambiguous,
    mark_submit_started,
)
from .risk import RiskDecision


class TestnetSafetyError(RuntimeError):
    pass


class AmbiguousTransportOutcome(RuntimeError):
    """The venue may have accepted the order but the client lacks an ACK."""


class AmbiguousTestnetSubmission(RuntimeError):
    pass


@dataclass(frozen=True)
class MarketPrecision:
    quantity_step: Decimal
    min_quantity: Decimal
    min_notional: Decimal

    def __post_init__(self) -> None:
        for name in ("quantity_step", "min_quantity", "min_notional"):
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                raise TypeError(f"{name} must be Decimal")
            if value <= 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True)
class TestnetOrderIntent:
    client_order_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    reference_price: Decimal
    created_at: datetime

    @property
    def notional(self) -> Decimal:
        return self.quantity * self.reference_price


@dataclass(frozen=True)
class VenueOrderSnapshot:
    client_order_id: str
    venue_order_id: str
    state: OrderLifecycle
    matched_quantity: Decimal = Decimal("0")
    average_price: Decimal | None = None
    fee_reported: Decimal = Decimal("0")
    fee_currency: str | None = None


class SandboxTransport(Protocol):
    sandbox: bool
    venue: str

    def submit_market_order(self, intent: TestnetOrderIntent) -> VenueOrderSnapshot: ...

    def fetch_order(
        self,
        *,
        client_order_id: str,
        venue_order_id: str | None,
        symbol: str,
    ) -> VenueOrderSnapshot | None: ...


class TestnetOrderStore(Protocol):
    def create(self, *, venue: str, intent: TestnetOrderIntent, order: ReconciledOrder) -> None: ...
    def get(self, client_order_id: str) -> ReconciledOrder | None: ...
    def update(self, order: ReconciledOrder) -> None: ...


def decimal_from(value: Any, name: str) -> Decimal:
    try:
        out = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"invalid decimal {name}") from exc
    if not out.is_finite():
        raise ValueError(f"{name} must be finite")
    return out


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    if value < 0 or step <= 0:
        raise ValueError("value must be non-negative and step positive")
    units = (value / step).to_integral_value(rounding=ROUND_DOWN)
    return units * step


class MemoryTestnetOrderStore:
    """Deterministic unit-test store. Production TESTNET should use PostgreSQL."""

    def __init__(self) -> None:
        self._orders: dict[str, ReconciledOrder] = {}

    def create(self, *, venue: str, intent: TestnetOrderIntent, order: ReconciledOrder) -> None:
        del venue, intent
        if order.client_order_id in self._orders:
            raise RuntimeError(f"duplicate client_order_id: {order.client_order_id}")
        self._orders[order.client_order_id] = order

    def get(self, client_order_id: str) -> ReconciledOrder | None:
        return self._orders.get(str(client_order_id))

    def update(self, order: ReconciledOrder) -> None:
        if order.client_order_id not in self._orders:
            raise RuntimeError("order intent must be persisted before update")
        self._orders[order.client_order_id] = order


class PostgresTestnetOrderStore:
    """Crash-persistent TESTNET order ledger. Credentials are never stored here."""

    def __init__(self, database_url: str) -> None:
        if not database_url:
            raise ValueError("database_url is required")
        self.database_url = database_url

    def _connect(self):
        import psycopg
        return psycopg.connect(self.database_url)

    def ensure_schema(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS testnet_orders (
            client_order_id TEXT PRIMARY KEY,
            venue TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            requested_quantity NUMERIC NOT NULL,
            reference_price NUMERIC NOT NULL,
            state TEXT NOT NULL,
            venue_order_id TEXT,
            matched_quantity NUMERIC NOT NULL DEFAULT 0,
            average_price NUMERIC,
            fee_reported NUMERIC NOT NULL DEFAULT 0,
            fee_currency TEXT,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb
        );
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
            conn.commit()

    def create(self, *, venue: str, intent: TestnetOrderIntent, order: ReconciledOrder) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO testnet_orders(
                        client_order_id,venue,symbol,side,requested_quantity,reference_price,
                        state,venue_order_id,matched_quantity,average_price,fee_reported,
                        fee_currency,created_at,updated_at,metadata
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    """,
                    (
                        order.client_order_id,
                        str(venue),
                        order.symbol,
                        order.side,
                        str(intent.quantity),
                        str(intent.reference_price),
                        order.state.value,
                        order.venue_order_id,
                        str(order.matched_quantity),
                        None if order.average_price is None else str(order.average_price),
                        str(order.fee_reported),
                        order.fee_currency,
                        intent.created_at,
                        order.updated_at,
                        json.dumps({"execution_mode": ExecutionMode.TESTNET.value}),
                    ),
                )
            conn.commit()

    def get(self, client_order_id: str) -> ReconciledOrder | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT client_order_id,symbol,side,requested_quantity,state,venue_order_id,
                           matched_quantity,average_price,fee_reported,fee_currency,updated_at
                    FROM testnet_orders WHERE client_order_id=%s
                    """,
                    (str(client_order_id),),
                )
                row = cur.fetchone()
        if row is None:
            return None
        return ReconciledOrder(
            client_order_id=str(row[0]),
            symbol=str(row[1]),
            side=str(row[2]),
            requested_quantity=float(row[3]),
            state=OrderLifecycle(str(row[4])),
            venue_order_id=None if row[5] is None else str(row[5]),
            matched_quantity=float(row[6]),
            average_price=None if row[7] is None else float(row[7]),
            fee_reported=float(row[8]),
            fee_currency=None if row[9] is None else str(row[9]),
            updated_at=row[10],
        )

    def update(self, order: ReconciledOrder) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE testnet_orders SET
                        state=%s,venue_order_id=%s,matched_quantity=%s,average_price=%s,
                        fee_reported=%s,fee_currency=%s,updated_at=%s
                    WHERE client_order_id=%s
                    """,
                    (
                        order.state.value,
                        order.venue_order_id,
                        str(order.matched_quantity),
                        None if order.average_price is None else str(order.average_price),
                        str(order.fee_reported),
                        order.fee_currency,
                        order.updated_at,
                        order.client_order_id,
                    ),
                )
                if cur.rowcount != 1:
                    raise RuntimeError("order intent must be persisted before update")
            conn.commit()


class TestnetExecutionGateway:
    """Risk-gated, sandbox-locked TESTNET order gateway."""

    def __init__(
        self,
        *,
        transport: SandboxTransport,
        store: TestnetOrderStore,
        readiness: LiveReadinessDecision,
        mode: ExecutionMode = ExecutionMode.TESTNET,
    ) -> None:
        if mode is not ExecutionMode.TESTNET:
            raise TestnetSafetyError("TESTNET_GATEWAY_REJECTS_NON_TESTNET_MODE")
        if not bool(getattr(transport, "sandbox", False)):
            raise TestnetSafetyError("SANDBOX_TRANSPORT_REQUIRED")
        if not readiness.testnet_review_eligible:
            raise TestnetSafetyError("TESTNET_READINESS_GATE_CLOSED")
        if readiness.live_execution_authorized:
            raise TestnetSafetyError("LIVE_AUTHORIZATION_MUST_NOT_FLOW_THROUGH_TESTNET_GATEWAY")
        self.transport = transport
        self.store = store
        self.readiness = readiness
        self.mode = mode

    def submit_market(
        self,
        *,
        client_order_id: str,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        reference_price: Decimal,
        precision: MarketPrecision,
        risk_decision: RiskDecision,
        created_at: datetime | None = None,
    ) -> ReconciledOrder:
        if not risk_decision.approved or risk_decision.kill_switch:
            raise TestnetSafetyError("RISK_GATE_REJECTED")
        if not client_order_id.strip() or not symbol.strip():
            raise ValueError("client_order_id and symbol are required")

        qty = decimal_from(quantity, "quantity")
        ref = decimal_from(reference_price, "reference_price")
        if qty <= 0 or ref <= 0:
            raise ValueError("quantity and reference_price must be positive")
        qty = floor_to_step(qty, precision.quantity_step)
        if qty < precision.min_quantity:
            raise TestnetSafetyError("MIN_QUANTITY_BREACH")
        if qty * ref < precision.min_notional:
            raise TestnetSafetyError("MIN_NOTIONAL_BREACH")

        now = created_at or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")

        intent = TestnetOrderIntent(
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            quantity=qty,
            reference_price=ref,
            created_at=now,
        )
        local = ReconciledOrder(
            client_order_id=client_order_id,
            symbol=symbol,
            side=side.value.lower(),
            requested_quantity=float(qty),
            state=OrderLifecycle.CREATED,
            updated_at=now,
        )

        # The durable intent MUST exist before the first venue submission.
        self.store.create(venue=self.transport.venue, intent=intent, order=local)
        local = mark_submit_started(local, now=now)
        self.store.update(local)

        try:
            snapshot = self.transport.submit_market_order(intent)
        except AmbiguousTransportOutcome as exc:
            local = mark_submit_ambiguous(local)
            self.store.update(local)
            raise AmbiguousTestnetSubmission(client_order_id) from exc
        except Exception:
            rejected = replace(
                local,
                state=OrderLifecycle.REJECTED,
                updated_at=datetime.now(timezone.utc),
            )
            self.store.update(rejected)
            raise

        acknowledged = self._merge_snapshot(local, snapshot)
        self.store.update(acknowledged)
        return acknowledged

    def reconcile(self, client_order_id: str) -> ReconciledOrder:
        local = self.store.get(client_order_id)
        if local is None:
            raise KeyError(client_order_id)
        if local.state in TERMINAL_STATES:
            return local
        snapshot = self.transport.fetch_order(
            client_order_id=local.client_order_id,
            venue_order_id=local.venue_order_id,
            symbol=local.symbol,
        )
        if snapshot is None:
            return local
        merged = self._merge_snapshot(local, snapshot)
        self.store.update(merged)
        return merged

    @staticmethod
    def _merge_snapshot(local: ReconciledOrder, snapshot: VenueOrderSnapshot) -> ReconciledOrder:
        if snapshot.client_order_id != local.client_order_id:
            raise TestnetSafetyError("CLIENT_ORDER_ID_MISMATCH")
        if not snapshot.venue_order_id:
            raise TestnetSafetyError("VENUE_ORDER_ID_REQUIRED")
        if local.venue_order_id and snapshot.venue_order_id != local.venue_order_id:
            raise TestnetSafetyError("VENUE_ORDER_ID_MISMATCH")

        matched = decimal_from(snapshot.matched_quantity, "matched_quantity")
        requested = decimal_from(local.requested_quantity, "requested_quantity")
        if matched < 0 or matched > requested:
            raise TestnetSafetyError("INVALID_MATCHED_QUANTITY")

        allowed = {
            OrderLifecycle.SUBMITTING: {
                OrderLifecycle.ACKNOWLEDGED,
                OrderLifecycle.PARTIAL,
                OrderLifecycle.FILLED,
                OrderLifecycle.CANCELLED,
                OrderLifecycle.REJECTED,
            },
            OrderLifecycle.UNKNOWN_PENDING_RECONCILIATION: {
                OrderLifecycle.ACKNOWLEDGED,
                OrderLifecycle.PARTIAL,
                OrderLifecycle.FILLED,
                OrderLifecycle.CANCELLED,
                OrderLifecycle.REJECTED,
            },
            OrderLifecycle.ACKNOWLEDGED: {
                OrderLifecycle.ACKNOWLEDGED,
                OrderLifecycle.PARTIAL,
                OrderLifecycle.FILLED,
                OrderLifecycle.CANCELLED,
                OrderLifecycle.REJECTED,
            },
            OrderLifecycle.PARTIAL: {
                OrderLifecycle.PARTIAL,
                OrderLifecycle.FILLED,
                OrderLifecycle.CANCELLED,
                OrderLifecycle.REJECTED,
            },
        }
        if local.state in TERMINAL_STATES:
            return local
        if snapshot.state not in allowed.get(local.state, set()):
            raise TestnetSafetyError(
                f"INVALID_ORDER_STATE_TRANSITION:{local.state.value}->{snapshot.state.value}"
            )

        avg = None if snapshot.average_price is None else decimal_from(snapshot.average_price, "average_price")
        fee = decimal_from(snapshot.fee_reported, "fee_reported")
        if avg is not None and avg <= 0:
            raise TestnetSafetyError("INVALID_AVERAGE_PRICE")
        if fee < 0:
            raise TestnetSafetyError("INVALID_FEE")

        return ReconciledOrder(
            client_order_id=local.client_order_id,
            symbol=local.symbol,
            side=local.side,
            requested_quantity=local.requested_quantity,
            state=snapshot.state,
            venue_order_id=snapshot.venue_order_id,
            matched_quantity=float(matched),
            average_price=None if avg is None else float(avg),
            fee_reported=float(fee),
            fee_currency=snapshot.fee_currency,
            updated_at=datetime.now(timezone.utc),
        )
