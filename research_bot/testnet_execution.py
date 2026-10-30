from __future__ import annotations

"""Sandbox-only execution gateway for TESTNET / demo trading.

The gateway deliberately separates strategy/risk decisions from venue I/O.
It never accepts LIVE mode and never stores exchange credentials.  Order intent
is persisted before submission so a timeout cannot be answered with a blind
retry that duplicates exposure.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, localcontext
import json
import logging
from typing import Any, Protocol, Callable
from threading import RLock
from .financial_decimal import decimal_value

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
from .durable_risk import PostgresRiskGovernor, RiskApproval, RiskAdmissionError


LOG = logging.getLogger(__name__)


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
    price_tick: Decimal | None = None

    def __post_init__(self) -> None:
        for name in ("quantity_step", "min_quantity", "min_notional"):
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                raise TypeError(f"{name} must be Decimal")
            if not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.price_tick is not None and (not self.price_tick.is_finite() or self.price_tick <= 0):
            raise ValueError("price_tick must be positive finite Decimal")


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
    def update(self, order: ReconciledOrder) -> ReconciledOrder: ...


def decimal_from(value: Any, name: str) -> Decimal:
    return decimal_value(value, name)


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    if value < 0 or step <= 0:
        raise ValueError("value must be non-negative and step positive")
    with localcontext() as ctx:
        ctx.prec = 120
        units = (value / step).to_integral_value(rounding=ROUND_DOWN)
        return units * step


def validate_order_update(current: ReconciledOrder, candidate: ReconciledOrder) -> None:
    if current.state_version != candidate.state_version:
        LOG.error("STALE_ORDER_VERSION_RECONCILE_REQUIRED client_order_id=%s", current.client_order_id)
        raise TestnetSafetyError("STALE_ORDER_VERSION_RECONCILE_REQUIRED")
    for name in ("client_order_id", "symbol", "side", "requested_quantity"):
        if getattr(current, name) != getattr(candidate, name):
            raise TestnetSafetyError("IMMUTABLE_ORDER_IDENTITY")
    if current.venue_order_id and current.venue_order_id != candidate.venue_order_id:
        raise TestnetSafetyError("VENUE_ORDER_ID_MISMATCH")
    if candidate.matched_quantity < current.matched_quantity or candidate.fee_reported < current.fee_reported:
        raise TestnetSafetyError("CUMULATIVE_ORDER_REGRESSION")
    if current.fee_currency and candidate.fee_currency != current.fee_currency:
        raise TestnetSafetyError("FEE_CURRENCY_MISMATCH")
    if current.state in TERMINAL_STATES and candidate != current:
        raise TestnetSafetyError("TERMINAL_ORDER_IMMUTABLE")
    if current.state is OrderLifecycle.PARTIAL and candidate.state is OrderLifecycle.ACKNOWLEDGED:
        raise TestnetSafetyError("ORDER_STATE_REGRESSION")
    transitions = {
        OrderLifecycle.CREATED: {OrderLifecycle.CREATED, OrderLifecycle.SUBMITTING},
        OrderLifecycle.SUBMITTING: {OrderLifecycle.UNKNOWN_PENDING_RECONCILIATION,
            OrderLifecycle.ACKNOWLEDGED, OrderLifecycle.PARTIAL, *TERMINAL_STATES},
        OrderLifecycle.UNKNOWN_PENDING_RECONCILIATION: {OrderLifecycle.UNKNOWN_PENDING_RECONCILIATION,
            OrderLifecycle.ACKNOWLEDGED, OrderLifecycle.PARTIAL, *TERMINAL_STATES},
        OrderLifecycle.ACKNOWLEDGED: {OrderLifecycle.ACKNOWLEDGED, OrderLifecycle.PARTIAL, *TERMINAL_STATES},
        OrderLifecycle.PARTIAL: {OrderLifecycle.PARTIAL, *TERMINAL_STATES},
    }
    if current.state not in TERMINAL_STATES and candidate.state not in transitions.get(current.state,set()):
        raise TestnetSafetyError("INVALID_PERSISTED_STATE_TRANSITION")
    if (candidate.matched_quantity == current.matched_quantity and current.average_price is not None
            and candidate.average_price != current.average_price):
        raise TestnetSafetyError("UNCHANGED_FILL_PRICE_CONFLICT")


class MemoryTestnetOrderStore:
    """Deterministic unit-test store. Production TESTNET should use PostgreSQL."""

    def __init__(self) -> None:
        self._orders: dict[str, ReconciledOrder] = {}
        self._lock = RLock()

    def create(self, *, venue: str, intent: TestnetOrderIntent, order: ReconciledOrder) -> None:
        del venue, intent
        if order.client_order_id in self._orders:
            raise RuntimeError(f"duplicate client_order_id: {order.client_order_id}")
        self._orders[order.client_order_id] = order

    def get(self, client_order_id: str) -> ReconciledOrder | None:
        return self._orders.get(str(client_order_id))

    def update(self, order: ReconciledOrder) -> ReconciledOrder:
        with self._lock:
            current = self._orders.get(order.client_order_id)
            if current is None:
                raise RuntimeError("order intent must be persisted before update")
            validate_order_update(current, order)
            saved = replace(order, state_version=current.state_version + 1)
            self._orders[order.client_order_id] = saved
            return saved



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
            state_version BIGINT NOT NULL DEFAULT 0,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb
        );
        ALTER TABLE testnet_orders ADD COLUMN IF NOT EXISTS state_version BIGINT NOT NULL DEFAULT 0;
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
            conn.commit()

    def create(self, *, venue: str, intent: TestnetOrderIntent, order: ReconciledOrder) -> None:
        with self._connect() as conn:
            self.create_in_transaction(conn, venue=venue, intent=intent, order=order)

    def create_in_transaction(self, conn, *, venue: str, intent: TestnetOrderIntent, order: ReconciledOrder) -> None:
        if (intent.client_order_id, intent.symbol, intent.side.value.lower(), intent.quantity) != (
                order.client_order_id, order.symbol, order.side, order.requested_quantity):
            raise TestnetSafetyError("INTENT_ORDER_IDENTITY_MISMATCH")
        if intent.reference_price <= 0:
            raise TestnetSafetyError("INVALID_REFERENCE_PRICE")
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

    def get(self, client_order_id: str) -> ReconciledOrder | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT client_order_id,symbol,side,requested_quantity,state,venue_order_id,
                           matched_quantity,average_price,fee_reported,fee_currency,updated_at,state_version
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
            requested_quantity=row[3],
            state=OrderLifecycle(str(row[4])),
            venue_order_id=None if row[5] is None else str(row[5]),
            matched_quantity=row[6],
            average_price=row[7],
            fee_reported=row[8],
            fee_currency=None if row[9] is None else str(row[9]),
            updated_at=row[10],
            state_version=int(row[11]),
        )

    def update(self, order: ReconciledOrder) -> ReconciledOrder:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT client_order_id,symbol,side,requested_quantity,state,venue_order_id,matched_quantity,average_price,fee_reported,fee_currency,updated_at,state_version FROM testnet_orders WHERE client_order_id=%s FOR UPDATE", (order.client_order_id,))
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("order intent must be persisted before update")
                current = ReconciledOrder(row[0],row[1],row[2],row[3],OrderLifecycle(row[4]),row[5],row[6],row[7],row[8],row[9],row[10],int(row[11]))
                validate_order_update(current, order)
                cur.execute(
                    """
                    UPDATE testnet_orders SET
                        state=%s,venue_order_id=%s,matched_quantity=%s,average_price=%s,
                        fee_reported=%s,fee_currency=%s,updated_at=%s,state_version=state_version+1
                    WHERE client_order_id=%s AND state_version=%s
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
                        order.state_version,
                    ),
                )
                if cur.rowcount != 1:
                    raise RuntimeError("order intent must be persisted before update")
            conn.commit()
        return replace(order, state_version=order.state_version + 1)


class TestnetExecutionGateway:
    """Risk-gated, sandbox-locked TESTNET order gateway."""

    def __init__(
        self,
        *,
        transport: SandboxTransport,
        store: TestnetOrderStore,
        readiness: LiveReadinessDecision,
        mode: ExecutionMode = ExecutionMode.TESTNET,
        risk_governor: PostgresRiskGovernor | None = None,
        account_id: str | None = None,
        clock: Callable[[], datetime] | None = None,
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
        self.risk_governor = risk_governor
        self.account_id = account_id
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def submit_market(
        self,
        *,
        client_order_id: str,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        reference_price: Decimal,
        precision: MarketPrecision,
        risk_decision: RiskDecision | RiskApproval,
        created_at: datetime | None = None,
    ) -> ReconciledOrder:
        if not risk_decision.approved or risk_decision.kill_switch:
            raise TestnetSafetyError("RISK_GATE_REJECTED")
        if not isinstance(risk_decision, RiskApproval) or self.risk_governor is None or not self.account_id:
            raise TestnetSafetyError("DURABLE_BOUND_APPROVAL_REQUIRED")
        if not client_order_id.strip() or not symbol.strip():
            raise ValueError("client_order_id and symbol are required")

        qty = decimal_from(quantity, "quantity")
        ref = decimal_from(reference_price, "reference_price")
        if qty <= 0 or ref <= 0:
            raise ValueError("quantity and reference_price must be positive")
        qty = floor_to_step(qty, precision.quantity_step)
        if precision.price_tick is not None and floor_to_step(ref, precision.price_tick) != ref:
            raise TestnetSafetyError("REFERENCE_PRICE_TICK_MISMATCH")
        if qty < precision.min_quantity:
            raise TestnetSafetyError("MIN_QUANTITY_BREACH")
        if qty * ref < precision.min_notional:
            raise TestnetSafetyError("MIN_NOTIONAL_BREACH")

        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("clock must be timezone-aware")
        if created_at is not None and (created_at.tzinfo is None or not 0 <= (now-created_at).total_seconds() <= 30):
            raise TestnetSafetyError("STALE_OR_FUTURE_ORDER_INTENT")

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
            requested_quantity=qty,
            state=OrderLifecycle.CREATED,
            updated_at=now,
        )

        # The durable intent MUST exist before the first venue submission.
        try:
            self.risk_governor.consume_with_intent(risk_decision, store=self.store, intent=intent,
                order=local, venue_id=self.transport.venue, account_id=self.account_id, now=now)
        except RiskAdmissionError as exc:
            LOG.error("RISK_APPROVAL_REJECTED client_order_id=%s", client_order_id, exc_info=True)
            raise TestnetSafetyError(str(exc)) from exc
        local = mark_submit_started(local, now=now)
        local = self.store.update(local)

        try:
            snapshot = self.transport.submit_market_order(intent)
        except AmbiguousTransportOutcome as exc:
            local = mark_submit_ambiguous(local)
            local = self.store.update(local)
            raise AmbiguousTestnetSubmission(client_order_id) from exc
        except Exception as exc:
            local = mark_submit_ambiguous(local)
            self.store.update(local)
            raise AmbiguousTestnetSubmission(client_order_id) from exc

        acknowledged = self._merge_snapshot(local, snapshot)
        saved = self.store.update(acknowledged)
        self._account_for(saved)
        return saved

    def reconcile(self, client_order_id: str) -> ReconciledOrder:
        local = self.store.get(client_order_id)
        if local is None:
            raise KeyError(client_order_id)
        if local.state in TERMINAL_STATES:
            self._account_for(local)
            return local
        snapshot = self.transport.fetch_order(
            client_order_id=local.client_order_id,
            venue_order_id=local.venue_order_id,
            symbol=local.symbol,
        )
        if snapshot is None:
            self._account_for(local)
            return local
        merged = self._merge_snapshot(local, snapshot)
        saved = self.store.update(merged)
        self._account_for(saved)
        return saved

    def _account_for(self, order: ReconciledOrder) -> None:
        if self.risk_governor is not None and self.account_id:
            self.risk_governor.reconcile_accounting(account_id=self.account_id,
                venue_id=self.transport.venue,client_order_id=order.client_order_id,
                now=datetime.now(timezone.utc))

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
        previous_matched = decimal_from(local.matched_quantity, "previous_matched_quantity")
        if matched < previous_matched:
            raise TestnetSafetyError("MATCHED_QUANTITY_REGRESSION")
        if snapshot.state is OrderLifecycle.FILLED and matched != requested:
            raise TestnetSafetyError("FILLED_QUANTITY_INCOMPLETE")

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
        if local.fee_currency and snapshot.fee_currency != local.fee_currency:
            raise TestnetSafetyError("FEE_CURRENCY_MISMATCH")
        if avg is not None and avg <= 0:
            raise TestnetSafetyError("INVALID_AVERAGE_PRICE")
        if fee < local.fee_reported:
            raise TestnetSafetyError("INVALID_OR_REGRESSING_FEE")

        return ReconciledOrder(
            client_order_id=local.client_order_id,
            symbol=local.symbol,
            side=local.side,
            requested_quantity=local.requested_quantity,
            state=snapshot.state,
            venue_order_id=snapshot.venue_order_id,
            matched_quantity=matched,
            average_price=avg,
            fee_reported=fee,
            fee_currency=snapshot.fee_currency,
            updated_at=datetime.now(timezone.utc),
            state_version=local.state_version,
        )
