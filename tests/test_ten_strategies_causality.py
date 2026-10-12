"""All ten registered rules must be invariant to unavailable future bars.

These are engineering anti-lookahead checks, NOT proof of OOS profitability.
The input/decision clock, confirms and fill assumptions are separately gated.
"""
from __future__ import annotations

import unittest

import pandas as pd

from research_bot.ten_strategies.detectors import detect
from research_bot.ten_strategies.rules import entry_flags
from research_bot.ten_strategies.protocol import TenConfig, IDS
from research_bot.ten_strategies.replay import replay_column
from scripts.run_four_systems_research import synthetic_bars


class TenRuleCausalityTests(unittest.TestCase):
    def test_all_ten_entry_flags_stable_on_prefix_and_changed_future(self):
        candles = synthetic_bars(420)
        cut = 294
        full = detect(candles)
        prefix = detect(candles.iloc[:cut].copy())
        changed = candles.copy()
        changed.loc[cut:, ["open", "high", "low", "close"]] *= 2.5
        future = detect(changed)
        self.assertEqual(len(IDS), 10)
        for i in range(85, cut):
            with self.subTest(index=i):
                a = entry_flags(full.iloc[i])
                b = entry_flags(prefix.iloc[i])
                c = entry_flags(future.iloc[i])
                self.assertEqual(len(a), 10)
                self.assertEqual(a, b)
                self.assertEqual(a, c)
                self.assertEqual(full.available_at.iloc[i], prefix.available_at.iloc[i])
                self.assertEqual(full.available_at.iloc[i], future.available_at.iloc[i])
        for event in prefix.attrs["events"]:
            self.assertLess(event["confirmed"], cut)
            self.assertGreaterEqual(
                pd.Timestamp(event["available_at"]),
                prefix.available_at.iloc[event["confirmed"]],
            )

    def test_each_ten_rule_replay_prefix_equity_stable(self):
        candles = synthetic_bars(420)
        cut = 294
        full = detect(candles)
        prefix = detect(candles.iloc[:cut].copy())
        config = TenConfig()
        for strategy in IDS:
            with self.subTest(strategy=strategy):
                original = replay_column(full, strategy, config)
                prior = replay_column(prefix, strategy, config)
                self.assertEqual(original.returns[:cut], prior.returns)
                self.assertEqual(original.equity[:cut], prior.equity)
                self.assertTrue(all(
                    trade["strategy"] == strategy for trade in prior.trades
                ))


if __name__ == "__main__":
    unittest.main()
