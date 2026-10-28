"""NEXT-GATE-PG-OMS-001 acceptance probes; missing safety is a failure."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import unittest

from research_bot.execution import OrderSide
from research_bot.live_readiness import LiveReadinessChecklist, evaluate_live_readiness
from research_bot.operational_risk import BrokerRiskContext, OperationalRiskEngine
from research_bot.order_reconciliation_v52 import OrderLifecycle, ReconciledOrder
from research_bot.risk import RiskSnapshot
from research_bot.testnet_execution import (
    AmbiguousTestnetSubmission, AmbiguousTransportOutcome, MarketPrecision,
    MemoryTestnetOrderStore, TestnetExecutionGateway, TestnetSafetyError, VenueOrderSnapshot,
)


class SyntheticTransport:
    sandbox = True
    venue = 'SYNTHETIC_NO_BROKER'

    def __init__(self, *, timeout=False):
        self.calls = 0
        self.timeout = timeout

    def submit_market_order(self, intent):
        self.calls += 1
        if self.timeout:
            raise AmbiguousTransportOutcome('synthetic timeout after send')
        return VenueOrderSnapshot(intent.client_order_id, 'synthetic-venue', OrderLifecycle.ACKNOWLEDGED)

    def fetch_order(self, **kwargs):
        return VenueOrderSnapshot(kwargs['client_order_id'], 'synthetic-venue', OrderLifecycle.ACKNOWLEDGED)


class NextGateRiskValidation(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
        self.snapshot = RiskSnapshot(1000, 1000, 0.0, 0.0, 0.0, 1.0, 1.0)
        self.context = BrokerRiskContext('SYNTHETIC_ACCOUNT', 'BTCUSDT', self.now, self.now,
            Decimal('1'), reconciled=True, unresolved_orders=False,
            day_start_equity=Decimal('1000'), day_start_date_utc='2026-10-28')

    def decide(self, engine, *, equity=1000, side='BUY', quantity='1', context=None):
        return engine.evaluate_order(replace(self.snapshot, equity=equity), symbol='BTC/USDT',
            side=side, quantity=Decimal(quantity), price=Decimal('100'), now=self.now,
            context=context or self.context, stop_price=Decimal('99.9'), roundtrip_cost_bps=50)

    def test_daily_and_drawdown_halt_reduction_and_unknown_recovery(self):
        engine = OperationalRiskEngine()
        for equity in [980, 940]:
            with self.subTest(equity=equity):
                self.assertFalse(self.decide(engine, equity=equity).approved)
                self.assertTrue(self.decide(engine, equity=equity, side='SELL').approved)
                self.assertFalse(self.decide(engine, equity=equity, side='SELL', quantity='1.01').approved)
        unknown = replace(self.context, unresolved_orders=True)
        self.assertFalse(self.decide(engine, context=unknown).approved)
        self.assertFalse(self.decide(engine, side='SELL', context=unknown).approved)

    def test_restart_does_not_unlatch_hard_drawdown_without_authorization(self):
        self.assertFalse(self.decide(OperationalRiskEngine(), equity=940).approved)
        # Recovered equity is not operator authorization to clear a hard halt.
        self.assertFalse(self.decide(OperationalRiskEngine(), equity=1000).approved)

    def test_two_workers_cannot_authorize_same_shared_asset_budget_twice(self):
        def approve(_):
            return self.decide(OperationalRiskEngine(), quantity='3.5').approved
        # Each request is 35% of the same account/asset; both cannot reserve it.
        with ThreadPoolExecutor(max_workers=2) as pool:
            approved = list(pool.map(approve, range(2)))
        self.assertLessEqual(sum(approved), 1)

    def test_order_quantity_preserves_decimal_exchange_precision(self):
        quantity = Decimal('0.123456789012345678')
        engine = OperationalRiskEngine()
        transport = SyntheticTransport()
        gateway = self.gateway(transport)
        order = gateway.submit_market(client_order_id='precision', symbol='BTC/USDT', side=OrderSide.BUY,
            quantity=quantity, reference_price=Decimal('100'),
            precision=MarketPrecision(Decimal('0.000000000000000001'), Decimal('0.000000000000000001'), Decimal('5')),
            risk_decision=self.decide(engine, quantity=str(quantity)), created_at=self.now)
        self.assertEqual(Decimal(str(order.requested_quantity)), quantity)

    @staticmethod
    def gateway(transport, store=None):
        # Readiness flags are fixture inputs, not real scientific promotion.
        readiness = evaluate_live_readiness(LiveReadinessChecklist(
            scientific_promotion_passed=True, forward_paper_reconciled=True,
            order_reconciliation_validated=True, risk_constitution_unified=True))
        return TestnetExecutionGateway(transport=transport,
            store=store or MemoryTestnetOrderStore(), readiness=readiness)

    def test_timeout_does_not_resend_and_reconciliation_restores_identity(self):
        transport = SyntheticTransport(timeout=True)
        gateway = self.gateway(transport)
        arguments = dict(client_order_id='timeout', symbol='BTC/USDT', side=OrderSide.BUY,
            quantity=Decimal('1'), reference_price=Decimal('100'),
            precision=MarketPrecision(Decimal('0.001'), Decimal('0.001'), Decimal('5')),
            risk_decision=self.decide(OperationalRiskEngine()), created_at=self.now)
        with self.assertRaises(AmbiguousTestnetSubmission): gateway.submit_market(**arguments)
        with self.assertRaises(RuntimeError): gateway.submit_market(**arguments)
        self.assertEqual(transport.calls, 1)
        self.assertEqual(gateway.reconcile('timeout').state, OrderLifecycle.ACKNOWLEDGED)

    def test_overfill_identity_and_terminal_invariants(self):
        local = ReconciledOrder('identity', 'BTC/USDT', 'buy', 1.0,
            state=OrderLifecycle.PARTIAL, venue_order_id='venue', matched_quantity=0.8)
        for snapshot in [VenueOrderSnapshot('identity','venue',OrderLifecycle.FILLED,Decimal('1.01')),
                         VenueOrderSnapshot('other','venue',OrderLifecycle.PARTIAL,Decimal('0.8')),
                         VenueOrderSnapshot('identity','other',OrderLifecycle.PARTIAL,Decimal('0.8'))]:
            with self.assertRaises(TestnetSafetyError): TestnetExecutionGateway._merge_snapshot(local,snapshot)
        terminal = replace(local, state=OrderLifecycle.FILLED, matched_quantity=1.0)
        snapshot = VenueOrderSnapshot('identity','venue',OrderLifecycle.ACKNOWLEDGED,Decimal('1'))
        self.assertEqual(TestnetExecutionGateway._merge_snapshot(terminal,snapshot).state,OrderLifecycle.FILLED)


if __name__ == '__main__': unittest.main()
