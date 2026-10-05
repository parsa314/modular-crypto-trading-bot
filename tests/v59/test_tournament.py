from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from research_bot.v59.fixtures import tournament_fixture_events
from research_bot.v59.tournament import (
    TournamentConfig,
    WalkForwardConfig,
    make_walk_forward_folds,
    run_tournament,
)


FEATURES = ("f_trend", "f_vol", "f_structure", "f_memory")


def test_walk_forward_has_strict_train_validation_test_order_and_embargo():
    cfg = WalkForwardConfig(
        min_train_rows=100,
        validation_rows=20,
        test_rows=30,
        step_rows=30,
        embargo_rows=2,
        max_folds=3,
    )
    folds = make_walk_forward_folds(300, cfg)
    assert len(folds) == 3
    for fold in folds:
        assert fold.train_end < fold.validation_start
        assert fold.validation_end < fold.test_start
        assert fold.validation_start - fold.train_end == 2
        assert fold.test_start - fold.validation_end == 2


def test_fixture_tournament_registers_every_model_cost_strategy_combination():
    events = tournament_fixture_events()
    cfg = TournamentConfig()
    result = run_tournament(events, feature_columns=FEATURES, config=cfg)
    assert len(result["strategy_ids"]) == 2
    expected = 2 * len(cfg.models) * len(cfg.cost_scenarios_bps)
    assert len(result["trial_results"]) == expected
    assert result["paper_execution"] is False
    assert result["live_execution"] is False
    assert len(result["registry_hash"]) == 64


def test_engineering_fixture_can_never_promote_a_model():
    result = run_tournament(
        tournament_fixture_events(),
        feature_columns=FEATURES,
        config=TournamentConfig(evidence_class="ENGINEERING_FIXTURE"),
    )
    assert result["trial_results"]
    assert all(row["promotable"] is False for row in result["trial_results"])
    assert {row["reason"] for row in result["trial_results"]} == {
        "ENGINEERING_FIXTURE_NOT_PROMOTABLE"
    }


def test_predictions_are_deterministic():
    events = tournament_fixture_events()
    first = run_tournament(events, feature_columns=FEATURES)
    second = run_tournament(events, feature_columns=FEATURES)
    assert first["registry_hash"] == second["registry_hash"]
    assert first["trial_results_hash"] == second["trial_results_hash"]
    assert first["attempted_folds_hash"] == second["attempted_folds_hash"]


def test_cost_stress_never_increases_coverage_for_same_prediction_path():
    result = run_tournament(tournament_fixture_events(), feature_columns=FEATURES)
    grouped = {}
    for row in result["trial_results"]:
        key = (row["strategy_id"], row["model_id"], row["prediction_hash"])
        grouped.setdefault(key, []).append(row)
    for rows in grouped.values():
        rows = sorted(rows, key=lambda x: x["cost_bps"])
        coverage = [row["coverage"] for row in rows]
        assert coverage == sorted(coverage, reverse=True)


def test_missing_feature_and_bad_label_fail_closed():
    events = tournament_fixture_events()
    with pytest.raises(ValueError, match="missing features"):
        run_tournament(events, feature_columns=("missing_feature",))
    bad = events.copy()
    bad.loc[0, "label"] = "WIN"
    with pytest.raises(ValueError, match="TP/SL/TIMEOUT"):
        run_tournament(bad, feature_columns=FEATURES)


def test_duplicate_event_identity_is_rejected():
    events = tournament_fixture_events()
    duplicate = pd.concat([events, events.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate event"):
        run_tournament(duplicate, feature_columns=FEATURES)


def test_tournament_records_all_attempted_folds_before_any_promotion_review():
    result = run_tournament(tournament_fixture_events(), feature_columns=FEATURES)
    assert result["attempted_folds"]
    keys = {
        (row["strategy_id"], row["model_id"], row["fold_id"])
        for row in result["attempted_folds"]
    }
    assert len(keys) == len(result["attempted_folds"])
    assert all(row["train_end_timestamp"] < row["validation_start_timestamp"] for row in result["attempted_folds"])
    assert all(row["validation_start_timestamp"] < row["test_start_timestamp"] for row in result["attempted_folds"])


def test_nonfixture_evidence_is_only_marked_for_separate_review_not_auto_promoted():
    # Complete temporal metadata is mandatory even in this synthetic contract
    # test. The evidence-class flag alone no longer bypasses time validation.
    events = tournament_fixture_events()
    events['decision_at'] = events.timestamp
    events['entry_time'] = events.timestamp + pd.Timedelta(seconds=1)
    events['information_start'] = events.timestamp
    events['event_end_time'] = events.timestamp + pd.Timedelta(seconds=20)
    events['information_end'] = events.event_end_time
    events['label_available_at'] = events.event_end_time
    events['feature_available_at'] = events.timestamp
    for name in ('feature_snapshot_id', 'data_version', 'strategy_version', 'source_hash'):
        events[name] = 'ENGINEERING_FIXTURE_'+name
    cfg = TournamentConfig(evidence_class="REAL_OOS_DEVELOPMENT",
                           walk_forward=WalkForwardConfig(embargo_hours=0))
    result = run_tournament(events, feature_columns=FEATURES, config=cfg)
    assert result["trial_results"]
    assert all(row["promotable"] is False for row in result["trial_results"])
    assert any(row["promotion_review_eligible"] for row in result["trial_results"])
    assert all(
        row["reason"] in {
            "ELIGIBLE_FOR_SEPARATE_PROMOTION_REVIEW",
            "INSUFFICIENT_TEST_EVENTS",
        }
        for row in result["trial_results"]
    )
