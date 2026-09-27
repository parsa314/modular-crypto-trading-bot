from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research_bot.feature_audit_v54 import _backtest_predictions
from research_bot.v54_completion import assess_v54_completion
from research_bot.v54_integrity import (
    V54FeatureHealthConfig,
    dataset_manifest_v54,
    feature_health_v54,
    moving_block_mean_ci_v54,
)
from research_bot.v54_promotion import V54PromotionPolicy, summarize_family_evidence_v54
from scripts.run_v54_feature_audit import read_frozen_input, write_frozen_input


def _frame(n: int = 160) -> pd.DataFrame:
    rng = np.random.default_rng(54)
    ts = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({
        "timestamp": ts,
        "decision_at": ts + pd.Timedelta(hours=1),
        "bar_open_at": ts,
        "bar_close_at": ts + pd.Timedelta(hours=1),
        "1h_available_at": ts + pd.Timedelta(hours=1),
        "4h_available_at": ts.floor("4h"),
        "open": open_,
        "high": np.maximum(open_, close) * 1.002,
        "low": np.minimum(open_, close) * 0.998,
        "close": close,
        "volume": 1000 + rng.normal(0, 5, n),
        "smc_feature": rng.normal(size=n),
        "constant_feature": 1.0,
        "mostly_missing": np.r_[rng.normal(size=5), np.full(n - 5, np.nan)],
    })


def test_dataset_manifest_is_deterministic_and_schema_bound():
    frame = _frame()
    a = dataset_manifest_v54(frame, symbol="BTC/USDT")
    b = dataset_manifest_v54(frame.copy(), symbol="BTC/USDT")
    assert a["frame_sha256"] == b["frame_sha256"]
    assert a["schema_sha256"] == b["schema_sha256"]
    changed = frame.copy()
    changed.loc[10, "close"] *= 1.01
    c = dataset_manifest_v54(changed, symbol="BTC/USDT")
    assert c["frame_sha256"] != a["frame_sha256"]
    changed_feature = frame.copy()
    changed_feature.loc[10, "smc_feature"] += 9.0
    d = dataset_manifest_v54(changed_feature, symbol="BTC/USDT")
    assert d["frame_sha256"] != a["frame_sha256"]


def test_feature_health_rejects_constant_and_mostly_missing_without_using_target():
    frame = _frame()
    health = feature_health_v54(
        frame,
        ["smc_feature", "constant_feature", "mostly_missing"],
        V54FeatureHealthConfig(max_missing_fraction=0.9, min_non_null=20),
    )
    assert "smc_feature" in health["healthy_features"]
    assert "constant_feature" in health["rejected_features"]
    assert "mostly_missing" in health["rejected_features"]


def test_terminal_liquidation_cost_is_charged():
    test = pd.DataFrame({
        "decision_at": pd.date_range("2026-01-01", periods=3, freq="1h", tz="UTC"),
        "future_return": [0.01, 0.01, 0.01],
    })
    pred = np.array([1.0, 1.0, 1.0])
    zero = _backtest_predictions(test, pred, 0.0, decision_time_col="decision_at")
    cost = _backtest_predictions(test, pred, 24.0, decision_time_col="decision_at")
    assert zero["turnover"].sum() == pytest.approx(2.0)  # entry + forced exit
    assert cost["net_return"].sum() < zero["net_return"].sum()


def test_block_bootstrap_returns_finite_inference_for_supported_length():
    rng = np.random.default_rng(1)
    diff = pd.Series(rng.normal(0.0002, 0.001, 300))
    out = moving_block_mean_ci_v54(diff, resamples=200, block=12, seed=7)
    assert out["n"] == 300
    assert np.isfinite(out["mean"])
    assert np.isfinite(out["ci_low"])
    assert np.isfinite(out["ci_high"])
    assert 0 <= out["p_nonpositive"] <= 1


def test_frozen_snapshot_replay_detects_tamper(tmp_path: Path):
    frame = _frame()
    manifest = write_frozen_input(frame, "BTC/USDT", tmp_path)
    replay, expected = read_frozen_input("BTC/USDT", tmp_path)
    assert len(replay) == len(frame)
    assert expected["frame_sha256"] == manifest["frame_sha256"]

    path = tmp_path / "BTC_USDT.csv.gz"
    tampered = pd.read_csv(path)
    tampered.loc[5, "close"] *= 1.02
    tampered.to_csv(path, index=False, compression="gzip", float_format="%.12g")
    with pytest.raises(ValueError, match="hash mismatch"):
        read_frozen_input("BTC/USDT", tmp_path)


def test_cross_symbol_promotion_is_research_only():
    per_symbol = {}
    for symbol in ("BTC/USDT", "ETH/USDT", "SOL/USDT"):
        per_symbol[symbol] = {
            "family_value": {
                "ICHIMOKU": {
                    "all_minus_drop_sharpe": 0.2,
                    "positive_increment": True,
                    "paired_net_return_inference": {"mean": 0.0001, "ci_low": -0.0001, "ci_high": 0.0003, "p_nonpositive": 0.2},
                }
            }
        }
    out = summarize_family_evidence_v54(per_symbol, V54PromotionPolicy())
    fam = out["families"]["ICHIMOKU"]
    assert fam["promotion_candidate"] is True
    assert fam["paired_return_support"] is True
    assert fam["execution_authorized"] is False
    assert out["paper_execution"] is False
    assert out["live_execution"] is False


def test_completion_gate_requires_provenance_and_complete_artifacts():
    symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    manifests = {
        symbol: {
            "frame_sha256": "a" * 64,
            "schema_sha256": "b" * 64,
            "rows": 1000,
            "decision_start": "2026-01-01T00:00:00+00:00",
            "decision_end": "2026-02-01T00:00:00+00:00",
        }
        for symbol in symbols
    }
    audit = {
        "paper_execution": False,
        "live_execution": False,
        "variants": {"ALL": {"aggregate": {}}},
        "family_value": {"ICHIMOKU": {"all_minus_drop_sharpe": 0.1}},
    }
    report = {
        "source_commit": "1" * 40,
        "symbols_completed": symbols,
        "dataset_manifests": manifests,
        "per_symbol": {symbol: audit for symbol in symbols},
        "cross_symbol_evidence": {
            "paper_execution": False,
            "live_execution": False,
            "families": {"ICHIMOKU": {"promotion_candidate": True, "paired_return_support": False}},
        },
        "paper_execution": False,
        "live_execution": False,
    }
    out = assess_v54_completion(report)
    assert out["complete"] is True
    assert out["empirical_status"] == "V54_EMPIRICAL_COMPLETE"
    assert out["execution_authorized"] is False

    broken = dict(report)
    broken["source_commit"] = None
    out2 = assess_v54_completion(broken)
    assert out2["complete"] is False
    assert "SOURCE_COMMIT_MISSING" in out2["reasons"]
