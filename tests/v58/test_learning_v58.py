"""Synthetic learning checks; these fixtures are not market evidence."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from research_bot.v58.learning import (
    LearningConfig, run_synthetic_baseline, run_synthetic_walk_forward,
    score_frozen_synthetic_baseline,
)


def synthetic_events() -> pd.DataFrame:
    n = 150
    clock = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame({
        "event_id": [f"synthetic-{i:03}" for i in range(n)],
        "venue": "synthetic",
        "decision_at": clock,
        "label_available_at": clock + pd.Timedelta(hours=4),
        "label": np.asarray(["TP", "SL", "TIMEOUT"] * (n // 3)),
        "feature_signal": np.asarray([2.0, -2.0, 0.0] * (n // 3)),
        "feature_context": np.arange(n, dtype=float),
    })


FEATURES = ("feature_signal", "feature_context")


def test_train_only_imputation_scaling_and_probability_axis():
    events = synthetic_events()
    events.loc[2, "feature_signal"] = np.nan
    result = run_synthetic_baseline(events, FEATURES)
    train_ids = result["split"]["train"]["row_ids"]
    train = events.set_index("event_id").loc[train_ids, list(FEATURES)]
    medians = train.median()
    filled = train.fillna(medians)
    assert result["preprocessing"]["medians"] == pytest.approx(medians.to_list())
    assert result["preprocessing"]["means"] == pytest.approx(filled.mean().to_list())
    assert result["preprocessing"]["scales"] == pytest.approx(filled.std(ddof=0).to_list())
    assert result["preprocessing"]["fit_row_ids"] == train_ids
    for prediction in result["predictions"]:
        assert list(prediction["probabilities"]) == ["TP", "SL", "TIMEOUT"]
        assert sum(prediction["probabilities"].values()) == pytest.approx(1.0)
        assert 0 <= prediction["entropy"] <= 1
        assert prediction["shift_score"] >= 0
    json.dumps(result, allow_nan=False)
    assert result["empirical_training"] is False
    assert result["paper_execution"] is result["live_execution"] is False


def test_information_time_purge_and_embargo():
    events = synthetic_events()
    events.loc[10, "label_available_at"] = events.loc[95, "decision_at"]
    events.loc[100, "label_available_at"] = events.loc[125, "decision_at"]
    result = run_synthetic_baseline(events, FEATURES)
    split = result["split"]
    assert "synthetic-010" in split["train"]["purged_row_ids"]
    assert "synthetic-100" in split["calibration"]["purged_row_ids"]
    for role, next_role in (("train", "calibration"), ("calibration", "test")):
        rows = events.set_index("event_id").loc[split[role]["row_ids"]]
        limit = pd.Timestamp(split[next_role]["start"]) - pd.Timedelta(seconds=split["embargo_seconds"])
        assert (rows["label_available_at"] < limit).all()


def test_equal_decision_clocks_never_cross_roles_and_input_order_is_irrelevant():
    events = synthetic_events()
    extra = events.iloc[::3].copy()
    extra["event_id"] = extra["event_id"] + "-second-arm"
    events = pd.concat([events, extra], ignore_index=True)
    result = run_synthetic_baseline(events, FEATURES)
    membership = {}
    for role in ("train", "calibration", "test"):
        for event_id in result["split"][role]["row_ids"] + result["split"][role]["purged_row_ids"]:
            decision_at = events.set_index("event_id").loc[event_id, "decision_at"]
            membership.setdefault(decision_at, set()).add(role)
    assert all(len(roles) == 1 for roles in membership.values())
    reversed_result = run_synthetic_baseline(events.iloc[::-1], FEATURES)
    assert reversed_result == result


def test_calibration_is_independent_of_test_labels_and_features():
    events = synthetic_events()
    original = run_synthetic_baseline(events, FEATURES)
    test_ids = original["split"]["test"]["row_ids"]
    test = events["event_id"].isin(test_ids)
    changed = events.copy()
    changed.loc[test, "label"] = "TP"
    changed.loc[test, "feature_context"] = 1e8
    result = run_synthetic_baseline(changed, FEATURES)
    assert result["calibration"] == original["calibration"]
    assert result["preprocessing"] == original["preprocessing"]
    assert result["metrics"]["calibration"] == original["metrics"]["calibration"]
    labels_only = events.copy()
    labels_only.loc[test, "label"] = "SL"
    assert run_synthetic_baseline(labels_only, FEATURES)["predictions"] == original["predictions"]


@pytest.mark.parametrize("column", ["label", "outcome", "feature_future_return", "feature_outcome_alias", "feature_label", "feature_exit_price", "feature_futureReturn", "feature_terminalOutcome"])
def test_outcome_or_future_feature_aliases_are_rejected(column):
    events = synthetic_events()
    if column != "label":
        events[column] = 1.0
    with pytest.raises(ValueError, match="feature"):
        run_synthetic_baseline(events, (column,))


def test_real_data_is_rejected_before_fit():
    events = synthetic_events()
    events.loc[0, "venue"] = "kraken"
    with pytest.raises(PermissionError, match="synthetic"):
        run_synthetic_baseline(events, FEATURES)


def test_predictive_metrics_match_independent_class_ordered_calculation():
    events = synthetic_events()
    result = run_synthetic_baseline(events, FEATURES)
    squared_errors, log_losses = [], []
    labels = events.set_index("event_id")["label"]
    for prediction in result["predictions"]:
        correct = labels[prediction["event_id"]]
        squared_errors.append(sum((probability - int(name == correct)) ** 2
                                  for name, probability in prediction["probabilities"].items()))
        log_losses.append(-np.log(prediction["probabilities"][correct]))
    test_metrics = result["metrics"]["test"]["calibrated"]
    assert test_metrics["brier"] == pytest.approx(np.mean(squared_errors))
    assert test_metrics["log_loss"] == pytest.approx(np.mean(log_losses))


@pytest.mark.parametrize("mutation", ["duplicate_id", "naive_time", "missing_time", "label_before_decision", "infinite_feature", "allmissing_training_feature", "unsupported_label", "missing_training_class"])
def test_invalid_inputs_fail_closed(mutation):
    events = synthetic_events()
    if mutation == "duplicate_id":
        events.loc[1, "event_id"] = events.loc[0, "event_id"]
    elif mutation == "naive_time":
        events["decision_at"] = events["decision_at"].dt.tz_localize(None)
    elif mutation == "missing_time":
        events.loc[0, "decision_at"] = pd.NaT
    elif mutation == "label_before_decision":
        events.loc[0, "label_available_at"] = events.loc[0, "decision_at"]
    elif mutation == "infinite_feature":
        events.loc[0, "feature_context"] = float("inf")
    elif mutation == "allmissing_training_feature":
        events.loc[:89, "feature_context"] = np.nan
    elif mutation == "unsupported_label":
        events.loc[0, "label"] = "UNKNOWN"
    elif mutation == "missing_training_class":
        events.loc[:89, "label"] = "TP"
    with pytest.raises(ValueError):
        run_synthetic_baseline(events, FEATURES)


@pytest.mark.parametrize("kwargs", [{"train_fraction": float("nan")}, {"train_fraction": 0.8, "calibration_fraction": 0.3}, {"embargo_seconds": -1}, {"min_train_events": 0}, {"random_seed": True}])
def test_learning_config_rejects_invalid_values(kwargs):
    with pytest.raises(ValueError):
        LearningConfig(**kwargs)


def test_insufficient_purged_fold_is_unavailable():
    events = synthetic_events()
    events["label_available_at"] += pd.Timedelta(days=365)
    with pytest.raises(ValueError, match="insufficient"):
        run_synthetic_baseline(events, FEATURES)


def test_walk_forward_expands_training_and_keeps_tests_disjoint():
    events = synthetic_events()
    result = run_synthetic_walk_forward(events, FEATURES)
    previous_train = set()
    test_ids = []
    assert result["fold_count"] == 3
    for fold in result["folds"]:
        train_ids = set(fold["split"]["train"]["row_ids"])
        assert previous_train < train_ids
        previous_train = train_ids
        test_ids.extend(fold["split"]["test"]["row_ids"])
        for role, next_role in (("train", "calibration"), ("calibration", "test")):
            rows = events.set_index("event_id").loc[fold["split"][role]["row_ids"]]
            cutoff = pd.Timestamp(fold["split"][next_role]["start"]) - pd.Timedelta(seconds=14400)
            assert (rows["label_available_at"] < cutoff).all()
    assert len(test_ids) == len(set(test_ids))
    assert [prediction["event_id"] for prediction in result["predictions"]] == test_ids
    assert result["empirical_training"] is result["paper_execution"] is result["live_execution"] is False
    json.dumps(result, allow_nan=False)


def test_walk_forward_future_test_mutation_preserves_earlier_folds():
    events = synthetic_events()
    original = run_synthetic_walk_forward(events, FEATURES)
    last_test_ids = original["folds"][-1]["split"]["test"]["row_ids"]
    future = events["event_id"].isin(last_test_ids)
    events.loc[future, "feature_signal"] = 1e6
    events.loc[future, "label"] = "TP"
    changed = run_synthetic_walk_forward(events, FEATURES)
    assert changed["folds"][:-1] == original["folds"][:-1]
    assert changed["folds"][-1]["calibration"] == original["folds"][-1]["calibration"]


def test_named_split_boundaries_and_missing_fold_support_fail_closed():
    events = synthetic_events()
    with pytest.raises(ValueError, match="existing decision clock"):
        run_synthetic_baseline(events, FEATURES, split_boundaries=("2025-01-10T00:01:00Z", "2025-01-15T00:00:00Z"))
    with pytest.raises(ValueError, match="nonempty chronological"):
        run_synthetic_baseline(events, FEATURES, split_boundaries=(events.loc[100, "decision_at"].isoformat(),
                                                                  events.loc[90, "decision_at"].isoformat()))
    with pytest.raises(ValueError, match="insufficient test"):
        run_synthetic_walk_forward(events, FEATURES, n_splits=6)


@pytest.mark.parametrize("config", [False, 0, ""])
def test_invalid_falsey_config_is_not_silently_replaced(config):
    with pytest.raises(ValueError, match="LearningConfig"):
        run_synthetic_baseline(synthetic_events(), FEATURES, config)
    with pytest.raises(ValueError, match="LearningConfig"):
        run_synthetic_walk_forward(synthetic_events(), FEATURES, config)


def test_exported_json_model_scores_without_labels_and_matches_test_predictions():
    events = synthetic_events()
    bundle = json.loads(json.dumps(run_synthetic_baseline(events, FEATURES), allow_nan=False))
    test = events.set_index("event_id", drop=False).loc[bundle["split"]["test"]["row_ids"]]
    unlabelled = test.drop(columns=["label", "label_available_at"])
    predictions = score_frozen_synthetic_baseline(bundle, unlabelled)
    for actual, expected in zip(predictions, bundle["predictions"], strict=True):
        assert actual["event_id"] == expected["event_id"]
        assert actual["decision_at"] == expected["decision_at"]
        assert actual["probabilities"] == pytest.approx(expected["probabilities"])
        assert actual["entropy"] == pytest.approx(expected["entropy"])
        assert actual["shift_score"] == pytest.approx(expected["shift_score"])
    # Outcome extras cannot enter the frozen inference matrix.
    unlabelled["label"] = "INVALID_FUTURE_OUTCOME"
    unlabelled["outcome"] = float("inf")
    assert score_frozen_synthetic_baseline(bundle, unlabelled) == predictions


def test_frozen_model_tampering_invalid_shapes_and_real_venues_are_rejected():
    events = synthetic_events()
    bundle = run_synthetic_baseline(events, FEATURES)
    changed = json.loads(json.dumps(bundle))
    changed["model"]["coefficients"][0][0] += 0.1
    with pytest.raises(ValueError, match="integrity"):
        score_frozen_synthetic_baseline(changed, events)
    malformed = json.loads(json.dumps(bundle))
    malformed["preprocessing"]["scales"][0] = 0
    with pytest.raises(ValueError, match="state"):
        score_frozen_synthetic_baseline(malformed, events)
    missing = json.loads(json.dumps(bundle))
    missing["model"].pop("intercepts")
    with pytest.raises(ValueError, match="malformed"):
        score_frozen_synthetic_baseline(missing, events)
    events["venue"] = "kraken"
    with pytest.raises(PermissionError, match="synthetic"):
        score_frozen_synthetic_baseline(bundle, events)


def test_frozen_model_refuses_backdated_inference_and_binds_availability():
    events = synthetic_events()
    bundle = run_synthetic_baseline(events, FEATURES)
    assert bundle["model_available_at"] == bundle["split"]["test"]["start"]
    with pytest.raises(PermissionError, match="backdated"):
        score_frozen_synthetic_baseline(bundle, events.iloc[:1])
    changed = json.loads(json.dumps(bundle))
    changed["model_available_at"] = events.loc[0, "decision_at"].isoformat()
    with pytest.raises(ValueError, match="integrity"):
        score_frozen_synthetic_baseline(changed, events)
    naive = json.loads(json.dumps(bundle))
    naive["model_available_at"] = "2025-01-01T00:00:00"
    with pytest.raises(ValueError, match="model_available_at"):
        score_frozen_synthetic_baseline(naive, events)
