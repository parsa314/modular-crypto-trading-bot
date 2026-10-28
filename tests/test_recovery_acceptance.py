"""Recovery-program acceptance gates; run against the pinned canonical source.

Synthetic inputs only. Failing gates document unreconciled requirements, and
must not be reclassified as a strategy result or fixed by weakening assertions.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from research_bot.orchestrator import ResearchTradingOrchestrator
from research_bot.order_reconciliation_v52 import OrderLifecycle, ReconciledOrder
from research_bot.risk import RiskEngine, RiskSnapshot
from research_bot.operational_risk import BrokerRiskContext, OperationalRiskEngine
from research_bot.testnet_execution import (
    TestnetExecutionGateway, TestnetSafetyError, VenueOrderSnapshot,
)


class RecoveryAcceptanceTests(unittest.TestCase):
    @staticmethod
    def risk_snapshot(equity: float = 940.0) -> RiskSnapshot:
        return RiskSnapshot(
            equity=equity, peak_equity=1000.0, gross_exposure=0.1,
            asset_weight=0.1, turnover=0.0, spread_bps=1.0,
            slippage_bps=1.0,
        )

    def test_first_release_halts_new_entries_at_five_percent_drawdown(self) -> None:
        decision = OperationalRiskEngine().evaluate(self.risk_snapshot())
        self.assertFalse(decision.approved)
        self.assertTrue(decision.kill_switch)

    def test_cumulative_partial_fill_cannot_decrease(self) -> None:
        local = ReconciledOrder(
            client_order_id="synthetic", symbol="BTC/USDT", side="buy",
            requested_quantity=1.0, matched_quantity=0.8,
            venue_order_id="synthetic-venue", state=OrderLifecycle.PARTIAL,
        )
        snapshot = VenueOrderSnapshot(
            client_order_id="synthetic", venue_order_id="synthetic-venue",
            state=OrderLifecycle.PARTIAL, matched_quantity=Decimal("0.2"),
        )
        with self.assertRaises(TestnetSafetyError):
            TestnetExecutionGateway._merge_snapshot(local, snapshot)

    def test_filled_state_requires_complete_cumulative_quantity(self) -> None:
        local = ReconciledOrder(
            client_order_id="synthetic", symbol="BTC/USDT", side="buy",
            requested_quantity=1.0, state=OrderLifecycle.SUBMITTING,
        )
        snapshot = VenueOrderSnapshot(
            client_order_id="synthetic", venue_order_id="synthetic-venue",
            state=OrderLifecycle.FILLED, matched_quantity=Decimal("0.2"),
        )
        with self.assertRaises(TestnetSafetyError):
            TestnetExecutionGateway._merge_snapshot(local, snapshot)

    def test_consistent_complete_fill_is_accepted(self) -> None:
        local = ReconciledOrder(
            client_order_id="synthetic", symbol="BTC/USDT", side="buy",
            requested_quantity=1.0, matched_quantity=0.8,
            venue_order_id="synthetic-venue", state=OrderLifecycle.PARTIAL,
        )
        snapshot = VenueOrderSnapshot(
            client_order_id="synthetic", venue_order_id="synthetic-venue",
            state=OrderLifecycle.FILLED, matched_quantity=Decimal("1.0"),
        )
        merged = TestnetExecutionGateway._merge_snapshot(local, snapshot)
        self.assertEqual(merged.state, OrderLifecycle.FILLED)
        self.assertEqual(merged.matched_quantity, 1.0)

    def test_new_entry_halt_still_allows_existing_position_exit_review(self) -> None:
        now = datetime(2026, 10, 28, tzinfo=timezone.utc)
        context = BrokerRiskContext(
            account_id="SYNTHETIC_UNIT_TEST", symbol="BTCUSDT",
            reconciled_at=now, quote_at=now, position_quantity=Decimal("0.1"),
            reconciled=True, unresolved_orders=False,
        )
        outcome = ResearchTradingOrchestrator.for_recovery_spot_h4().process_forecast(
            asset="BTC", symbol="BTC/USDT",
            timestamp=datetime(2026, 10, 28, tzinfo=timezone.utc),
            expected_return=-0.1, expected_cost=0.0024,
            risk_penalty=0.0, uncertainty_penalty=0.0, confidence=0.9,
            currently_long=True, risk_snapshot=self.risk_snapshot(800.0),
            reference_price=100.0, quantity=0.1,
            client_order_id="synthetic-exit",
            operational_context=context,
        )
        self.assertTrue(outcome.risk.approved)
        self.assertEqual(outcome.fill.side.value, "SELL")

    def test_legacy_research_defaults_remain_unchanged(self) -> None:
        self.assertEqual(RiskEngine().limits.max_drawdown, 0.12)
        self.assertEqual(RiskEngine().limits.max_gross_exposure, 1.0)


if __name__ == "__main__":
    unittest.main()
