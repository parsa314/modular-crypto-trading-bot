import hashlib
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from scripts.collect_coinex_history import collect_one


class HistoricalCollectorTests(unittest.TestCase):
    def setUp(self):
        self.start = pd.Timestamp("2025-01-01T00:00:00Z").to_pydatetime()
        self.cutoff = pd.Timestamp("2025-01-02T00:00:00Z").to_pydatetime()

    def sample(self):
        return pd.DataFrame({"timestamp": pd.date_range(self.start, periods=6, freq="4h"),
                             "open": [100.0]*6, "high": [101.0]*6,
                             "low": [99.0]*6, "close": [100.0]*6,
                             "volume": [10.0]*6})

    def test_real_fetcher_contract_and_digest(self):
        def fetch(symbol, **kwargs):
            self.assertEqual(kwargs["market_type"], "spot")
            self.assertEqual(kwargs["period"], "4hour")
            self.assertEqual(kwargs["start_ms"], int(pd.Timestamp(self.start).timestamp()*1000))
            return self.sample()
        with tempfile.TemporaryDirectory() as tmp:
            manifest = collect_one("BTC/USDT", self.start, self.cutoff, Path(tmp), fetch)
            contents = (Path(tmp) / manifest["file"]).read_bytes()
            self.assertEqual(hashlib.sha256(contents).hexdigest(), manifest["sha256"])
            self.assertEqual(manifest["rows"], 6)
            self.assertEqual(manifest["status"], "GAPLESS")

    def test_gaps_reported_without_synthetic_rows(self):
        frame = self.sample().drop(index=2)
        with tempfile.TemporaryDirectory() as tmp:
            manifest = collect_one("ETH/USDT", self.start, self.cutoff,
                                   Path(tmp), lambda *a, **k: frame)
            self.assertEqual(manifest["missing_bars"], 1)
            self.assertEqual(manifest["rows"], 5)
            self.assertEqual(manifest["status"], "GAPS_PRESENT")

    def test_unclosed_bar_is_not_saved(self):
        cutoff = pd.Timestamp("2025-01-01T20:00:00Z").to_pydatetime()
        with tempfile.TemporaryDirectory() as tmp:
            manifest = collect_one("SOL/USDT", self.start, cutoff,
                                   Path(tmp), lambda *a, **k: self.sample())
            self.assertEqual(manifest["rows"], 5)

    def test_missing_requested_history_is_not_called_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame = self.sample().iloc[2:]
            manifest = collect_one("XRP/USDT", self.start, self.cutoff,
                                   Path(tmp), lambda *a, **k: frame)
            self.assertEqual(manifest["status"], "PARTIAL_COVERAGE")
            self.assertEqual(manifest["leading_missing_bars"], 2)


if __name__ == "__main__":
    unittest.main()
