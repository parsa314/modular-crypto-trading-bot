import numpy as np
import pandas as pd
import pytest

from research_bot.v59.fixtures import tournament_fixture_events
from research_bot.v59.tournament import TournamentConfig, WalkForwardConfig, _economic_path, _interval_folds, run_tournament


FEATURES = ("f_trend", "f_vol", "f_structure", "f_memory")


def real_events():
    x = tournament_fixture_events()
    x["information_end"] = x.timestamp + pd.Timedelta(minutes=30)
    x['decision_at'] = x.timestamp
    x['entry_time'] = x.timestamp + pd.Timedelta(seconds=1)
    x['information_start'] = x.timestamp
    x['event_end_time'] = x.information_end
    x['label_available_at'] = x.information_end
    x['feature_available_at'] = x.timestamp
    for name in ('feature_snapshot_id', 'data_version', 'strategy_version', 'source_hash'):
        x[name] = 'ENGINEERING_FIXTURE_'+name
    return x


def test_real_class_requires_interval_provenance():
    with pytest.raises(ValueError, match="interval"):
        run_tournament(tournament_fixture_events(), feature_columns=FEATURES,
                       config=TournamentConfig(evidence_class="REAL_MARKET_EVENT_DATA"))


def test_outcome_purge_and_time_embargo_are_auditable():
    r = run_tournament(real_events(), feature_columns=FEATURES,
                       config=TournamentConfig(evidence_class="REAL_MARKET_EVENT_DATA", models=("PRIOR",),
                                               walk_forward=WalkForwardConfig(embargo_hours=.25)))
    assert r["attempted_folds"]
    for fold in r["attempted_folds"]:
        if fold.get("status"):
            continue
        assert pd.Timestamp(fold["train_max_information_end"]) < pd.Timestamp(fold["validation_start_timestamp"]) - pd.Timedelta(minutes=15)
        assert pd.Timestamp(fold["validation_max_information_end"]) < pd.Timestamp(fold["test_start_timestamp"]) - pd.Timedelta(minutes=15)
    assert r["event_compounding_is_investable"] is False
    assert all(not t["promotable"] for t in r["trial_results"])


def test_real_outcome_cannot_be_used_as_predictive_feature():
    x = real_events()
    x["realized_gross_return"] = .01
    with pytest.raises(ValueError, match="features"):
        run_tournament(x, feature_columns=("realized_gross_return",),
                       config=TournamentConfig(evidence_class="REAL_MARKET_EVENT_DATA"))


def test_timeout_return_is_only_read_after_admission():
    x = pd.DataFrame({"reward_fraction": [.1, .1], "loss_fraction": [.01, .01],
                      "timeout_loss_fraction": [.01, .01], "label": ["TIMEOUT", "TIMEOUT"],
                      "realized_gross_return": [.03, -.05]})
    p = np.tile([.8, .1, .1], (2, 1))
    admitted, realized = _economic_path(x, p, cost_bps=24, entropy_threshold=1)
    assert admitted.all()
    assert realized == pytest.approx([.0276, -.0524])
    x.realized_gross_return *= -1
    changed, _ = _economic_path(x, p, cost_bps=24, entropy_threshold=1)
    assert np.array_equal(admitted, changed)


def test_overlapping_oos_windows_rejected():
    with pytest.raises(ValueError, match="Overlapping"):
        WalkForwardConfig(test_rows=80, step_rows=40)


def test_same_clock_assets_never_straddle_partition_boundaries():
    x = real_events().loc[lambda f: f.strategy_id == f.strategy_id.iloc[0]].copy()
    doubled = pd.concat([x, x], ignore_index=True).sort_values("timestamp").reset_index(drop=True)
    folds = _interval_folds(doubled, WalkForwardConfig())
    for fold in folds:
        for name in ("train_end", "validation_start", "validation_end", "test_start", "test_end"):
            boundary = getattr(fold, name)
            if 0 < boundary < len(doubled):
                assert doubled.timestamp.iloc[boundary - 1] < doubled.timestamp.iloc[boundary]
