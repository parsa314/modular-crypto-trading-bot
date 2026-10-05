from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from research_bot.contracts import ExecutionMode
from research_bot.execution import OrderSide
from research_bot.live_readiness import LiveReadinessChecklist, evaluate_live_readiness
from research_bot.order_reconciliation_v52 import OrderLifecycle
from research_bot.risk import RiskDecision
from research_bot.testnet_execution import (
    AmbiguousTestnetSubmission,
    AmbiguousTransportOutcome,
    MarketPrecision,
    MemoryTestnetOrderStore,
    TestnetExecutionGateway,
    TestnetSafetyError,
    VenueOrderSnapshot,
)


def _readiness():
    return evaluate_live_readiness(
        LiveReadinessChecklist(
            scientific_promotion_passed=True,
            forward_paper_reconciled=True,
            order_reconciliation_validated=True,
            risk_constitution_unified=True,
        )
    )


def _risk_ok():
    return RiskDecision(approved=True, kill_switch=False, reasons=(), cvar_95=None)


def _precision():
    return MarketPrecision(
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        min_notional=Decimal("5"),
    )


class FakeSandboxTransport:
    sandbox = True
    venue = "FAKE_DEMO"

    def __init__(self):
        self.calls = 0
        self.snapshot = VenueOrderSnapshot(
            client_order_id="cid-1",
            venue_order_id="venue-1",
            state=OrderLifecycle.ACKNOWLEDGED,
        )
        self.ambiguous = False

    def submit_market_order(self, intent):
        self.calls += 1
        if self.ambiguous:
            raise AmbiguousTransportOutcome("timeout after send")
        return replace(self.snapshot, client_order_id=intent.client_order_id)

    def fetch_order(self, *, client_order_id, venue_order_id, symbol):
        del venue_order_id, symbol
        return replace(self.snapshot, client_order_id=client_order_id)


def _gateway(transport=None, store=None):
    return TestnetExecutionGateway(
        transport=transport or FakeSandboxTransport(),
        store=store or MemoryTestnetOrderStore(),
        readiness=_readiness(),
    )


def test_testnet_gateway_rejects_live_mode_and_non_sandbox_transport():
    transport = FakeSandboxTransport()
    with pytest.raises(TestnetSafetyError, match="NON_TESTNET"):
        TestnetExecutionGateway(
            transport=transport,
            store=MemoryTestnetOrderStore(),
            readiness=_readiness(),
            mode=ExecutionMode.LIVE,
        )

    transport.sandbox = False
    with pytest.raises(TestnetSafetyError, match="SANDBOX_TRANSPORT_REQUIRED"):
        TestnetExecutionGateway(
            transport=transport,
            store=MemoryTestnetOrderStore(),
            readiness=_readiness(),
        )


def test_readiness_gate_must_pass_before_testnet_submission():
    with pytest.raises(TestnetSafetyError, match="READINESS_GATE_CLOSED"):
        TestnetExecutionGateway(
            transport=FakeSandboxTransport(),
            store=MemoryTestnetOrderStore(),
            readiness=evaluate_live_readiness(LiveReadinessChecklist()),
        )


def test_decimal_quantity_is_floored_to_venue_step_before_submit():
    transport = FakeSandboxTransport()
    gateway = _gateway(transport=transport)
    order = gateway.submit_market(
        client_order_id="cid-1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        quantity=Decimal("0.0109"),
        reference_price=Decimal("100000"),
        precision=_precision(),
        risk_decision=_risk_ok(),
        created_at=datetime(2026, 10, 5, tzinfo=timezone.utc),
    )
    assert order.state is OrderLifecycle.ACKNOWLEDGED
    assert order.requested_quantity == pytest.approx(0.010)
    assert transport.calls == 1


def test_risk_rejection_prevents_any_transport_call():
    transport = FakeSandboxTransport()
    gateway = _gateway(transport=transport)
    with pytest.raises(TestnetSafetyError, match="RISK_GATE_REJECTED"):
        gateway.submit_market(
            client_order_id="cid-1",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            quantity=Decimal("0.01"),
            reference_price=Decimal("100000"),
            precision=_precision(),
            risk_decision=RiskDecision(
                approved=False,
                kill_switch=True,
                reasons=("MAX_DRAWDOWN_BREACH",),
                cvar_95=None,
            ),
        )
    assert transport.calls == 0


def test_ambiguous_submit_is_persisted_and_blind_retry_is_blocked():
    transport = FakeSandboxTransport()
    transport.ambiguous = True
    store = MemoryTestnetOrderStore()
    gateway = _gateway(transport=transport, store=store)

    with pytest.raises(AmbiguousTestnetSubmission):
        gateway.submit_market(
            client_order_id="cid-ambiguous",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            quantity=Decimal("0.01"),
            reference_price=Decimal("100000"),
            precision=_precision(),
            risk_decision=_risk_ok(),
        )
    saved = store.get("cid-ambiguous")
    assert saved is not None
    assert saved.state is OrderLifecycle.UNKNOWN_PENDING_RECONCILIATION
    assert transport.calls == 1

    with pytest.raises(RuntimeError, match="duplicate client_order_id"):
        gateway.submit_market(
            client_order_id="cid-ambiguous",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            quantity=Decimal("0.01"),
            reference_price=Decimal("100000"),
            precision=_precision(),
            risk_decision=_risk_ok(),
        )
    assert transport.calls == 1


def test_reconcile_advances_partial_then_terminal_and_preserves_identity():
    transport = FakeSandboxTransport()
    store = MemoryTestnetOrderStore()
    gateway = _gateway(transport=transport, store=store)
    gateway.submit_market(
        client_order_id="cid-1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        quantity=Decimal("0.01"),
        reference_price=Decimal("100000"),
        precision=_precision(),
        risk_decision=_risk_ok(),
    )

    transport.snapshot = VenueOrderSnapshot(
        client_order_id="cid-1",
        venue_order_id="venue-1",
        state=OrderLifecycle.PARTIAL,
        matched_quantity=Decimal("0.004"),
        average_price=Decimal("100010"),
        fee_reported=Decimal("0.1"),
        fee_currency="USDT",
    )
    partial = gateway.reconcile("cid-1")
    assert partial.state is OrderLifecycle.PARTIAL
    assert partial.matched_quantity == pytest.approx(0.004)

    transport.snapshot = replace(
        transport.snapshot,
        state=OrderLifecycle.FILLED,
        matched_quantity=Decimal("0.010"),
        average_price=Decimal("100012"),
    )
    filled = gateway.reconcile("cid-1")
    assert filled.state is OrderLifecycle.FILLED
    assert filled.venue_order_id == "venue-1"
    assert gateway.reconcile("cid-1").state is OrderLifecycle.FILLED
