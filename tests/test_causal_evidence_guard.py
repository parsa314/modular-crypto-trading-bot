"""Production feature prefix/suffix causality regression; no order capability."""
from __future__ import annotations

import unittest

import pandas as pd

from research_bot.four_systems.features import build_features, feature_families
from scripts.run_four_systems_research import synthetic_bars


class FeatureCausalityTests(unittest.TestCase):
    def test_prefix_and_future_perturbation_for_registered_families(self):
        bars = synthetic_bars(500)
        frozen = build_features(bars)
        columns = tuple(name for family in feature_families().values() for name in family) + ("setup",)
        for cut in (219, 277, 381, 450):
            with self.subTest(cut=cut):
                prefix = build_features(bars.iloc[:cut].copy())
                pd.testing.assert_frame_equal(
                    frozen.iloc[:cut][list(columns)].reset_index(drop=True),
                    prefix[list(columns)].reset_index(drop=True),
                    check_flags=False,
                )
                future_changed = bars.copy()
                future_changed.loc[cut:, ["open", "high", "low", "close"]] *= 3
                changed = build_features(future_changed)
                pd.testing.assert_frame_equal(
                    frozen.iloc[:cut][list(columns)].reset_index(drop=True),
                    changed.iloc[:cut][list(columns)].reset_index(drop=True),
                    check_flags=False,
                )

    def test_negative_control_would_detect_future_leak(self):
        prices = pd.Series([100, 101, 102, 103, 104], dtype=float)
        leaked_full = prices.shift(-1).iloc[2]
        leaked_prefix = prices.iloc[:3].shift(-1).iloc[-1]
        self.assertNotEqual(pd.isna(leaked_full), pd.isna(leaked_prefix))


if __name__ == "__main__":
    unittest.main()
