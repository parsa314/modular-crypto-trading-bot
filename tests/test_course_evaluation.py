"""Synthetic fixtures are engineering evidence, never market performance."""
import json
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from research_bot.course.evaluation import (
    EvaluationConfig,
    average_uniqueness,
    combinatorial_splits,
    economic_metrics,
    evaluate_research,
    net_returns,
    paired_comparison,
    purge_train_indices,
    validate_events,
    walk_forward_predictions,
    walk_forward_splits,
)


def fixture_events(n=160):
    t = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame({"event_id": [f"e{i}" for i in range(n)],
                         "decision_time": t - pd.Timedelta(seconds=1),
                         "available_at": t - pd.Timedelta(seconds=2),
                         "entry_time": t, "exit_time": t + pd.Timedelta(hours=4),
                         "label_available_at": t + pd.Timedelta(hours=4),
                         "gross_return": np.where(np.arange(n) % 2, .012, -.008),
                         "feature": np.arange(n, dtype=float)})


def test_rejects_future_features_same_close_and_naive_time():
    cfg = EvaluationConfig()
    for column, value in [("available_at", "2026-01-01T00:00:00Z"),
                          ("entry_time", "2024-12-31T23:59:59Z"),
                          ("label_available_at", "2024-12-31T23:59:59Z"),
                          ("decision_time", "2025-01-01")]:
        frame = fixture_events()
        frame[column] = frame[column].astype(object)
        frame.loc[0, column] = value
        with pytest.raises(ValueError):
            validate_events(frame, cfg)


def test_duplicate_event_invalid_return_and_holdout_rejected():
    frame = fixture_events()
    frame.loc[1, "event_id"] = frame.loc[0, "event_id"]
    with pytest.raises(ValueError, match="DUPLICATE"):
        validate_events(frame, EvaluationConfig())
    frame = fixture_events()
    frame.loc[0, "gross_return"] = np.inf
    with pytest.raises(ValueError, match="RETURN"):
        validate_events(frame, EvaluationConfig())
    with pytest.raises(ValueError, match="HOLDOUT"):
        validate_events(fixture_events(), EvaluationConfig(frozen_holdout_start="2025-01-10T00:00Z"))


def test_time_weighted_uniqueness_independently_computed():
    frame = fixture_events(2)
    # A=[0,4h), B=[2h,6h): each is alone 2h and shared 2h.
    frame.loc[1, "entry_time"] = frame.loc[0, "entry_time"] + pd.Timedelta(hours=2)
    frame.loc[1, "exit_time"] = frame.loc[0, "entry_time"] + pd.Timedelta(hours=6)
    result = average_uniqueness(frame)
    assert result["by_event"] == {"e0": .75, "e1": .75}
    assert result["maximum_concurrency"] == 2
    assert average_uniqueness(fixture_events())["mean"] == 1


def test_interval_purge_embargo_and_matured_train_labels():
    frame = fixture_events(8)
    test = [3]
    # Label intervals touch/overlap: indices 2,3,4 are purged conservatively.
    keep = purge_train_indices(frame, [0, 1, 2, 4, 5, 6, 7], test)
    assert keep.tolist() == [0, 1, 5, 6, 7]
    assert purge_train_indices(frame, [0, 1, 2, 4, 5, 6, 7], test, 14400).tolist() == [0, 1, 6, 7]
    cfg = EvaluationConfig(min_train=10, test_size=10)
    frame = fixture_events(80)
    for train, test in walk_forward_splits(frame, cfg):
        assert frame.iloc[train].label_available_at.max() < frame.iloc[test].decision_time.min()
        for i in train:
            for j in test:
                assert frame.iloc[i].label_available_at < frame.iloc[j].decision_time


def test_full_combinatorial_assignment_and_actual_label_purging():
    frame = fixture_events(24)
    cfg = EvaluationConfig(cpcv_groups=4, cpcv_test_groups=2)
    splits = combinatorial_splits(frame, cfg)
    assert len(splits) == 6
    for path in range(3):
        occurrences = [i for s in splits for a in s["assignments"] if a["path"] == path for i in a["indices"]]
        assert sorted(occurrences) == list(range(24))
    for split in splits:
        for i in split["train"]:
            for j in split["test"]:
                assert (frame.iloc[i].label_available_at < frame.iloc[j].decision_time
                        or frame.iloc[i].decision_time > frame.iloc[j].label_available_at)


def test_costs_roundtrip_once_direction_correct_and_flat_free():
    assert np.allclose(net_returns([.01, .01, .01], [1, -1, 0], 24), [.0076, -.0124, 0])
    with pytest.raises(ValueError):
        net_returns([.01], [np.nan], 24)
    with pytest.raises(ValueError):
        net_returns([.01], [2], 24)


def test_drawdown_includes_initial_capital_and_overlap_not_compounded():
    result = economic_metrics([-.1, .05], [1, 1], portfolio_path_valid=True)
    assert result["max_drawdown"] == pytest.approx(-.1)
    assert result["total_return"] == pytest.approx(-.055)
    assert result["cagr"] is None
    result = economic_metrics([-.1, .05], [1, 1])
    assert result["total_return"] is None and result["max_drawdown"] is None


def test_train_only_preprocessing_and_no_holdout_tuning():
    cfg = EvaluationConfig(min_train=12, test_size=12, baseline_admissible=True,
                           model_names=("logistic",))
    frame = fixture_events(60)
    frame.loc[24:, "feature"] = 1_000_000
    result = walk_forward_predictions(frame, ["feature"], cfg)
    first = result["folds"][0]
    train = first["train_indices"]
    assert first["train_imputer_medians"] == [float(frame.iloc[train].feature.median())]
    assert first["train_scaler_means"] == [float(frame.iloc[train].feature.mean())]
    assert first["train_scaler_means"][0] < 24
    assert min(first["test_indices"]) == 24
    with pytest.raises(ValueError, match="FEATURE_FORBIDDEN"):
        walk_forward_predictions(frame, ["gross_return"], cfg)


def test_ml_stays_blocked_without_baseline_admission_and_evidence():
    result = walk_forward_predictions(fixture_events(), ["feature"], EvaluationConfig())
    assert result["status"] == "BLOCKED"
    with pytest.raises(ValueError, match="PREREGISTRATION"):
        evaluate_research(fixture_events(), feature_columns=["feature"],
                          strategy_positions={"baseline": np.ones(160)}, git_commit="fixture",
                          config=EvaluationConfig(baseline_admissible=True))


def test_deterministic_null_centered_bootstrap_and_insufficient_support():
    cfg = EvaluationConfig(bootstrap_runs=50, bootstrap_block=4)
    r = np.tile([.003, -.001], 40)
    result = paired_comparison(r, np.zeros(80), cfg)
    assert result == paired_comparison(r, np.zeros(80), cfg)
    assert result["ci"][0] > 0
    assert 0 <= result["p_one_sided"] <= 1
    assert paired_comparison(r[:5], np.zeros(5), cfg)["status"] == "NOT_APPLICABLE"


def test_report_strict_json_reproducible_synthetic_not_promoted():
    frame = fixture_events(160)
    cfg = EvaluationConfig(regular_period_seconds=14400, periods_per_year=2190,
                           bootstrap_runs=40)
    arguments = {"feature_columns": ["feature"], "strategy_positions": {"long": np.ones(160), "flat": np.zeros(160)},
                     "git_commit": "synthetic-engineering-fixture", "config": cfg,
                     "provenance": {"evidence_class": "SYNTHETIC_ENGINEERING_FIXTURE", "complete_trial_universe": True}}
    report = evaluate_research(frame, **arguments)
    assert report == evaluate_research(frame, **arguments)
    json.dumps(report, allow_nan=False)
    assert report["status"] == "SCIENTIFIC_DECISION_DEFERRED"
    assert report["execution_authorization"] == "DENIED_RESEARCH_ONLY"
    assert report["walk_forward"]["status"] == "BLOCKED"
    assert report["cpcv"]["learned_paths_status"].startswith("BLOCKED")
    assert report["pbo"]["status"] == "NOT_APPLICABLE"  # flat trial has undefined Sharpe
    assert report["sharpe_probabilities"]["long"]["dsr"] is None
    assert report["cost_stress"]["24.0"]["long"]["mean_net_event_return"] == pytest.approx(-.0004)
    assert report["trial_records"][0]["status"] == "HYPOTHESIS"


def test_irregular_or_overlapping_events_block_annualization():
    frame = fixture_events()
    frame["exit_time"] += pd.Timedelta(hours=4)
    frame["label_available_at"] += pd.Timedelta(hours=4)
    report = evaluate_research(frame, feature_columns=[], strategy_positions={"long": np.ones(160)},
                              git_commit="fixture", config=EvaluationConfig(regular_period_seconds=14400,
                                                                          periods_per_year=2190))
    assert report["average_uniqueness"]["mean"] < 1
    assert report["cost_stress"]["0.0"]["long"]["cagr"] is None
    assert report["sharpe_probabilities"]["long"]["psr"] is None


def test_evaluation_config_rejects_invalid_parameters():
    for kwargs in ({"cost_bps": (np.nan,)}, {"cpcv_test_groups": 4},
                   {"bootstrap_runs": 1}, {"model_names": ("rl",)}):
        with pytest.raises(ValueError):
            replace(EvaluationConfig(), **kwargs)


def test_statistics_applicability_and_deflation_separate_from_psr():
    frame = fixture_events(160)
    cfg = EvaluationConfig(regular_period_seconds=14400, bootstrap_runs=40,
                           independent_trials=3, trial_sharpe_std=.05)
    report = evaluate_research(frame, feature_columns=[],
                              strategy_positions={"long": np.ones(160), "short": -np.ones(160)},
                              git_commit="synthetic-fixture", config=cfg,
                              provenance={"complete_trial_universe": True,
                                          "evidence_class": "SYNTHETIC_ENGINEERING_FIXTURE"})
    assert report["pbo"]["method"] == "CSCV_PBO_NOT_CPCV"
    assert report["pbo"]["splits"] == 20
    assert 0 <= report["pbo"]["pbo"] <= 1
    item = report["sharpe_probabilities"]["long"]
    assert 0 <= item["dsr"] <= item["psr"] <= 1
    assert report["status"] == "SCIENTIFIC_DECISION_DEFERRED"


def test_multidimensional_or_nonfinite_pnl_panel_rejected():
    with pytest.raises(ValueError):
        net_returns([[.1], [.2]], [[1], [1]], 24)
    with pytest.raises(ValueError):
        economic_metrics([.1, .2], [np.nan, 1])
    with pytest.raises(ValueError):
        paired_comparison([.1, np.inf], [.1, .2], EvaluationConfig())
