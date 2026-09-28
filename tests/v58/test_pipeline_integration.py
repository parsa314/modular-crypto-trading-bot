import json

import pytest

from research_bot.v58.contracts import StrategyArm
from research_bot.v58.generators import generate_candidates
from research_bot.v58.pipeline import (
    run_synthetic_engineering_pipeline, run_verified_development_pipeline,
    synthetic_integration_fixture, write_pipeline_result,
)


def test_connected_pipeline_emits_real_generator_events_and_valid_ledger(tmp_path):
    frame = synthetic_integration_fixture()
    result = run_synthetic_engineering_pipeline(frame)
    assert result.records
    assert {row["strategy_arm"] for row in result.records} == {arm.value for arm in StrategyArm}
    assert result.ledger.verify()
    assert len(result.ledger.entries) == 3 * len(result.records)
    assert all(row["classification"] == "SYNTHETIC_ENGINEERING_ONLY" for row in result.records)
    candidates = {e.event_id: e for e in generate_candidates(frame, venue="synthetic", symbol="BTC/USDT")}
    assert all(
        row["entry_time"] == frame.iloc[candidates[row["event_id"]].row_index + 1].timestamp.isoformat()
        for row in result.records
    )
    write_pipeline_result(result, tmp_path)
    manifest = json.loads((tmp_path / "run_manifest.json").read_text())
    assert manifest["event_count"] == len(result.records) and manifest["ledger_valid"] is True
    assert manifest["paper_execution"] is manifest["live_execution"] is False


def test_pipeline_replay_is_byte_deterministic(tmp_path):
    frame = synthetic_integration_fixture()
    first = run_synthetic_engineering_pipeline(frame)
    second = run_synthetic_engineering_pipeline(frame.copy())
    assert first.records == second.records
    assert first.replay_hash == second.replay_hash
    write_pipeline_result(first, tmp_path / "a")
    write_pipeline_result(second, tmp_path / "b")
    for name in ("synthetic_events.jsonl", "evidence_ledger.jsonl", "run_manifest.json"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


def test_real_market_outcome_path_fails_closed():
    with pytest.raises(PermissionError, match="verified development intake"):
        run_synthetic_engineering_pipeline(synthetic_integration_fixture(), venue="coinex")


def test_real_development_path_requires_verified_manifest_and_hash():
    frame = synthetic_integration_fixture()
    with pytest.raises(PermissionError):
        run_verified_development_pipeline(
            frame, venue="coinex", symbol="BTC/USDT", dataset_sha256="a" * 64,
            manifest_status="UNVERIFIED",
        )
    result = run_verified_development_pipeline(
        frame, venue="coinex", symbol="BTC/USDT", dataset_sha256="a" * 64,
        manifest_status="HISTORICAL_BYTES_VERIFIED_DEVELOPMENT_ONLY",
    )
    assert result.records and all(r["classification"] == "REAL_MARKET_DEVELOPMENT_EVIDENCE" for r in result.records)
