"""SIMULATED TEST: OS process termination on isolated SQLite fixtures."""
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pandas as pd
from research_bot.contracts import Decision
from research_bot.paper_ledger_008 import PaperLedger
from research_bot.paper_replay_008 import replay


class PaperCrashTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.db=self.root/"paper.sqlite"
        self.t=datetime(2024,1,1,tzinfo=timezone.utc)
        self.args=dict(order_id="crash-fixture",side="BUY",quantity=Decimal("1"),
                       reference_price=Decimal("100"),decision_at=self.t,
                       fill_at=self.t+timedelta(hours=1),fee_bps=Decimal("10"),
                       spread_bps=Decimal("0"),slippage_bps=Decimal("0"))

    def tearDown(self): self.tmp.cleanup()

    def fork_exit(self, fn):
        pid=os.fork()
        if pid == 0:
            try: fn()
            finally: os._exit(70)
        _,status=os.waitpid(pid,0)
        return os.waitstatus_to_exitcode(status)

    def kill_during_account_update(self, ledger, args):
        real_connect=sqlite3.connect
        class CrashConnection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                result=super().execute(sql,parameters)
                if sql.startswith("UPDATE paper_account SET cash="):
                    os._exit(73)
                return result
        def connect(*args,**kwargs):
            return real_connect(*args,**{**kwargs,"factory":CrashConnection})
        with patch("research_bot.paper_ledger_008.sqlite3.connect",side_effect=connect):
            ledger.fill(**args)

    def test_process_death_during_fill_rolls_back_and_retry_applies_once(self):
        ledger=PaperLedger(self.db)
        self.assertEqual(self.fork_exit(lambda:self.kill_during_account_update(ledger,self.args)),73)
        self.assertEqual(PaperLedger(self.db).snapshot()["cash"],"10000")
        first=PaperLedger(self.db).fill(**self.args)
        self.assertFalse(first["duplicate"])
        self.assertTrue(PaperLedger(self.db).fill(**self.args)["duplicate"])
        self.assertEqual(Decimal(PaperLedger(self.db).snapshot()["cash"]),Decimal("9899.9"))

    def test_process_death_during_exit_rolls_back_and_retry_does_not_reverse(self):
        ledger=PaperLedger(self.db)
        ledger.fill(**self.args)
        sell={**self.args,"order_id":"exit-fixture","side":"SELL",
              "decision_at":self.t+timedelta(hours=1),"fill_at":self.t+timedelta(hours=2)}
        before=ledger.snapshot()
        self.assertEqual(self.fork_exit(lambda:self.kill_during_account_update(ledger,sell)),73)
        self.assertEqual(PaperLedger(self.db).snapshot(),before)
        PaperLedger(self.db).fill(**sell)
        self.assertTrue(PaperLedger(self.db).fill(**sell)["duplicate"])
        self.assertEqual(Decimal(PaperLedger(self.db).snapshot()["quantity"]),Decimal("0"))

    def test_process_death_after_fill_before_evidence_recovers_pending_intent(self):
        csv=self.root/"synthetic.csv"
        dates=pd.date_range("2024-01-01",periods=100,freq="h",tz="UTC")
        pd.DataFrame({"timestamp":dates,"open":[100]*100,"high":[101]*100,
                      "low":[99]*100,"close":[100]*100,"volume":[5]*100}).to_csv(csv,index=False)
        at=datetime(2024,1,5,4,tzinfo=timezone.utc)
        args=dict(as_of=at,source="SYNTHETIC",fee_bps=Decimal("10"),
                  spread_bps=Decimal("3"),slippage_bps=Decimal("2"))
        class SyntheticDecision:
            def evaluate(self,**kwargs): return SimpleNamespace(decision=Decision.BUY_CANDIDATE)
        def child():
            original=PaperLedger.fill
            def crash_after_commit(instance,**kwargs):
                original(instance,**kwargs)
                os._exit(74)
            with patch("research_bot.paper_replay_008.DecisionEngine",return_value=SyntheticDecision()), \
                 patch.object(PaperLedger,"fill",crash_after_commit):
                replay(csv,self.db,**args)
        self.assertEqual(self.fork_exit(child),74)
        with self.assertRaisesRegex(ValueError,"PAPER_PENDING_RECONCILIATION_REQUIRED"):
            PaperLedger(self.db).fill(**{**self.args,"order_id":"other"})
        resumed=replay(csv,self.db,**args)
        self.assertEqual(resumed["fills"],1)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM paper_fills").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM replay_evidence").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM paper_pending_replay").fetchone()[0],0)
        self.assertFalse(replay(csv,self.db,**args)["new_record"])

    def test_process_death_after_intent_before_fill_retries_once(self):
        csv=self.root/"synthetic-before.csv"
        dates=pd.date_range("2024-01-01",periods=100,freq="h",tz="UTC")
        pd.DataFrame({"timestamp":dates,"open":[100]*100,"high":[101]*100,
                      "low":[99]*100,"close":[100]*100,"volume":[5]*100}).to_csv(csv,index=False)
        args=dict(as_of=datetime(2024,1,5,4,tzinfo=timezone.utc),source="SYNTHETIC-BEFORE",
                  fee_bps=Decimal("10"),spread_bps=Decimal("3"),slippage_bps=Decimal("2"))
        class SyntheticDecision:
            def evaluate(self,**kwargs): return SimpleNamespace(decision=Decision.BUY_CANDIDATE)
        def child():
            with patch("research_bot.paper_replay_008.DecisionEngine",return_value=SyntheticDecision()), \
                 patch.object(PaperLedger,"fill",lambda *a,**k:os._exit(76)):
                replay(csv,self.db,**args)
        self.assertEqual(self.fork_exit(child),76)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM paper_fills").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT count(*) FROM paper_pending_replay").fetchone()[0],1)
        resumed=replay(csv,self.db,**args)
        self.assertEqual(resumed["fills"],1)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM paper_fills").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM paper_pending_replay").fetchone()[0],0)


if __name__ == "__main__": unittest.main()
