"""Synthetic paper-ledger behavior; never contacts a broker."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from research_bot.paper_ledger_008 import PaperLedger


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/"paper.sqlite"
        self.ledger=PaperLedger(self.path)
        self.now=datetime(2024,1,1,tzinfo=timezone.utc)
        self.args=dict(order_id="fixture",side="BUY",quantity=Decimal("0.123456789012345678"),
                       reference_price=Decimal("100"),decision_at=self.now,
                       fill_at=self.now+timedelta(hours=1),fee_bps=Decimal("10"),
                       spread_bps=Decimal("2"),slippage_bps=Decimal("1"))

    def tearDown(self): self.tmp.cleanup()

    def test_exact_buy_restart_and_duplicate(self):
        first=self.ledger.fill(**self.args)
        notional=Decimal("0.123456789012345678")*Decimal("100.0200")
        fee=notional*Decimal("0.001")
        self.assertEqual(Decimal(first["cash"]),Decimal("10000")-notional-fee)
        self.assertEqual(Decimal(first["position"]),self.args["quantity"])
        restarted=PaperLedger(self.path)
        self.assertEqual(Decimal(restarted.snapshot()["cash"]),Decimal(first["cash"]))
        self.assertTrue(restarted.fill(**self.args)["duplicate"])
        self.assertEqual(Decimal(restarted.snapshot()["cash"]),Decimal(first["cash"]))
        with self.assertRaisesRegex(ValueError,"ORDER_ID_CONFLICT"):
            restarted.fill(**{**self.args,"quantity":Decimal("0.1")})

    def test_risk_and_future_fill_rejection_are_atomic(self):
        with self.assertRaisesRegex(ValueError,"RISK_REJECTED"):
            self.ledger.fill(**{**self.args,"quantity":Decimal("40")})
        self.assertEqual(self.ledger.snapshot()["cash"],"10000")
        with self.assertRaisesRegex(ValueError,"FILL_MUST_FOLLOW_DECISION"):
            self.ledger.fill(**{**self.args,"fill_at":self.now})
        with self.assertRaisesRegex(ValueError,"SELL_EXCEEDS_POSITION"):
            self.ledger.fill(**{**self.args,"side":"SELL"})
        self.assertEqual(self.ledger.snapshot()["quantity"],"0")

    def test_marked_peak_persists_across_restart(self):
        self.ledger.fill(**{**self.args,"quantity":Decimal("10")})
        high=self.ledger.mark(Decimal("200"))
        self.assertGreater(Decimal(high["peak"]),Decimal("10000"))
        restarted=PaperLedger(self.path)
        self.assertEqual(restarted.mark(Decimal("100"))["peak"],high["peak"])

    def test_sell_reports_realized_profit_after_both_fees(self):
        buy={**self.args,"quantity":Decimal("1"),"spread_bps":Decimal("0"),"slippage_bps":Decimal("0")}
        self.ledger.fill(**buy)
        sell={**buy,"order_id":"fixture-sell","side":"SELL","reference_price":Decimal("110"),
              "decision_at":self.now+timedelta(hours=1),"fill_at":self.now+timedelta(hours=2)}
        result=self.ledger.fill(**sell)
        self.assertEqual(Decimal(result["realized"]),Decimal("9.79"))
        self.assertEqual(Decimal(result["cash"]),Decimal("10009.79"))
        self.assertEqual(Decimal(result["position"]),Decimal("0"))


if __name__ == "__main__": unittest.main()
