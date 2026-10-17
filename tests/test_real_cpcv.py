"""Temporal boundaries and independently calculated selection statistics."""
from itertools import combinations

import numpy as np
import pandas as pd
import pytest
from scipy import stats

pytest.importorskip("purgedcv")

from src.validation.real_bot_cpcv import build_cpcv_layout, summarize_paths, rank_utility


def event_fixture(days=180):
    times = pd.date_range("2022-01-01", periods=days, freq="1D", tz="UTC")
    prediction = pd.DatetimeIndex(np.repeat(times.to_numpy(), 2)) + pd.to_timedelta(np.tile([15, 45], days), unit="m")
    return pd.DataFrame({"event_id": np.arange(len(prediction)), "available_at": prediction,
                         "exit_time": prediction + pd.Timedelta(hours=5),
                         "label": np.tile([1, None], days)})


def test_installed_cpcv_uses_fifteen_splits_and_never_divides_same_calendar_date():
    events = event_fixture()
    layout = build_cpcv_layout(events, start="2022-01-01", end="2022-06-30")
    assert layout.splitter.__class__.__module__.startswith("purgedcv.")
    assert layout.splitter.get_n_splits() == len(layout.folds) == 15
    test_appearances = np.zeros(len(layout.dates), dtype=int)
    for fold in layout.folds:
        train = events[events.available_at.dt.floor("D").isin(layout.dates[fold.train_dates])]
        held = events[events.available_at.dt.floor("D").isin(layout.dates[fold.test_dates])]
        assert set(train.available_at.dt.floor("D")).isdisjoint(held.available_at.dt.floor("D"))
        padding = pd.Timedelta(minutes=75)
        for row in train.itertuples():
            overlaps = ((held.available_at <= row.exit_time + padding)
                        & (held.exit_time >= row.available_at - padding))
            assert not overlaps.any()
        test_appearances[fold.test_dates] += 1
    assert np.all(test_appearances == 5)


def test_reconstruction_has_five_paths_without_pooling_repeated_calendar_dates():
    layout = build_cpcv_layout(event_fixture(), start="2022-01-01", end="2022-06-30")
    predictions = [np.full(len(f.test_dates), float(f.fold_id)) for f in layout.folds]
    paths = layout.splitter.reconstruct_paths(predictions)
    assert paths.shape == (5, len(layout.dates))
    assert np.isfinite(paths).all()


def test_timeouts_have_real_intervals_and_missing_exit_times_are_not_fabricated():
    events = event_fixture()
    layout = build_cpcv_layout(events)
    assert len(layout.folds) == 15
    events.loc[0, "exit_time"] = pd.NaT
    with pytest.raises(ValueError, match="actual exit_time"):
        build_cpcv_layout(events)


def candidate_returns():
    rng = np.random.default_rng(718)
    fixed = rng.normal(.0002, .01, 180)
    ppo = rng.normal(.001, .012, 180)
    return np.array([fixed, ppo])


def test_pbo_is_cscv_selection_rank_not_the_fraction_of_losing_folds():
    matrix = candidate_returns()
    chunks = np.array_split(np.arange(matrix.shape[1]), 6)
    overfit = []
    for selected in combinations(range(6), 3):
        inside = np.concatenate([chunks[i] for i in selected])
        outside = np.concatenate([chunks[i] for i in range(6) if i not in selected])
        is_score = matrix[:, inside].mean(axis=1) / matrix[:, inside].std(axis=1)
        oos_score = matrix[:, outside].mean(axis=1) / matrix[:, outside].std(axis=1)
        best = int(np.argmax(is_score))
        overfit.append(oos_score[best] < oos_score[1 - best])
    report = summarize_paths(np.repeat(matrix[None, :, :], 5, axis=0))
    assert report["pbo"] == pytest.approx(np.mean(overfit))
    assert report["path_statistics"][0]["pbo_diagnostics"]["n_combos"] == 20
    assert report["candidate_count"] == 2
    assert report["pbo_scope"] == ["fixed_fraction_2pct", "ppo"]


def test_dsr_uses_per_observation_sharpe_and_non_excess_kurtosis():
    matrix = candidate_returns()
    trial_sr = matrix.mean(axis=1) / matrix.std(axis=1, ddof=0)
    variance = float(trial_sr.var(ddof=1))
    euler = .5772156649015329
    expected_max = (1 - euler) * stats.norm.ppf(1 - 1 / 2) + euler * stats.norm.ppf(1 - 1 / (2 * np.e))
    benchmark = np.sqrt(variance) * expected_max
    returns, sr = matrix[1], trial_sr[1]
    skew = stats.skew(returns, bias=False)
    kurtosis = stats.kurtosis(returns, bias=False, fisher=False)
    denominator = np.sqrt(1 - skew * sr + (kurtosis - 1) * sr ** 2 / 4)
    expected = stats.norm.cdf((sr - benchmark) * np.sqrt(len(returns) - 1) / denominator)
    report = summarize_paths(np.repeat(matrix[None, :, :], 5, axis=0))
    first = report["path_statistics"][0]
    assert first["dsr"] == pytest.approx(expected)
    assert first["dsr_diagnostics"]["sr_star"] == pytest.approx(benchmark)
    assert first["dsr_diagnostics"]["n_obs"] == 180  # not five duplicated paths
    assert first["dsr_diagnostics"]["n_trials"] >= 2
    assert report["dsr"] == first["dsr"]


def test_cash_sharpe_is_undefined_and_cash_cannot_win_rank_utility():
    fixed = -np.linspace(.0001, .002, 180)
    matrix = np.array([fixed, np.zeros(180)])
    assert rank_utility(matrix[1]) < rank_utility(matrix[0])
    report = summarize_paths(np.repeat(matrix[None, :, :], 5, axis=0))
    assert report["dsr"] is None and not report["dsr_defined"]
    assert report["path_statistics"][0]["ppo_sharpe"] is None
    assert report["conservative_dsr_rejection_bound"] == 0.
    assert report["verdict"] == "REJECTED"


def test_identical_cash_configurations_do_not_produce_a_bogus_pbo():
    report = summarize_paths(np.zeros((5, 2, 120)))
    assert report["pbo"] is None and not report["pbo_defined"]
    assert report["dsr"] is None and report["verdict"] == "REJECTED"
