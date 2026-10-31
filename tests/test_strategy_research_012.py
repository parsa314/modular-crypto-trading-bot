"""Synthetic unit fixtures only; not trading or model-performance evidence."""
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

import pandas as pd
from research_bot.strategies.common import StrategySignal
from research_bot.strategy_research_012 import baseline_candidates


class FakeEngine:
    def __init__(self, name): self.name = name
    def evaluate(self, frame, *, as_of):
        return StrategySignal(self.name, "BUY", 2, frame.available_at.iloc[-1].to_pydatetime(),
                              ("SYNTHETIC_EVENT",), {"last_close": float(frame.close.iloc[-1])})


class CausalResearchTests(unittest.TestCase):
    def setUp(self):
        start = datetime(2024,1,1,tzinfo=timezone.utc)
        times = [start + timedelta(hours=i) for i in range(96)]
        self.frame = pd.DataFrame({"timestamp": times, "available_at": [x+timedelta(hours=1) for x in times],
            "open": [100.0]*96, "high": [101.0]*96, "low": [99.0]*96,
            "close": [100.0]*96, "volume": [1.0]*96})
        self.args = dict(symbol="BTC/USDT", timeframe="H1", as_of=times[95], source_id="SYNTHETIC",
                         source_sha256="a"*64, availability_kind="HISTORICAL_CLOSE_ASSUMED")
        self.fake = {n:FakeEngine(n) for n in ("ICHIMOKU","ICT","SMC","AL_BROOKS")}

    def run_events(self):
        with patch("research_bot.strategy_research_012._ENGINES", self.fake):
            return baseline_candidates(self.frame,**self.args)

    def test_future_bar_cannot_change_baseline_events(self):
        before=self.run_events()
        self.frame.loc[95,["open","high","low","close"]] = [900,1000,800,950]
        after=self.run_events()
        self.assertEqual(before,after)
        self.assertEqual(len(before),4)
        self.assertEqual(before[0].features["consumed_rows"],95)

    def test_scores_are_not_probabilities_or_executable(self):
        for event in self.run_events():
            self.assertFalse(event.features["score_is_probability"])
            self.assertFalse(event.eligible_for_execution)
            self.assertIsNone(event.invalidation)
            self.assertIsNone(event.expiry)
            self.assertEqual(event.availability_kind,"HISTORICAL_CLOSE_ASSUMED")

    def test_available_before_candle_close_is_rejected(self):
        self.frame.loc[50,"available_at"] = self.frame.loc[50,"timestamp"] + timedelta(minutes=59)
        with self.assertRaisesRegex(ValueError,"BAR_NOT_CLOSED_WHEN_AVAILABLE"):
            self.run_events()

    def test_missing_or_duplicate_bar_is_rejected(self):
        self.frame=self.frame.drop(index=50)
        with self.assertRaisesRegex(ValueError,"NONCONTIGUOUS_BARS"):
            self.run_events()
        self.frame=pd.concat([self.frame,self.frame.iloc[[0]]],ignore_index=True).sort_values("timestamp")
        with self.assertRaisesRegex(ValueError,"DUPLICATE"):
            self.run_events()

    def test_misaligned_hourly_open_is_rejected(self):
        self.frame["timestamp"] = self.frame["timestamp"] + timedelta(minutes=5)
        self.frame["available_at"] = self.frame["available_at"] + timedelta(minutes=5)
        with self.assertRaisesRegex(ValueError,"BAR_OPEN_MISALIGNED"):
            self.run_events()

    def test_naive_as_of_and_unlabeled_source_are_rejected(self):
        self.args["as_of"]=datetime(2024,1,5)
        with self.assertRaisesRegex(ValueError,"TIMEZONE_AWARE"):
            self.run_events()
        self.args["as_of"]=datetime(2024,1,5,tzinfo=timezone.utc)
        self.args["availability_kind"]="PROVEN"
        with self.assertRaisesRegex(ValueError,"AVAILABILITY_PROVENANCE"):
            self.run_events()

    def test_baseline_runs_without_fake_engine(self):
        result=baseline_candidates(self.frame,**self.args)
        self.assertTrue(all(not x.eligible_for_execution for x in result))


if __name__ == "__main__": unittest.main()
