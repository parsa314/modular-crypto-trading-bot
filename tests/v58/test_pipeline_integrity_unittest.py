"""Offline regression checks; runnable with Python's standard unittest runner."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from research_bot.v58.pipeline import (
    run_synthetic_engineering_pipeline, run_verified_development_pipeline,
    synthetic_integration_fixture, write_pipeline_result,
)


class PipelineIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = synthetic_integration_fixture()

    def test_dataframe_and_status_cannot_assert_real_market_provenance(self):
        with self.assertRaises(PermissionError):
            run_verified_development_pipeline(
                self.frame, venue="coinex", symbol="BTC/USDT",
                dataset_sha256="a" * 64,
                manifest_status="HISTORICAL_BYTES_VERIFIED_DEVELOPMENT_ONLY",
            )

    def test_hourly_path_uses_hourly_bar_duration(self):
        frame = self.frame.copy()
        frame["timestamp"] = pd.date_range("2025-01-01", periods=len(frame), freq="1h", tz="UTC")
        result = run_synthetic_engineering_pipeline(frame, timeframe="1h")
        self.assertGreater(len(result.records), 0)
        for row in result.records:
            if row["resolved_at"]:
                elapsed = pd.Timestamp(row["resolved_at"]) - pd.Timestamp(row["entry_time"])
                self.assertLessEqual(elapsed, pd.Timedelta(hours=row["horizon_bars"]))

    def test_invalid_cost_is_rejected_even_when_no_events(self):
        for cost in (-1, float("nan"), float("inf")):
            with self.subTest(cost=cost), self.assertRaises(ValueError):
                run_synthetic_engineering_pipeline(self.frame.iloc[:20], round_trip_cost_bps=cost)

    def test_changed_run_cannot_overwrite_existing_evidence(self):
        first = run_synthetic_engineering_pipeline(self.frame)
        second = run_synthetic_engineering_pipeline(self.frame, round_trip_cost_bps=36)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            write_pipeline_result(first, output)
            before = {p.name: p.read_bytes() for p in output.iterdir()}
            with self.assertRaises(ValueError):
                write_pipeline_result(second, output)
            self.assertEqual(before, {p.name: p.read_bytes() for p in output.iterdir()})
            write_pipeline_result(first, output)

    def test_mixed_classification_rejected_before_creating_output(self):
        result = run_synthetic_engineering_pipeline(self.frame)
        records = [dict(row) for row in result.records]
        records[0]["classification"] = "REAL_MARKET_DEVELOPMENT_EVIDENCE"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "invalid"
            with self.assertRaises(ValueError):
                write_pipeline_result(replace(result, records=tuple(records)), output)
            self.assertFalse(output.exists())

    def test_mutated_record_rejected_before_export(self):
        result = run_synthetic_engineering_pipeline(self.frame)
        result.records[0]["entry_price"] *= 2
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "invalid"
            with self.assertRaises(ValueError):
                write_pipeline_result(result, output)
            self.assertFalse(output.exists())

    def test_zero_event_run_keeps_input_and_policy_identity(self):
        frame = self.frame.iloc[:20].copy()
        first = run_synthetic_engineering_pipeline(frame)
        changed = frame.copy()
        changed["volume"] *= 2
        second = run_synthetic_engineering_pipeline(changed)
        third = run_synthetic_engineering_pipeline(frame, round_trip_cost_bps=36)
        self.assertEqual(len(first.records), 0)
        self.assertEqual(len(second.records), 0)
        self.assertNotEqual(first.replay_hash, second.replay_hash)
        self.assertNotEqual(first.replay_hash, third.replay_hash)
        with tempfile.TemporaryDirectory() as directory:
            write_pipeline_result(first, Path(directory))
            manifest = json.loads((Path(directory) / "run_manifest.json").read_text())
            self.assertEqual(manifest["classification"], "SYNTHETIC_ENGINEERING_ONLY")
            self.assertEqual(len(manifest["metadata"]["source_code_sha256"]), 64)
            self.assertEqual(len(manifest["metadata"]["data_version"]), 64)


if __name__ == "__main__":
    unittest.main()
