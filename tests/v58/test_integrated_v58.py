"""Public-seam checks for the connected synthetic learning/portfolio path."""
from dataclasses import replace
from hashlib import sha256
import json

import numpy as np
import pandas as pd
import pytest

from research_bot.v58.events import stable_hash
from research_bot.v58.integrated import (
    FEATURE_COLUMNS, UtilityConfig, build_synthetic_event_dataset,
    run_synthetic_ai_demo, synthetic_market_fixture, utility_decision,
)
from research_bot.v58.learning import score_frozen_synthetic_baseline


def intent_inputs():
    clock = "2025-06-01T00:00:00+00:00"
    record = {
        "classification": "SYNTHETIC_ENGINEERING_ONLY", "venue": "synthetic",
        "event_id": "e1", "symbol": "BTC/USDT", "event_timestamp": clock,
        "entry_time": clock, "entry_price": 100, "stop_price": 99,
        "target_price": 101.5, "horizon_bars": 12, "regime_confidence": 0.8,
    }
    prediction = {"event_id": "e1", "decision_at": clock,
                  "probabilities": {"TP": 0.6, "SL": 0.2, "TIMEOUT": 0.2},
                  "entropy": 0.2, "shift_score": 1.0}
    return record, prediction


def test_cost_adjusted_utility_and_outcome_independence():
    record, prediction = intent_inputs()
    actual = utility_decision(record, prediction)
    # 0.6*.015 - .2*.01 - .2*.25*.01 - .0024 - .05*.01*.2
    assert actual["expected_utility"] == pytest.approx(0.004)
    assert actual["admission_reason"] == "ADMITTED"
    assert utility_decision({**record, "outcome": "SL", "net_return": -999,
                             "exit_price": 1, "resolved_at": "2099-01-01"}, prediction) == actual


@pytest.mark.parametrize("field,value", [
    ("event_id", "other-event"), ("decision_at", "2030-01-01T00:00:00+00:00"),
    ("decision_at", "2025-06-01T00:00:00"), ("decision_at", "NaT"),
])
def test_wrong_prediction_join_rejected(field, value):
    record, prediction = intent_inputs()
    with pytest.raises(ValueError):
        utility_decision(record, {**prediction, field: value})


@pytest.mark.parametrize("config", [False, {}, 0, ""])
def test_falsey_malformed_utility_config_rejected(config):
    with pytest.raises(ValueError):
        utility_decision(*intent_inputs(), config)


def test_utility_gates_are_independent():
    record, prediction = intent_inputs()
    assert utility_decision({**record, "regime_confidence": 0.1}, prediction)["admission_reason"] == "LOW_REGIME_CONFIDENCE"
    assert utility_decision(record, {**prediction, "entropy": 1})["admission_reason"] == "HIGH_ENTROPY"
    assert utility_decision(record, {**prediction, "shift_score": 9})["admission_reason"] == "DISTRIBUTION_SHIFT"
    assert utility_decision(record, prediction, replace(UtilityConfig(), round_trip_cost_bps=100))["admission_reason"] == "COST_UTILITY_GATE"
    with pytest.raises(ValueError, match="synthetic"):
        utility_decision({**record, "venue": "binance"}, prediction)


@pytest.mark.parametrize("symbols", [("BTC/USDT", "BTC_USDT"), ("../BTC",), ("BTC/USDT", "BTC/USDT"), (False,)])
def test_asset_evidence_path_collisions_rejected(tmp_path, symbols):
    with pytest.raises(ValueError, match="canonical BASE/QUOTE"):
        run_synthetic_ai_demo(output=tmp_path / "rejected", symbols=symbols)
    assert not (tmp_path / "rejected").exists()


def test_dataset_uses_full_horizon_and_causal_snapshots():
    frame = synthetic_market_fixture(bars=1000)
    data, records, pipelines = build_synthetic_event_dataset({"BTC/USDT": frame})
    last_open = frame.timestamp.iloc[-1]
    for row in data.itertuples():
        assert pd.Timestamp(row.decision_at) + pd.Timedelta(hours=4 * 11) <= last_open
        assert pd.Timestamp(row.label_available_at) > pd.Timestamp(row.decision_at)
        assert row.label == records[row.event_id]["outcome"]
    assert set(data.label) == {"TP", "SL", "TIMEOUT"}
    assert data.attrs["excluded_terminal_events"] == len(pipelines["BTC/USDT"].records) - len(data)
    # Cut after a known candidate; even an early resolved label cannot make
    # an event without its entire scheduled horizon eligible for training.
    final_candidate = max(pd.Timestamp(row["event_timestamp"]) for row in records.values())
    truncated = frame.loc[frame.timestamp <= final_candidate].reset_index(drop=True)
    cut_data, _, cut_pipelines = build_synthetic_event_dataset({"BTC/USDT": truncated})
    assert cut_data.attrs["excluded_terminal_events"] > 0
    assert len(cut_data) < len(cut_pipelines["BTC/USDT"].records)
    # Mutating future candles must not affect already available features.
    cutoff = frame.timestamp.iloc[700]
    changed = frame.copy()
    changed.loc[701:, ["open", "high", "low", "close"]] *= 1.05
    other, _, _ = build_synthetic_event_dataset({"BTC/USDT": changed})
    old = data.loc[pd.to_datetime(data.decision_at) <= cutoff].set_index("event_id")
    new = other.loc[pd.to_datetime(other.decision_at) <= cutoff].set_index("event_id")
    pd.testing.assert_frame_equal(old.loc[:, list(FEATURE_COLUMNS)], new.loc[:, list(FEATURE_COLUMNS)])


def test_connected_cli_bundle_is_reproducible_and_frozen_scoreable(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    summary = run_synthetic_ai_demo(output=first, bars_per_asset=1000, symbols=("BTC/USDT",))
    repeated = run_synthetic_ai_demo(output=second, bars_per_asset=1000, symbols=("BTC/USDT",))
    assert summary == repeated
    assert summary["fold_count"] == 3 and summary["test_event_count"] > 0
    assert summary["classification"] == "SYNTHETIC_ENGINEERING_ONLY"
    assert not any(summary[name] for name in ("empirical_training", "paper_execution", "live_execution", "promotion_authorized"))
    assert [row["round_trip_cost_bps"] for row in summary["cost_stress"]] == [0, 24, 36, 50]
    assert all(row["no_trade"]["net_return"] == 0 for row in summary["cost_stress"])
    manifest = json.loads((first / "artifact_manifest.json").read_text())
    assert stable_hash(manifest["files"]) == summary["artifact_map_sha256"]
    for name, digest in manifest["files"].items():
        assert sha256((first / name).read_bytes()).hexdigest() == digest
        assert (first / name).read_bytes() == (second / name).read_bytes()
    learning = json.loads((first / "learning.json").read_text())
    dataset = pd.read_csv(first / "event_dataset.csv").set_index("event_id", drop=False)
    for fold in learning["folds"]:
        rows = dataset.loc[fold["split"]["test"]["row_ids"]].drop(columns=["label", "label_available_at"])
        fresh = score_frozen_synthetic_baseline(fold, rows)
        assert [r["event_id"] for r in fresh] == [r["event_id"] for r in fold["predictions"]]
        for scored, original in zip(fresh, fold["predictions"]):
            np.testing.assert_allclose([scored["probabilities"][name] for name in ("TP", "SL", "TIMEOUT")],
                                       [original["probabilities"][name] for name in ("TP", "SL", "TIMEOUT")], rtol=1e-12)
