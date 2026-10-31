"""Synthetic engineering fixtures only, not market-performance evidence."""
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from research_bot.paper_replay_008 import replay
from research_bot.contracts import Decision
from research_bot.paper_ledger_008 import PaperLedger
from datetime import timedelta


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / "fixture.csv"
        self.state = self.root / "state.sqlite"
        dates = pd.date_range("2024-01-01", periods=100, freq="h", tz="UTC")
        pd.DataFrame({"timestamp":dates, "open": [100]*100, "high":[101]*100,
                      "low":[99]*100, "close":[100]*100, "volume":[5]*100}).to_csv(self.path,index=False)
        self.as_of = datetime(2024,1,5,4,tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def run_replay(self, **kwargs):
        return replay(self.path, self.state, as_of=self.as_of, source="FIXTURE",
                      fee_bps=Decimal("10"), spread_bps=kwargs.get("spread",Decimal("3")),
                      slippage_bps=Decimal("2"), initial_cash=kwargs.get("cash",Decimal("10000")))

    def test_four_arms_cash_baseline_and_restart_idempotency(self):
        first = self.run_replay()
        second = self.run_replay()
        self.assertEqual(set(first["signals"]), {"ICHIMOKU", "ICT", "SMC", "AL_BROOKS"})
        self.assertEqual(first["decision"], "NO_TRADE")
        self.assertEqual((first["cash"], first["position"], first["fees"], first["fills"]), ("10000", "0", "0", 0))
        self.assertTrue(first["new_record"])
        self.assertFalse(second["new_record"])
        with sqlite3.connect(self.state) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM replay_evidence").fetchone()[0], 1)

    def test_future_bar_does_not_change_signal(self):
        before = self.run_replay()
        data = pd.read_csv(self.path)
        future = data.iloc[-1].copy()
        future["timestamp"] = "2024-01-06T00:00:00Z"
        future["close"] = 100000
        future["high"] = 100001
        pd.concat([data,pd.DataFrame([future])],ignore_index=True).to_csv(self.path,index=False)
        after = self.run_replay()
        self.assertEqual(before["signals"], after["signals"])
        self.assertEqual(before["decision"], after["decision"])

    def test_duplicates_and_coverage_gap_are_rejected(self):
        data = pd.read_csv(self.path)
        pd.concat([data,data.iloc[[-1]]],ignore_index=True).to_csv(self.path,index=False)
        with self.assertRaisesRegex(ValueError,"DUPLICATE"):
            self.run_replay()
        data.drop(index=50).to_csv(self.path,index=False)
        with self.assertRaisesRegex(ValueError,"COVERAGE_GAP"):
            self.run_replay()

    def test_risk_rejection_and_invalid_cash(self):
        self.assertFalse(self.run_replay(spread=Decimal("36"))["risk_approved"])
        with self.assertRaisesRegex(ValueError,"INVALID_INITIAL_CASH"):
            self.run_replay(cash=Decimal("0"))

    def test_cached_replay_rejects_tampered_account(self):
        self.run_replay()
        with sqlite3.connect(self.state) as db:
            db.execute("UPDATE paper_account SET cash='1' WHERE id=1")
        with self.assertRaisesRegex(ValueError,"PAPER_ACCOUNTING_MISMATCH"):
            self.run_replay()

    def test_injected_candidate_flows_to_later_paper_fill_and_restart(self):
        class SyntheticDecision:
            def evaluate(self, **kwargs):
                return SimpleNamespace(decision=Decision.HOLD if kwargs["currently_long"] else Decision.BUY_CANDIDATE)
        with patch("research_bot.paper_replay_008.DecisionEngine", return_value=SyntheticDecision()):
            first=self.run_replay()
            second=self.run_replay()
        self.assertEqual(first["fills"], 1)
        self.assertFalse(second["new_record"])
        self.assertEqual(first["cash"], second["cash"])
        self.assertGreater(Decimal(first["position"]), Decimal("0"))

    def test_cached_replay_separates_historical_result_from_current_fee_adjusted_account(self):
        class SyntheticDecision:
            def evaluate(self, **kwargs): return SimpleNamespace(decision=Decision.BUY_CANDIDATE)
        with patch("research_bot.paper_replay_008.DecisionEngine", return_value=SyntheticDecision()):
            first=self.run_replay()
        ledger=PaperLedger(self.state)
        ledger.settle_additional_fee(event_id="late",order_id=first["fill"]["order_id"],
            amount=Decimal("0.25"),currency="USDT",source="SYNTHETIC",
            observed_at=self.as_of+timedelta(hours=1))
        resumed=self.run_replay()
        self.assertEqual(Decimal(resumed["current_account"]["cash"]),Decimal(first["cash"])-Decimal("0.25"))
        self.assertEqual(resumed["cash"],first["cash"])
        self.assertFalse(resumed["new_record"])

    def test_older_replay_cannot_consume_future_account_state(self):
        ledger=PaperLedger(self.state)
        later=self.as_of+timedelta(hours=2)
        ledger.fill(order_id="future",side="BUY",quantity=Decimal("1"),reference_price=Decimal("100"),
                    decision_at=self.as_of,fill_at=later,fee_bps=Decimal("10"),
                    spread_bps=Decimal("3"),slippage_bps=Decimal("2"))
        with self.assertRaisesRegex(ValueError,"PAPER_STATE_FROM_FUTURE"):
            self.run_replay()

    def test_next_bar_price_cannot_change_prior_order_quantity(self):
        class SyntheticDecision:
            def evaluate(self, **kwargs):
                return SimpleNamespace(decision=Decision.BUY_CANDIDATE)
        with patch("research_bot.paper_replay_008.DecisionEngine", return_value=SyntheticDecision()):
            first=self.run_replay()
            data=pd.read_csv(self.path)
            data.loc[data.index[-1],"close"]=150
            data.loc[data.index[-1],"high"]=151
            data.to_csv(self.path,index=False)
            self.state=self.root/"second.sqlite"
            second=self.run_replay()
        self.assertEqual(first["fill"]["quantity"],second["fill"]["quantity"])
        self.assertNotEqual(first["fill"]["fill_price"],second["fill"]["fill_price"])

    def test_runner_allows_reducing_exit_while_hard_halted(self):
        ledger=PaperLedger(self.state)
        t=datetime(2024,1,1,tzinfo=timezone.utc)
        ledger.fill(order_id="entry",side="BUY",quantity=Decimal("30"),reference_price=Decimal("100"),
                    decision_at=t,fill_at=t+timedelta(hours=1),fee_bps=Decimal("0"),
                    spread_bps=Decimal("0"),slippage_bps=Decimal("0"))
        ledger.mark(Decimal("80"),observed_at=t+timedelta(hours=2))
        data=pd.read_csv(self.path)
        data.loc[:,"open"]=80; data.loc[:,"high"]=81
        data.loc[:,"low"]=79; data.loc[:,"close"]=80
        data.to_csv(self.path,index=False)
        class SyntheticExit:
            def evaluate(self, **kwargs): return SimpleNamespace(decision=Decision.EXIT)
        with patch("research_bot.paper_replay_008.DecisionEngine",return_value=SyntheticExit()):
            result=self.run_replay()
        self.assertFalse(result["risk_approved"])
        self.assertTrue(result["persistent_risk_status"]["hard_halt"])
        self.assertEqual(result["fills"],1)
        self.assertEqual(Decimal(result["position"]),Decimal("0"))


if __name__ == "__main__":
    unittest.main()
