"""Actual file/manifest evidence-cycle tests; no API, exchange, or credentials."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from scripts.run_readonly_evidence_cycle import run_cycle


def dataset(folder: Path) -> Path:
    closes = pd.date_range("2024-01-01T01:00:00Z", periods=40, freq="h")
    csv = folder / "BTC_USDT_1h_closed.csv"
    frame = pd.DataFrame({
        "timestamp_close": closes,
        "available_at": closes + pd.Timedelta(seconds=2),
        "symbol": ["BTC_USDT"] * 40,
        "open": [100] * 40,
        "high": [101] * 40,
        "low": [99] * 40,
        "close": [100] * 40,
        "volume": [1] * 40,
    })
    frame.to_csv(csv, index=False)
    csv.with_suffix(".manifest.json").write_text(json.dumps({
        "source": "TEST_CHECKSUMMED_BACKFILL",
        "csv_sha256": sha256(csv.read_bytes()).hexdigest(),
        "availability_basis": "DECLARED_CLOSE_PLUS_2_SECONDS_NOT_OBSERVED",
    }))
    return csv


class ReadonlyEvidenceCycleTests(unittest.TestCase):
    def test_real_csv_pipeline_records_no_trade_and_actual_counts(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            csv = dataset(folder)
            output = folder / "report.json"
            result = run_cycle(csv, output, strategy="rsi_ma")
            self.assertEqual(result["data"]["rows"], 40)
            self.assertEqual(result["data"]["gaps"], 0)
            self.assertEqual(result["data"]["sha256"], sha256(csv.read_bytes()).hexdigest())
            self.assertEqual(result["signals"]["count"], 1)
            self.assertEqual(result["decisions"]["total"], 1)
            self.assertEqual(result["decisions"]["no_trade"], 1)
            self.assertEqual(result["risk"]["status"], "NOT_EVALUATED")
            self.assertIsNone(result["risk"]["cvar_95"])
            self.assertFalse(result["signals"]["full_prefix_invariance_proven_by_this_run"])
            self.assertEqual(result["execution"]["orders_sent"], 0)
            self.assertEqual(result["economic_validation"]["status"], "NOT_RUN")
            self.assertEqual(json.loads(output.read_text())["run_id"], result["run_id"])
            with self.assertRaises(FileExistsError):
                run_cycle(csv, output, strategy="rsi_ma")

    def test_missing_file_and_manifest_never_create_report(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            missing, output = folder / "absent.csv", folder / "report.json"
            with self.assertRaisesRegex(ValueError, "HISTORICAL_INPUT_NOT_FOUND"):
                run_cycle(missing, output, strategy="rsi")
            self.assertFalse(output.exists())
            csv = dataset(folder)
            csv.with_suffix(".manifest.json").unlink()
            with self.assertRaisesRegex(ValueError, "REQUIRED_DATA_MANIFEST_MISSING"):
                run_cycle(csv, output, strategy="rsi")
            self.assertFalse(output.exists())

    def test_hash_mismatch_and_empty_window_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            csv = dataset(folder)
            output = folder / "report.json"
            sidecar = csv.with_suffix(".manifest.json")
            meta = json.loads(sidecar.read_text())
            meta["csv_sha256"] = "0" * 64
            sidecar.write_text(json.dumps(meta))
            with self.assertRaisesRegex(ValueError, "DATA_MANIFEST_HASH_MISMATCH"):
                run_cycle(csv, output, strategy="ma_cross")
            meta["csv_sha256"] = sha256(csv.read_bytes()).hexdigest()
            sidecar.write_text(json.dumps(meta))
            with self.assertRaisesRegex(ValueError, "EMPTY_REQUESTED_HISTORICAL_WINDOW"):
                run_cycle(csv, output, strategy="ma_cross",
                          start="2024-02-01T00:00:00Z", end="2024-02-02T00:00:00Z")
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
