"""SIMULATED TEST: explicit incremental fee events on isolated SQLite."""
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from research_bot.paper_ledger_008 import PaperLedger


class PaperFeeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/"paper.db"
        self.l=PaperLedger(self.path)
        self.t=datetime(2024,1,1,tzinfo=timezone.utc)
        self.l.fill(order_id="buy",side="BUY",quantity=Decimal("1"),reference_price=Decimal("100"),
                    decision_at=self.t,fill_at=self.t+timedelta(hours=1),fee_bps=Decimal("0"),
                    spread_bps=Decimal("0"),slippage_bps=Decimal("0"))
        self.fee=dict(event_id="fee-1",order_id="buy",amount=Decimal("0.25"),currency="USDT",
                      source="synthetic-test",observed_at=self.t+timedelta(hours=2))

    def tearDown(self): self.tmp.cleanup()

    def test_late_fee_unique_duplicate_and_conflict(self):
        first=self.l.settle_additional_fee(**self.fee)
        self.assertEqual(Decimal(first["cash"]),Decimal("9899.75"))
        self.assertEqual(Decimal(first["basis"]),Decimal("100.25"))
        self.assertFalse(self.l.risk_status()["anchor_valid"])
        self.assertTrue(PaperLedger(self.path).settle_additional_fee(**self.fee)["duplicate"])
        with self.assertRaisesRegex(ValueError,"FEE_EVENT_CONFLICT"):
            self.l.settle_additional_fee(**{**self.fee,"amount":Decimal("0.30")})
        with self.assertRaisesRegex(ValueError,"ORDER_FEE_ALREADY_ADJUSTED"):
            self.l.settle_additional_fee(**{**self.fee,"event_id":"different"})

    def test_late_fee_latches_risk_before_restart(self):
        self.l.settle_additional_fee(**{**self.fee,"amount":Decimal("500")})
        state=PaperLedger(self.path).risk_status()
        self.assertTrue(state["daily_halt"])
        self.assertTrue(state["hard_halt"])

    def test_unknown_and_nonquote_fees_fail_closed(self):
        before=self.l.snapshot()
        with self.assertRaisesRegex(ValueError,"UNKNOWN_FEE"):
            self.l.settle_additional_fee(**{**self.fee,"amount":None})
        with self.assertRaisesRegex(ValueError,"UNVERIFIED_FEE_EVIDENCE"):
            self.l.settle_additional_fee(**{**self.fee,"currency":"BTC"})
        self.assertEqual(before,self.l.snapshot())

    def test_journal_detects_cash_tampering_before_duplicate_or_new_fill(self):
        self.l.settle_additional_fee(**self.fee)
        self.assertEqual(Decimal(self.l.reconcile()["cash"]),Decimal("9899.75"))
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE paper_account SET cash='1' WHERE id=1")
        with self.assertRaisesRegex(ValueError,"PAPER_ACCOUNTING_MISMATCH"):
            self.l.reconcile()
        with self.assertRaisesRegex(ValueError,"PAPER_ACCOUNTING_MISMATCH"):
            self.l.settle_additional_fee(**self.fee)

    def test_late_buy_fee_after_subsequent_trade_requires_lot_reconciliation(self):
        self.l.fill(order_id="sell",side="SELL",quantity=Decimal("0.5"),reference_price=Decimal("110"),
                    decision_at=self.t+timedelta(hours=1),fill_at=self.t+timedelta(hours=2),
                    fee_bps=Decimal("0"),spread_bps=Decimal("0"),slippage_bps=Decimal("0"))
        with self.assertRaisesRegex(ValueError,"LATE_FEE_REQUIRES_LOT_RECONCILIATION"):
            self.l.settle_additional_fee(**self.fee)

    def test_legacy_nonempty_ledger_remains_unverified(self):
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TABLE paper_events")
            db.execute("DROP TABLE paper_journal_meta")
        upgraded=PaperLedger(self.path)
        with self.assertRaisesRegex(ValueError,"LEGACY_LEDGER_UNVERIFIED"):
            upgraded.reconcile()
        self.assertEqual(Decimal(upgraded.snapshot()["cash"]),Decimal("9900"))

    def test_crash_during_fee_transaction_rolls_back_then_recovers_once(self):
        pid=os.fork()
        if pid == 0:
            real_connect=sqlite3.connect
            class CrashConnection(sqlite3.Connection):
                def execute(self,sql,parameters=()):
                    result=super().execute(sql,parameters)
                    if sql.startswith("UPDATE paper_account SET cash=?,basis="):
                        os._exit(75)
                    return result
            def connect(*args,**kwargs):
                return real_connect(*args,**{**kwargs,"factory":CrashConnection})
            with patch("research_bot.paper_ledger_008.sqlite3.connect",side_effect=connect):
                self.l.settle_additional_fee(**self.fee)
            os._exit(70)
        _,status=os.waitpid(pid,0)
        self.assertEqual(os.waitstatus_to_exitcode(status),75)
        self.assertEqual(Decimal(PaperLedger(self.path).snapshot()["cash"]),Decimal("9900"))
        self.assertFalse(PaperLedger(self.path).settle_additional_fee(**self.fee)["duplicate"])
        self.assertTrue(PaperLedger(self.path).settle_additional_fee(**self.fee)["duplicate"])
        self.assertEqual(Decimal(PaperLedger(self.path).snapshot()["cash"]),Decimal("9899.75"))


if __name__ == "__main__": unittest.main()
