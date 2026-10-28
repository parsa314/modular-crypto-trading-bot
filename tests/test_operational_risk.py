from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from research_bot.operational_risk import BrokerRiskContext, OperationalRiskEngine
from research_bot.risk import RiskSnapshot


class OperationalRiskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
        self.engine = OperationalRiskEngine()
        self.snapshot = RiskSnapshot(1000, 1000, 0.0, 0.0, 0.0, 1.0, 1.0)
        self.context = BrokerRiskContext(
            account_id="SYNTHETIC_UNIT_TEST", symbol="BTCUSDT",
            reconciled_at=self.now, quote_at=self.now, position_quantity=Decimal("1"),
            reconciled=True, unresolved_orders=False,
            day_start_equity=Decimal("1000"), day_start_date_utc="2026-10-28",
        )

    def decide(self, **changes):
        args = dict(snapshot=self.snapshot, symbol="BTC/USDT", side="BUY",
                    quantity=Decimal("1"), price=Decimal("100"), now=self.now,
                    context=self.context, stop_price=Decimal("98"), roundtrip_cost_bps=50)
        args.update(changes)
        return self.engine.evaluate_order(**args)

    def test_stop_and_cost_at_budget_and_above(self) -> None:
        # 1*(100-98) + 100*50/10000 = 2.50 = 0.25% of 1000.
        self.assertTrue(self.decide().approved)
        self.assertFalse(self.decide(quantity=Decimal("1.000001")).approved)

    def test_peak_drawdown_exact_boundary(self) -> None:
        for equity, allowed in [(Decimal("950.001"), True), (Decimal("950"), False), (Decimal("949.999"), False)]:
            with self.subTest(equity=equity):
                verdict = self.engine.evaluate(replace(self.snapshot, equity=equity))
                self.assertEqual(verdict.approved, allowed)

    def test_daily_loss_exact_boundary(self) -> None:
        for equity, allowed in [(Decimal("980.001"), True), (Decimal("980"), False), (Decimal("979.999"), False)]:
            with self.subTest(equity=equity):
                self.assertEqual(self.decide(snapshot=replace(self.snapshot, equity=equity), quantity=Decimal("0.5")).approved, allowed)

    def test_projected_exposure_includes_new_order(self) -> None:
        self.assertTrue(self.decide(snapshot=replace(self.snapshot, gross_exposure=0.60)).approved)
        self.assertFalse(self.decide(snapshot=replace(self.snapshot, gross_exposure=0.60001)).approved)
        self.assertFalse(self.decide(snapshot=replace(self.snapshot, asset_weight=0.251)).approved)

    def test_verified_reduction_during_halt_and_without_day_anchor(self) -> None:
        context = replace(self.context, day_start_equity=None, day_start_date_utc=None)
        self.assertTrue(self.decide(side="SELL", snapshot=replace(self.snapshot, equity=800), context=context).approved)

    def test_reduction_cannot_short_or_spend_reserved_quantity(self) -> None:
        self.assertFalse(self.decide(side="SELL", quantity=Decimal("1.01")).approved)
        self.assertFalse(self.decide(side="SELL", context=replace(self.context, reserved_sell_quantity=Decimal("0.1"))).approved)

    def test_missing_stale_or_unresolved_context_blocks_both_sides(self) -> None:
        for ctx in [None, replace(self.context, reconciled=False), replace(self.context, unresolved_orders=True),
                    replace(self.context, reconciled="true"),
                    replace(self.context, quote_at="2026-10-28T12:00:00Z"),
                    replace(self.context, quote_at=self.now-timedelta(seconds=121)),
                    replace(self.context, reconciled_at=self.now+timedelta(seconds=1))]:
            for side in ["BUY", "SELL"]:
                with self.subTest(context=ctx, side=side):
                    self.assertFalse(self.decide(side=side, context=ctx).approved)

    def test_position_counts_and_cooldown(self) -> None:
        for ctx in [replace(self.context, open_positions=5), replace(self.context, same_direction_positions=3),
                    replace(self.context, open_positions=-1), replace(self.context, last_entry_at=self.now-timedelta(minutes=14, seconds=59))]:
            self.assertFalse(self.decide(context=ctx).approved)
        self.assertTrue(self.decide(context=replace(self.context, last_entry_at=self.now-timedelta(minutes=15))).approved)

    def test_invalid_prices_costs_and_symbols(self) -> None:
        for args in [dict(price=float("nan")), dict(quantity=-1), dict(stop_price=100),
                     dict(stop_price=None), dict(roundtrip_cost_bps=-1), dict(symbol="SOL/USDT"),
                     dict(context=replace(self.context, day_start_date_utc="2026-10-27"))]:
            with self.subTest(args=args):
                self.assertFalse(self.decide(**args).approved)


if __name__ == "__main__":
    unittest.main()
