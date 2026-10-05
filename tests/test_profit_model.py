import numpy as np
import pandas as pd
import pytest

from research_bot.research.profit_features import FEATURE_COLUMNS, add_profit_labels, build_profit_features
from research_bot.research.profit_model import (
    FoldRejected, ProfitFold, calendar_folds, fit_profit_fold, probability_diagnostics, walk_forward_predictions,
)


@pytest.fixture(scope="module")
def source():
    rng = np.random.default_rng(17)
    timestamps = pd.date_range("2022-01-01", "2022-09-01", freq="h", inclusive="left", tz="UTC")
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.006, len(timestamps))))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"timestamp": timestamps, "open": open_, "close": close,
                         "high": np.maximum(open_, close) + 0.1, "low": np.minimum(open_, close) - 0.1,
                         "volume": rng.uniform(1, 500, len(timestamps))})


@pytest.fixture(scope="module")
def featured(source):
    return add_profit_labels(build_profit_features(source))


@pytest.fixture(scope="module")
def fitted(featured):
    return fit_profit_fold(featured, calendar_folds("2022-01-01", "2022-09-01")[0])


def test_calendar_fold_boundaries_and_test_windows_do_not_overlap():
    folds = calendar_folds("2022-01-01", "2025-01-01")
    assert len(folds) == 29
    assert folds[0].validation_start == pd.Timestamp("2022-07-01", tz="UTC")
    assert folds[0].test_start == pd.Timestamp("2022-08-01", tz="UTC")
    assert folds[-1].test_end == pd.Timestamp("2025-01-01", tz="UTC")
    for previous, current in zip(folds, folds[1:]):
        assert previous.test_end == current.test_start
        assert current.validation_start == current.train_start + pd.DateOffset(months=6)


@pytest.mark.parametrize("start,end", [("2022-01-02", "2025-01-01"), ("2022-01-01", "2022-01-01"),
                                     ("2022-01-01", "2024-12-31"), ("2022-01-01T01:00:00Z", "2025-01-01")])
def test_calendar_requires_whole_month_windows(start, end):
    with pytest.raises(ValueError, match="calendar-month"):
        calendar_folds(start, end)


def test_direct_fold_cannot_disable_embargo_or_change_frozen_calendar():
    with pytest.raises(ValueError, match="24h embargo"):
        ProfitFold(0, "2022-01-01", "2022-07-01", "2022-08-01", "2022-09-01", embargo_hours=-1)
    with pytest.raises(ValueError, match="6/1/1"):
        ProfitFold(0, "2022-01-01", "2022-06-01", "2022-08-01", "2022-09-01")


def test_walkforward_rejects_missing_configured_data_and_short_studies(source):
    with pytest.raises(ValueError, match="complete configured study period"):
        walk_forward_predictions(source.iloc[:-1], end="2022-09-01")
    with pytest.raises(ValueError, match="one complete"):
        walk_forward_predictions(source, end="2022-03-01")


def test_label_overlap_is_purged_with_additional_embargo(fitted, featured):
    fold = fitted.fold
    for phase, boundary in (("train", fold.validation_start), ("validation", fold.test_start)):
        predictions = fitted.predict(featured, phase)
        assert predictions["label_end"].max() < boundary - pd.Timedelta(hours=24)
        assert predictions["timestamp"].max() < boundary - pd.Timedelta(hours=48)
        assert not predictions["is_out_of_sample"].any()
    test = fitted.predict(featured, "test")
    assert test["timestamp"].min() == fold.test_start
    assert test["timestamp"].max() == fold.test_end - pd.Timedelta(hours=1)
    assert test["is_out_of_sample"].all()


def test_scaler_is_fit_to_train_only(fitted, featured):
    train = fitted.predict(featured, "train")
    expected = featured.loc[train.index, list(FEATURE_COLUMNS)].mean().to_numpy()
    np.testing.assert_allclose(fitted.pipeline.named_steps["scaler"].mean_, expected, rtol=1e-12, atol=1e-12)
    assert fitted.metadata["scaler_fit_phase"] == "train"
    assert fitted.metadata["calibration_fit_phase"] == "validation"


def test_perturbing_test_features_and_targets_cannot_change_fitted_parameters(fitted, featured):
    changed = featured.copy()
    future = changed["timestamp"] >= fitted.fold.test_start
    changed.loc[future, list(FEATURE_COLUMNS)] *= 123
    changed.loc[future, "target"] = 1 - changed.loc[future, "target"]
    other = fit_profit_fold(changed, fitted.fold)
    np.testing.assert_array_equal(fitted.pipeline.named_steps["scaler"].mean_, other.pipeline.named_steps["scaler"].mean_)
    np.testing.assert_array_equal(fitted.pipeline.named_steps["classifier"].coef_, other.pipeline.named_steps["classifier"].coef_)
    np.testing.assert_array_equal(fitted.calibrator.coef_, other.calibrator.coef_)
    np.testing.assert_array_equal(fitted.calibrator.intercept_, other.calibrator.intercept_)


def test_future_ohlcv_repricing_cannot_leak_through_labels_or_features(fitted, source):
    changed = source.copy()
    future = changed["timestamp"] >= fitted.fold.test_start
    changed.loc[future, ["open", "high", "low", "close"]] *= 2
    changed.loc[future, "volume"] *= 7
    other = fit_profit_fold(add_profit_labels(build_profit_features(changed)), fitted.fold)
    np.testing.assert_array_equal(fitted.pipeline.named_steps["classifier"].coef_, other.pipeline.named_steps["classifier"].coef_)
    np.testing.assert_array_equal(fitted.calibrator.coef_, other.calibrator.coef_)


def test_validation_changes_calibrator_but_not_base_model(fitted, featured):
    changed = featured.copy()
    validation = changed["timestamp"].ge(fitted.fold.validation_start) & changed["timestamp"].lt(fitted.fold.test_start)
    changed.loc[validation, "target"] = 1 - changed.loc[validation, "target"]
    other = fit_profit_fold(changed, fitted.fold)
    np.testing.assert_array_equal(fitted.pipeline.named_steps["classifier"].coef_, other.pipeline.named_steps["classifier"].coef_)
    assert not np.allclose(fitted.calibrator.coef_, other.calibrator.coef_)


def test_single_class_and_insufficient_calibration_are_rejected(fitted, featured):
    changed = featured.copy()
    validation = changed["timestamp"].ge(fitted.fold.validation_start) & changed["timestamp"].lt(fitted.fold.test_start)
    changed.loc[validation, "target"] = 1.0
    with pytest.raises(FoldRejected, match="validation"):
        fit_profit_fold(changed, fitted.fold)
    changed.loc[validation, "feature_ready"] = False
    with pytest.raises(FoldRejected, match="validation"):
        fit_profit_fold(changed, fitted.fold)


def test_probability_diagnostics_use_fixed_bins_and_missing_targets():
    diagnostics = probability_diagnostics(np.array([0, 0, 1, 1, np.nan]), np.array([0, 0.2, 0.8, 1, 0.4]))
    assert diagnostics == pytest.approx({"n": 4, "brier": 0.02, "ece_10": 0.1})
    assert probability_diagnostics(np.array([np.nan]), np.array([0.4])) == {"n": 0, "brier": None, "ece_10": None}
    with pytest.raises(ValueError, match="probabilities"):
        probability_diagnostics(np.array([0, 1]), np.array([0.5, np.inf]))


def test_walkforward_keeps_all_phases_and_auditable_metadata(source):
    result = walk_forward_predictions(source, end="2022-09-01")
    assert len(result.folds) == 1
    assert result.folds[0]["status"] == "FITTED"
    assert set(result.predictions["phase"]) == {"train", "validation", "test"}
    test = result.predictions.query("phase == 'test'")
    assert len(test) == 31 * 24
    assert test["target"].isna().sum() == 24
    assert test["probability"].between(0, 1).all()
    assert set(result.folds[0]["diagnostics"]) == {"train", "validation", "test"}


def test_rejected_folds_never_emit_placeholder_probabilities(source):
    changed = source.copy()
    changed[["open", "close"]] = 100.0
    changed["high"], changed["low"] = 101.0, 99.0
    result = walk_forward_predictions(changed, end="2022-09-01")
    assert result.folds[0]["status"] == "REJECTED"
    assert result.predictions.empty
