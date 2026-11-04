"""Synthetic engineering contracts for independent DEMO gate D0.

These tests are NOT evidence of official broker connectivity or D0 operational
approval. Actual DEMO orders must remain blocked while finance defects persist.
"""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from research_bot.contracts import ExecutionMode
from research_bot.demo_readiness import DemoOrderEvidence, evaluate_demo_order_readiness
from research_bot.execution import OrderSide
from research_bot.live_readiness import LiveReadinessChecklist, evaluate_live_readiness
from research_bot.order_reconciliation_v52 import OrderLifecycle
from research_bot.risk import RiskDecision
from research_bot.testnet_execution import (
    MarketPrecision,
    MemoryTestnetOrderStore,
    TestnetExecutionGateway,
    TestnetSafetyError,
    VenueOrderSnapshot,
)


def valid_synthetic_evidence():
    # Synthetic flags are supplied ONLY to test the pure gate. No venue was verified.
    return DemoOrderEvidence(
        venue="FAKE_DEMO",
        allowed_symbols=("BTC/USDT",),
        max_virtual_notional=Decimal("500"),
        explicit_demo_enabled=True,
        official_sandbox_identity_verified=True,
        demo_scoped_credentials_verified=True,
        authenticated_readonly_preflight_passed=True,
        durable_intent_and_recovery_validated=True,
        ambiguous_submit_reconciliation_validated=True,
        fee_liability_and_terminal_reconciliation_validated=True,
        independent_risk_and_kill_switch_validated=True,
        failure_drills_passed=True,
        explicit_operator_demo_order_approval=True,
    )


class FakeDemo:
    venue = "FAKE_DEMO"
    sandbox = True

    def __init__(self):
        self.submitted = 0

    def submit_market_order(self, intent):
        self.submitted += 1
        return VenueOrderSnapshot(
            client_order_id=intent.client_order_id,
            venue_order_id="synthetic-venue-1",
            state=OrderLifecycle.ACKNOWLEDGED,
        )

    def fetch_order(self, **kwargs):
        return None


def gateway(transport=None, decision=None, readiness=None):
    return TestnetExecutionGateway(
        transport=transport if transport is not None else FakeDemo(),
        store=MemoryTestnetOrderStore(),
        readiness=readiness if readiness is not None else evaluate_live_readiness(LiveReadinessChecklist()),
        demo_readiness=decision if decision is not None else evaluate_demo_order_readiness(valid_synthetic_evidence()),
        mode=ExecutionMode.TESTNET,
    )


def submit(g, symbol="BTC/USDT", qty=Decimal("0.001"), px=Decimal("100000")):
    return g.submit_market(
        client_order_id="only-synthetic-id",
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=qty,
        reference_price=px,
        precision=MarketPrecision(
            quantity_step=Decimal("0.001"),
            min_quantity=Decimal("0.001"),
            min_notional=Decimal("5"),
        ),
        risk_decision=RiskDecision(True, False, (), None),
        created_at=datetime(2026, 10, 5, tzinfo=timezone.utc),
    )


def test_default_d0_is_closed_with_missing_evidence():
    decision = evaluate_demo_order_readiness(DemoOrderEvidence())
    assert not decision.order_test_eligible
    assert decision.live_execution_authorized is False
    assert "VERIFIED_DEMO_VENUE_REQUIRED" in decision.missing_evidence


@pytest.mark.parametrize(
    "field",
    [
        "explicit_demo_enabled",
        "official_sandbox_identity_verified",
        "demo_scoped_credentials_verified",
        "authenticated_readonly_preflight_passed",
        "durable_intent_and_recovery_validated",
        "ambiguous_submit_reconciliation_validated",
        "fee_liability_and_terminal_reconciliation_validated",
        "independent_risk_and_kill_switch_validated",
        "failure_drills_passed",
        "explicit_operator_demo_order_approval",
    ],
)
def test_missing_any_required_d0_proof_blocks_orders(field):
    decision = evaluate_demo_order_readiness(replace(valid_synthetic_evidence(), **{field: False}))
    assert not decision.order_test_eligible
    assert field in decision.missing_evidence


@pytest.mark.parametrize("cap", ["0", "-1", "NaN", "Infinity", "invalid"])
def test_invalid_virtual_order_cap_denied(cap):
    d = evaluate_demo_order_readiness(replace(valid_synthetic_evidence(), max_virtual_notional=cap))
    assert not d.order_test_eligible
    assert d.max_virtual_notional == Decimal("0")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"venue": "OKX"},
        {"venue": ""},
        {"allowed_symbols": ()},
        {"allowed_symbols": ("BTC/USDT", "BTC/USDT")},
    ],
)
def test_ambiguous_venue_or_unreliable_allowlist_denied(kwargs):
    d = evaluate_demo_order_readiness(replace(valid_synthetic_evidence(), **kwargs))
    assert not d.order_test_eligible


def test_legacy_testnet_pass_does_not_override_absent_d0():
    legacy = evaluate_live_readiness(LiveReadinessChecklist(
        scientific_promotion_passed=True,
        forward_paper_reconciled=True,
        order_reconciliation_validated=True,
        risk_constitution_unified=True,
    ))
    assert legacy.testnet_review_eligible
    with pytest.raises(TestnetSafetyError, match="D0"):
        TestnetExecutionGateway(
            transport=FakeDemo(),
            store=MemoryTestnetOrderStore(),
            readiness=legacy,
        )


def test_d0_does_not_depend_on_scientific_promotion_for_synthetic_tests():
    g = gateway()
    result = submit(g)
    assert result.state is OrderLifecycle.ACKNOWLEDGED
    assert g.transport.submitted == 1
    assert g.readiness.testnet_review_eligible is False


def test_off_allowlist_order_blocked_before_venue_or_store():
    transport = FakeDemo()
    g = gateway(transport=transport)
    with pytest.raises(TestnetSafetyError, match="NOT_ALLOWLISTED"):
        submit(g, symbol="ETH/USDT")
    assert transport.submitted == 0
    assert g.store.get("only-synthetic-id") is None


def test_over_cap_order_blocked_before_venue_or_store():
    transport = FakeDemo()
    g = gateway(transport=transport)
    with pytest.raises(TestnetSafetyError, match="NOTIONAL_CAP"):
        submit(g, qty=Decimal("0.006"))
    assert transport.submitted == 0
    assert g.store.get("only-synthetic-id") is None


def test_cross_venue_d0_is_not_reusable():
    transport = FakeDemo()
    transport.venue = "DIFFERENT_DEMO"
    with pytest.raises(TestnetSafetyError, match="VENUE_IDENTITY_MISMATCH"):
        gateway(transport=transport)


def test_sandbox_flag_changed_after_construction_rejected_pre_submit():
    transport = FakeDemo()
    g = gateway(transport=transport)
    transport.sandbox = False
    with pytest.raises(TestnetSafetyError, match="SANDBOX_TRANSPORT_REQUIRED"):
        submit(g)
    assert transport.submitted == 0
