"""Tests of spot execution invariants using an independently controlled signal."""

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import numpy as np
import pandas as pd

from research_bot.spot_ichimoku_bot import SpotBacktestConfig, backtest_spot_ichimoku


def bars(n=125):
    ts = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    prices = np.full(n, 100.0)
    prices[120] = 110
    prices[121] = 120
    return pd.DataFrame({"timestamp": ts, "open": prices, "high": prices + 2,
                         "low": prices - 2, "close": prices, "volume": np.ones(n)})


class SpotBacktestTests(unittest.TestCase):
    def test_buy_is_next_open_and_sell_does_not_short(self):
        def strategy(frame, currently_long, threshold):
            action = "BUY_CANDIDATE" if len(frame) == 120 else "EXIT"
            return type("Signal", (), {"action": action})()

        with patch("research_bot.spot_ichimoku_bot.frozen_shadow_signal", side_effect=strategy):
            report = backtest_spot_ichimoku(
                bars(), observed_at=datetime(2025, 2, 1, tzinfo=timezone.utc),
                config=SpotBacktestConfig(fee_bps=0, slippage_bps=0),
            )
        self.assertEqual([t["side"] for t in report["trades"]], ["BUY", "SELL"])
        self.assertEqual([t["fill_price"] for t in report["trades"]], [110, 120])
        self.assertAlmostEqual(report["total_return"], (2000 / 110 * 10) / 10000)
        self.assertEqual(report["final_base_quantity"], 0)

    def test_gap_is_rejected(self):
        broken = bars().drop(index=4)
        with self.assertRaisesRegex(ValueError, "missing bars"):
            backtest_spot_ichimoku(broken)

    def test_unclosed_last_bar_is_excluded(self):
        with patch("research_bot.spot_ichimoku_bot.frozen_shadow_signal") as signal:
            signal.return_value.action = "NO_TRADE"
            result = backtest_spot_ichimoku(
                bars(), observed_at=bars().iloc[-1]["timestamp"].to_pydatetime(),
            )
        self.assertEqual(result["bars"], 124)


if __name__ == "__main__":
    unittest.main()
