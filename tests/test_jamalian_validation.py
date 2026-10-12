"""Independent causality and support checks for the diagnostic harness."""
import unittest

import numpy as np
import pandas as pd

from research_bot.jamalian_validation import ValidationHarness, purged_combinatorial_splits


class JamalianValidationTests(unittest.TestCase):
    def setUp(self):
        self.starts = pd.date_range("2026-01-01", periods=240, freq="h", tz="UTC")
        self.ends = self.starts + pd.Timedelta(hours=3)
        rng = np.random.default_rng(708)
        self.returns = pd.DataFrame(rng.normal(0, .001, size=(240, 3)), index=self.starts,
            columns=["frozen_a", "frozen_b", "frozen_c"])

    def diagnose(self, frame=None, **kwargs):
        params = dict(dataset_sha256="a" * 64, trial_count=3, data_class="SYNTHETIC_SMOKE")
        params.update(kwargs)
        return ValidationHarness().diagnose(self.returns if frame is None else frame, self.ends, **params)

    def test_cpcv_purges_overlapping_labels_and_embargo(self):
        embargo = pd.Timedelta(hours=2)
        folds = list(purged_combinatorial_splits(self.starts, self.ends, embargo=embargo))
        self.assertEqual(len(folds), 15)
        for train, test in folds:
            self.assertFalse(set(train) & set(test))
            for i in train:
                for j in test:
                    self.assertTrue(self.ends[i] < self.starts[j] or self.starts[i] > self.ends[j] + embargo)

    def test_diagnostics_are_labelled_synthetic_and_cannot_promote(self):
        result = self.diagnose()
        self.assertEqual(result["data_class"], "SYNTHETIC_SMOKE")
        self.assertEqual(result["cscv_pbo"]["splits"], 20)
        self.assertEqual(len(result["cpcv_fixed_path_selection"]), 15)
        self.assertFalse(result["paper_execution"])
        self.assertFalse(result["profitable_alpha_claim"])
        self.assertTrue(all(0 <= x <= 1 for x in result["dsr_per_period"].values()))

    def test_no_nan_or_infinity_rows_can_be_dropped(self):
        for value in (np.nan, np.inf):
            frame = self.returns.copy()
            frame.iloc[10, 1] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.diagnose(frame)

    def test_hidden_trials_and_sealed_data_are_rejected(self):
        for args in ({"trial_count": 4}, {"data_class": "KRAKEN_HOLDOUT"}, {"n_groups": 5},
                     {"dataset_sha256": "missing"}, {"embargo": pd.Timedelta(hours=-1)}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.diagnose(**args)

    def test_short_constant_or_duplicate_paths_are_not_success(self):
        with self.assertRaises(ValueError):
            self.diagnose(self.returns.iloc[:60])
        for frame in (self.returns.assign(frozen_a=0), self.returns.assign(frozen_b=self.returns.frozen_a)):
            with self.assertRaises(ValueError):
                self.diagnose(frame)

    def test_naive_duplicate_or_backward_times_rejected(self):
        for starts in (self.starts.tz_localize(None), self.starts[::-1], self.starts[:1].append(self.starts[:-1])):
            with self.assertRaises(ValueError):
                list(purged_combinatorial_splits(starts, self.ends))


if __name__ == "__main__":
    unittest.main()
