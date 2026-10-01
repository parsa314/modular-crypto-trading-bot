import gzip
import os
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys
from unittest.mock import patch

from research_bot.v58.data_audit import digest, inspect_csv
from research_bot.v58.development_sources import SOURCES
from research_bot.v58.immutable_io import write_immutable_bundle
from research_bot.v58.pipeline import (
    run_synthetic_engineering_pipeline, run_verified_development_pipeline,
    synthetic_integration_fixture,
)
from research_bot.v58.real_replay import replay_archive


class EvidencePublicationTests(unittest.TestCase):
    def test_current_directory_rejected_without_detaching_caller(self):
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                with self.assertRaisesRegex(ValueError, "named output directory"):
                    write_immutable_bundle(Path("."), {"manifest.json": b"complete"})
                self.assertTrue(os.path.samefile(Path.cwd(), directory))
                self.assertEqual(list(Path(directory).iterdir()), [])
            finally:
                os.chdir(previous)

    def test_cli_rejects_wrong_archive_without_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "fake.zip"
            archive.write_bytes(b"not the registered archive")
            output = Path(directory) / "run"
            completed = subprocess.run(
                [sys.executable, "-m", "research_bot.v58", "verified-replay", "--archive", str(archive),
                 "--venue", "coinex", "--output", str(output)], capture_output=True, text=True,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("archive SHA-256 mismatch", completed.stderr)
            self.assertNotIn("Traceback", completed.stderr)
            self.assertFalse(output.exists())

    def test_frozen_registry_matches_recorded_reviewed_manifest(self):
        root = Path(__file__).resolve().parents[2]
        manifest = json.loads((root / "evidence/v58_core_repair/V58_RECOVERED_DATA_MANIFEST.json").read_text())
        self.assertEqual(len(SOURCES), 10)
        for row in SOURCES:
            original = next(r for r in manifest["datasets"] if r["csv_sha256"] == row["csv_sha256"])
            self.assertEqual(row, {key: original[key] for key in row})

    def test_matching_caller_digest_cannot_promote_unknown_csv(self):
        raw = synthetic_integration_fixture().to_csv(index=False).encode()
        with self.assertRaisesRegex(PermissionError, "frozen development source"):
            run_verified_development_pipeline(
                raw, venue="coinex", symbol="BTC/USDT", dataset_sha256=digest(raw),
                manifest_status="HISTORICAL_BYTES_VERIFIED_DEVELOPMENT_ONLY",
            )

    def test_trusted_intake_parses_exact_bytes_and_binds_metadata(self):
        # Test-only trust store: this fixture is never an empirical data artifact.
        raw = synthetic_integration_fixture().to_csv(index=False).encode()
        _, quality = inspect_csv(raw)
        source = {**quality, "venue": "coinex", "symbol": "BTC/USDT", "source_run": 1}
        with patch("research_bot.v58.pipeline.SOURCES", [source]):
            result = run_verified_development_pipeline(
                raw, venue="coinex", symbol="BTC/USDT", dataset_sha256=digest(raw),
                manifest_status="HISTORICAL_BYTES_VERIFIED_DEVELOPMENT_ONLY",
            )
            self.assertGreater(len(result.records), 0)
            self.assertEqual(result.metadata["data_version"], digest(raw))
            self.assertEqual(result.metadata["provenance"], source)
            self.assertTrue(all(row["data_version"] == digest(raw) for row in result.records))
            with self.assertRaises(PermissionError):
                run_verified_development_pipeline(
                    raw + b"\n", venue="coinex", symbol="BTC/USDT", dataset_sha256=digest(raw),
                    manifest_status="HISTORICAL_BYTES_VERIFIED_DEVELOPMENT_ONLY",
                )
            with self.assertRaisesRegex(ValueError, "4h timeframe"):
                run_verified_development_pipeline(
                    raw, venue="coinex", symbol="BTC/USDT", dataset_sha256=digest(raw),
                    manifest_status="HISTORICAL_BYTES_VERIFIED_DEVELOPMENT_ONLY", timeframe="1h",
                )

    def test_bundle_is_idempotent_and_refuses_partial_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bundle"
            files = {"a/data.json": b"data", "manifest.json": b"complete"}
            write_immutable_bundle(output, files)
            write_immutable_bundle(output, files)
            (output / "manifest.json").unlink()
            with self.assertRaises(ValueError):
                write_immutable_bundle(output, files)
            self.assertFalse((output / "manifest.json").exists())
            self.assertEqual((output / "a/data.json").read_bytes(), b"data")

    def test_failed_staging_does_not_publish_partial_result(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bundle"
            with patch("research_bot.v58.immutable_io.os.fsync", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    write_immutable_bundle(output, {"data": b"data", "manifest": b"complete"})
            self.assertFalse(output.exists())
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_unsafe_output_member_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ("../escape", "/absolute", "a/../escape", "a\\escape"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    write_immutable_bundle(Path(directory) / "bundle", {name: b"bad"})
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_replay_rejects_unregistered_archive_provenance_before_read(self):
        with self.assertRaises(PermissionError):
            replay_archive(path=Path("absent.zip"), venue="coinex", expected_sha256="a" * 64,
                           source_run=1, source_artifact=1, output=Path("unused"))

    def test_later_dataset_failure_does_not_publish_earlier_dataset(self):
        source = SOURCES[0]
        dataset = {"file": "x.gz", "symbol": source["symbol"], "csv_sha256": source["csv_sha256"],
                   "dataset_id": "first", "row_count": 20, "start_timestamp": "start", "end_timestamp": "end"}
        report = {"archive_sha256": source["archive_sha256"],
                  "datasets": [dataset, {**dataset, "dataset_id": "second"}]}
        result = run_synthetic_engineering_pipeline(synthetic_integration_fixture().iloc[:20])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "replay"
            with patch("research_bot.v58.real_replay.audit_archive", return_value=(report, {"x.gz": gzip.compress(b"fixture")})), \
                 patch("research_bot.v58.real_replay.run_verified_development_pipeline", side_effect=[result, ValueError("bad second dataset")]):
                with self.assertRaisesRegex(ValueError, "bad second dataset"):
                    replay_archive(path=Path("unused.zip"), venue=source["venue"], expected_sha256=source["archive_sha256"],
                                   source_run=source["source_run"], source_artifact=source["source_artifact"], output=output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
