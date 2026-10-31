"""Synthetic fixtures: durable halts and risk-reducing exits."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from research_bot.paper_ledger_008 import PaperLedger


class PaperRiskTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/"paper.db"
        self.l=PaperLedger(self.path)
        self.t=datetime(2024,1,1,tzinfo=timezone.utc)
        self.base=dict(side="BUY",quantity=Decimal("30"),reference_price=Decimal("100"),
                       decision_at=self.t,fill_at=self.t+timedelta(hours=1),
                       fee_bps=Decimal("0"),spread_bps=Decimal("0"),slippage_bps=Decimal("0"))

    def tearDown(self): self.tmp.cleanup()

    def test_hard_halt_survives_restart_and_allows_reducing_exit(self):
        self.l.fill(order_id="entry",**self.base)
        self.l.mark(Decimal("80"),observed_at=self.t+timedelta(hours=2))
        l=PaperLedger(self.path)
        self.assertTrue(l.risk_status()["hard_halt"])
        args={**self.base,"quantity":Decimal("1"),"decision_at":self.t+timedelta(hours=2),
              "fill_at":self.t+timedelta(hours=3),"reference_price":Decimal("110")}
        with self.assertRaisesRegex(ValueError,"PAPER_RISK_HALTED"):
            l.fill(order_id="new-entry",**args)
        result=l.fill(order_id="reduce",**{**args,"side":"SELL"})
        self.assertEqual(Decimal(result["position"]),Decimal("29"))
        with self.assertRaisesRegex(ValueError,"SELL_EXCEEDS_POSITION"):
            l.fill(order_id="over-exit",**{**args,"side":"SELL","quantity":Decimal("100")})

    def test_daily_halt_persists_after_equity_recovers(self):
        self.l.fill(order_id="entry",**self.base)
        self.l.mark(Decimal("93"),observed_at=self.t+timedelta(hours=2))
        self.assertTrue(PaperLedger(self.path).risk_status()["daily_halt"])
        self.l.mark(Decimal("105"),observed_at=self.t+timedelta(hours=3))
        self.assertTrue(PaperLedger(self.path).risk_status()["daily_halt"])

    def test_exact_daily_boundary_and_verified_rollover(self):
        self.l.fill(order_id="entry",**{**self.base,"quantity":Decimal("20")})
        self.l.mark(Decimal("90"),observed_at=self.t+timedelta(hours=2))
        state=self.l.risk_status()
        self.assertTrue(state["daily_halt"])
        self.assertFalse(state["hard_halt"])
        self.l.mark(Decimal("100"),observed_at=self.t+timedelta(days=1))
        self.assertTrue(self.l.risk_status()["anchor_valid"])
        self.assertFalse(self.l.risk_status()["daily_halt"])

    def test_missing_utc_anchor_blocks_new_entries(self):
        self.l.fill(order_id="entry",**self.base)
        later=self.t+timedelta(days=1,hours=3)
        self.l.mark(Decimal("100"),observed_at=later)
        self.assertFalse(self.l.risk_status()["anchor_valid"])
        with self.assertRaisesRegex(ValueError,"PAPER_RISK_HALTED"):
            self.l.fill(order_id="blocked",**{**self.base,"quantity":Decimal("1"),
                "decision_at":later,"fill_at":later+timedelta(hours=1)})

    def test_same_timestamp_conflicting_price_fails_closed(self):
        self.l.mark(Decimal("100"),observed_at=self.t)
        with self.assertRaisesRegex(ValueError,"CONFLICTING_PRICE_SAME_TIME"):
            self.l.mark(Decimal("101"),observed_at=self.t)

    def test_flat_account_does_not_invent_next_day_open(self):
        self.l.fill(order_id="entry",**self.base)
        self.l.fill(order_id="exit",**{**self.base,"side":"SELL",
                    "decision_at":self.t+timedelta(hours=1),"fill_at":self.t+timedelta(hours=2)})
        later=self.t+timedelta(days=1,hours=3)
        self.l.mark(Decimal("100"),observed_at=later)
        self.assertFalse(self.l.risk_status()["anchor_valid"])
        with self.assertRaisesRegex(ValueError,"PAPER_RISK_HALTED"):
            self.l.fill(order_id="next-day-entry",**{**self.base,"quantity":Decimal("1"),
                "decision_at":later,"fill_at":later+timedelta(hours=1)})


if __name__ == "__main__": unittest.main()
