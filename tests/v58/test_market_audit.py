"""Regression checks for the V58 no-retuning development market audit."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research_bot.v58.market_audit import FEATURE_COLUMNS, _run_market_audit


def development_events(n: int = 300) -> pd.DataFrame:
    clocks = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    labels = np.asarray(["TP", "SL", "TIMEOUT"] * (n // 3))
    signal = np.asarray([2.0, -2.0, 0.0] * (n // 3))
    gross = np.asarray([0.015, -0.010, 0.001] * (n // 3))
    data = {
        "event_id": [f"dev-{i:04d}" for i in range(n)],
        "venue": "coinex",
        "symbol": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT"] * (n // 5),
        "decision_at": clocks,
        "label_available_at": clocks + pd.Timedelta(hours=4),
        "label": labels,
        "regime_confidence": 0.9,
        "entry_price": 100.0,
        "stop_price": 99.0,
        "target_price": 101.5,
        "gross_return": gross,
        "data_version": "a" * 64,
        "policy_hash": "b" * 64,
    }
    for i, name in enumerate(FEATURE_COLUMNS):
        data[name] = signal * (1.0 + i * 0.05) + np.arange(n, dtype=float) * 1e-5
    return pd.DataFrame(data)


def run(events: pd.DataFrame):
    return _run_market_audit(events, source_manifest={"scope": "TEST_FIXTURE_ONLY"})


def test_walk_forward_is_disjoint_and_prevalence_is_train_only():
    result = run(development_events())
    assert result["report"]["fold_count"] == 3
    ids = [row["event_id"] for row in result["predictions"]]
    assert len(ids) == len(set(ids))
    assert result["report"]["paper_execution"] is False
    assert result["report"]["live_execution"] is False
    assert result["report"]["promotion_authorized"] is False
    for model in result["models"]:
        prevalence = model["training_class_prevalence"]
        assert list(prevalence) == ["TP", "SL", "TIMEOUT"]
        assert sum(prevalence.values()) == pytest.approx(1.0)
        assert model["preprocessing"]["fit_scope"] == "TRAIN_ONLY"
        assert model["calibration"]["fit_scope"] == "VALIDATION_ONLY"


def test_future_test_mutation_cannot_change_any_frozen_model_state():
    events = development_events()
    original = run(events)
    last_test_ids = {row["event_id"] for row in original["predictions"] if row["fold_index"] == 2}
    changed = events.copy()
    mask = changed["event_id"].isin(last_test_ids)
    changed.loc[mask, "label"] = "TP"
    changed.loc[mask, FEATURE_COLUMNS[0]] = 1e6
    rerun = run(changed)
    assert rerun["models"] == original["models"]
    assert [fold["split"] for fold in rerun["report"]["folds"]] == [fold["split"] for fold in original["report"]["folds"]]


def test_fixed_abstention_can_preserve_zero_trade_result_without_threshold_rescue():
    events = development_events()
    events["regime_confidence"] = 0.0
    result = run(events)
    assert result["report"]["aggregate"]["admitted_event_count"] == 0
    assert result["report"]["aggregate"]["coverage"] == 0.0
    assert result["report"]["aggregate"]["mean_admitted_net_event_return_24bps"] is None
    assert {row["admission_reason"] for row in result["predictions"]} == {"LOW_REGIME_CONFIDENCE"}


@pytest.mark.parametrize("mutation", ["wrong_venue", "duplicate_id", "bad_clock", "future_label", "infinite_feature", "bad_geometry", "bad_hash"])
def test_invalid_or_untrusted_inputs_fail_closed(mutation):
    events = development_events()
    if mutation == "wrong_venue":
        events["venue"] = "nobitex"
    elif mutation == "duplicate_id":
        events.loc[1, "event_id"] = events.loc[0, "event_id"]
    elif mutation == "bad_clock":
        events["decision_at"] = events["decision_at"].astype(object)
        events.loc[0, "decision_at"] = pd.Timestamp("2025-01-01")
    elif mutation == "future_label":
        events.loc[0, "label_available_at"] = events.loc[0, "decision_at"]
    elif mutation == "infinite_feature":
        events.loc[0, FEATURE_COLUMNS[0]] = float("inf")
    elif mutation == "bad_geometry":
        events.loc[0, "stop_price"] = 101.0
    elif mutation == "bad_hash":
        events.loc[0, "data_version"] = "claimed"
    with pytest.raises((ValueError, PermissionError)):
        run(events)
