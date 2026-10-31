"""Synthetic engineering fixtures only, not market-performance evidence."""
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd

from research_bot.paper_replay_008 import replay


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


if __name__ == "__main__":
    unittest.main()
