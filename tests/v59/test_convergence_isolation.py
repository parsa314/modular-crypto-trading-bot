import numpy as np
import pandas as pd
import pytest

from test_convergence_event_times import timed_events, FEATURES
from research_bot.v59 import tournament as t


def protocol():
    return t.TournamentConfig(models=('LOGISTIC',), cost_scenarios_bps=(24.,),
        evidence_class='REAL_MARKET_EVENT_DATA', walk_forward=t.WalkForwardConfig(max_folds=1, embargo_hours=0))


def test_long_horizon_overlap_and_late_label_cannot_reach_fit(monkeypatch):
    x = timed_events(); x = x[x.strategy_id == x.strategy_id.iloc[0]].reset_index(drop=True)
    boundary = x.timestamp.iloc[241]
    # Two distinct failure mechanisms: active outcome and publication delay.
    x.loc[0, ['event_end_time', 'information_end', 'label_available_at']] = boundary+pd.Timedelta(hours=2)
    x.loc[1, 'label_available_at'] = boundary+pd.Timedelta(hours=2)
    x.loc[2, 'information_end'] = boundary+pd.Timedelta(hours=3)
    x.loc[2, 'label_available_at'] = boundary+pd.Timedelta(hours=3)
    fitted = []
    original = t._fit_model
    def fit(model, X, y, seed):
        fitted.append(set(X.index));return original(model, X, y, seed)
    monkeypatch.setattr(t, '_fit_model', fit)
    t.run_tournament(x, feature_columns=FEATURES, config=protocol())
    assert fitted and {0, 1, 2}.isdisjoint(fitted[0])
    assert 3 in fitted[0]  # Nonoverlapping sample is retained.
    assert max(fitted[0]) < 241  # No validation/future test observation reaches fitting.


def test_scaler_train_only_calibration_validation_only_and_test_not_retuned(monkeypatch):
    x = timed_events(); x = x[x.strategy_id == x.strategy_id.iloc[0]].reset_index(drop=True)
    cfg = protocol(); fold = t._interval_folds(x, cfg.walk_forward)[0]
    # Sentinel test features and labels. Training and calibration must not change.
    base_fit, base_cal = t._fit_model, t._calibrate_isotonic
    recorded = []
    def fit(model, X, y, seed):
        result = base_fit(model, X, y, seed)
        assert np.allclose(result.named_steps['scale'].mean_, X.mean().to_numpy())
        recorded.append(('train', X.copy(), y.copy(), result.named_steps['scale'].mean_.copy()))
        return result
    def calibrate(p, y, target):
        recorded.append(('cal', p.copy(), y.copy()))
        assert min(y.index) >= fold.validation_start and max(y.index) < fold.validation_end
        return base_cal(p, y, target)
    monkeypatch.setattr(t, '_fit_model', fit);monkeypatch.setattr(t, '_calibrate_isotonic', calibrate)
    first = t.run_tournament(x, feature_columns=FEATURES, config=cfg)
    original = recorded.copy();recorded.clear()
    x.loc[fold.test_start:, 'label'] = 'TIMEOUT'
    x.loc[fold.test_start:, 'realized_gross_return'] = -.9
    x['realized_gross_return'] = x.realized_gross_return.fillna(0.)
    second = t.run_tournament(x, feature_columns=FEATURES, config=cfg)
    pd.testing.assert_frame_equal(original[0][1], recorded[0][1])
    pd.testing.assert_series_equal(original[0][2], recorded[0][2])
    pd.testing.assert_series_equal(original[1][2], recorded[1][2])
    assert np.array_equal(original[1][1], recorded[1][1])
    assert first['trial_results'][0]['coverage'] == second['trial_results'][0]['coverage']
    recorded.clear()
    x.loc[fold.test_start:, list(FEATURES)] = 1_000_000.
    t.run_tournament(x, feature_columns=FEATURES, config=cfg)
    pd.testing.assert_frame_equal(original[0][1], recorded[0][1])
    assert np.array_equal(original[1][1], recorded[1][1])


def test_all_clock_groups_and_folds_are_expanding_and_embargoed():
    x = timed_events(); doubled = pd.concat([x, x.assign(event_id=x.event_id+'-other')]).sort_values(['strategy_id','timestamp']).reset_index(drop=True)
    cfg = t.WalkForwardConfig(embargo_rows=3, embargo_hours=1)
    for _, group in doubled.groupby('strategy_id'):
        folds = t._interval_folds(group.reset_index(drop=True), cfg)
        assert folds
        ends = []
        for f in folds:
            assert f.train_start == 0 and f.train_end < f.validation_start < f.validation_end < f.test_start < f.test_end
            ends.append(f.train_end)
            assert f.validation_start-f.train_end == 6 and f.test_start-f.validation_end == 6
        assert ends == sorted(set(ends))


@pytest.mark.parametrize('name', ['feature_snapshot_id','data_version','strategy_version','source_hash'])
def test_nonempty_event_provenance_is_mandatory(name):
    x = timed_events();x.loc[0, name] = ''
    with pytest.raises(ValueError, match='provenance'):
        t.run_tournament(x, feature_columns=FEATURES, config=protocol())


def test_train_interval_also_cannot_overlap_earlier_test_information_start(monkeypatch):
    x = timed_events();x = x[x.strategy_id == x.strategy_id.iloc[0]].reset_index(drop=True)
    cfg = protocol();f = t._interval_folds(x, cfg.walk_forward)[0]
    test_cutoff = x.timestamp.iloc[100]
    x.loc[f.test_start:f.test_end-1, 'information_start'] = test_cutoff
    captured = [];original = t._fit_model
    def fit(model, X, y, seed):
        captured.append(set(X.index));return original(model, X, y, seed)
    monkeypatch.setattr(t, '_fit_model', fit)
    result = t.run_tournament(x, feature_columns=FEATURES, config=cfg)
    assert captured == []  # Validation also overlaps: no admissible calibration remains.
    assert all(row['status'] == 'SKIPPED_AFTER_INTERVAL_PURGE' for row in result['attempted_folds'])
