"""Primary-signal admission and independently specified execution boundaries."""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("purgedcv", reason="Primary ML contracts require the validation extra")
pytest.importorskip("xgboost", reason="Primary ML contracts require the AI stack")

from src.ml.meta_labeler import PRIMARY_FEATURES, PrimaryMetaLabeler, new_model
from src.ml.real_bot_pipeline import primary_outcome, trade_stream, fit_primary_model
from src.strategies import PrimarySignal


def candles(count=30):
    t = pd.date_range("2024-01-01", periods=count, freq="15min", tz="UTC")
    return pd.DataFrame({"timestamp": t, "available_at": t + pd.Timedelta(minutes=15),
                         "open": 100., "high": 100.5, "low": 99.5, "close": 100., "volume": 10.})


def test_barriers_use_next_open_not_signal_close_and_double_touch_loses():
    bars = candles(4)
    bars.loc[1:, ["open", "close", "high", "low"]] = [200., 200., 200.5, 199.5]
    bars.loc[2, ["high", "low"]] = [203., 198.]
    result = primary_outcome(bars, {"bar_index": 0, "atr": 1., "available_at": bars.available_at.iloc[0]})
    assert result["entry_price"] == 200 and result["exit_price"] == 199
    assert result["label"] == 0 and result["ambiguous"]


def test_missing_next_open_does_not_delay_entry():
    bars = candles().drop(index=1).reset_index(drop=True)
    assert primary_outcome(bars, {"bar_index": 0, "atr": 1., "available_at": bars.available_at.iloc[0]}) is None


def test_holding_gap_liquidates_at_first_later_observed_quote_never_retroactively():
    bars = candles().drop(index=2).reset_index(drop=True)
    bars.loc[2, ["open", "high", "low", "close"]] = [90., 91., 89., 90.]
    event = {"bar_index": 0, "atr": 10., "available_at": bars.available_at.iloc[0], "event_id": 1}
    outcome = primary_outcome(bars, event)
    assert outcome["label_reason"] == "data_gap" and outcome["label"] is None
    assert outcome["exit_time"] == bars.timestamp.iloc[2] and outcome["exit_price"] == 90.
    stream = trade_stream(pd.DataFrame([event | outcome]), bars, pd.Series({1: .9}), .8,
                          bars.timestamp.iloc[0], bars.available_at.iloc[-1])
    assert stream.iloc[0].mark_times[-1] == outcome["exit_time"]
    assert stream.iloc[0].mark_returns[-1] == pytest.approx(.9 * .9985 / 1.0015 - 1)


def test_twenty_bar_timeout_is_traded_and_exact_roundtrip_fees_are_charged():
    bars = candles()
    signal = {"bar_index": 0, "atr": 10., "available_at": bars.available_at.iloc[0], "event_id": 5}
    outcome = primary_outcome(bars, signal)
    assert outcome["exit_index"] == 20 and outcome["label"] is None
    assert outcome["net_return"] == pytest.approx(.9985 / 1.0015 - 1)
    events = pd.DataFrame([signal | outcome])
    stream = trade_stream(events, bars, pd.Series({5: .9}), .8, bars.timestamp.iloc[0], bars.available_at.iloc[-1])
    assert len(stream) == 1 and stream.label_reason.iloc[0] == "timeout"


def test_split_end_liquidates_without_later_label_information():
    bars = candles()
    bars.loc[9, "high"] = 140.
    signal = {"bar_index": 0, "atr": 10., "available_at": bars.available_at.iloc[0]}
    before = primary_outcome(bars, signal, end=bars.available_at.iloc[4])
    assert before["label_reason"] == "split_end" and before["exit_index"] == 4
    assert before["label"] is None
    assert primary_outcome(bars, signal)["label"] == 1


def test_primary_signal_emits_only_model_approved_closed_bar_crosses():
    t = pd.date_range("2024-01-01", periods=110, freq="15min", tz="UTC")
    close = np.r_[np.linspace(110, 90, 100), np.linspace(91, 150, 10)]
    bars = pd.DataFrame({"close": close, "available_at": t})
    indices = np.flatnonzero(PrimarySignal.raw_entries(bars))
    assert len(indices)
    end = indices[0] + 1
    features = pd.DataFrame({"available_at": [bars.available_at.iloc[end - 1]], "signal_confidence": [.8]})
    accepted = SimpleNamespace(threshold=.7, predict_proba=lambda _: np.array([.8]))
    rejected = SimpleNamespace(threshold=.7, predict_proba=lambda _: np.array([.6]))
    assert PrimarySignal(accepted).generate(bars.iloc[:end], features)["direction"] == "BUY"
    assert PrimarySignal(rejected).generate(bars.iloc[:end], features) is None
    with pytest.raises(ValueError, match="exact closed-bar"):
        PrimarySignal(accepted).generate(bars.iloc[:end], features.assign(available_at=t[0]))


def test_oof_scores_include_timeout_events_while_fit_targets_remain_binary():
    times = pd.date_range("2022-01-01", periods=100, freq="1D", tz="UTC")
    events = pd.DataFrame({c: np.linspace(0, 1, 100) for c in PRIMARY_FEATURES})
    events["event_id"], events["available_at"] = np.arange(100), times
    events["label_end"] = times + pd.Timedelta(hours=1)
    events["label"] = pd.Series(np.arange(100) % 2, dtype="Int8")
    events.loc[::7, "label"] = pd.NA
    _, threshold, probabilities, folds = fit_primary_model(events)
    assert len(probabilities) == 100 and np.isfinite(probabilities).all()
    assert len(folds) == 5 and threshold == pytest.approx(probabilities.quantile(.9))


def test_native_xgboost_json_roundtrip_has_identical_probabilities(tmp_path):
    x = pd.DataFrame({name: np.linspace(0, 1, 60) for name in PRIMARY_FEATURES})
    model = new_model("xgboost")
    model.fit(x, np.arange(60) % 2)
    labeler = PrimaryMetaLabeler(model, .75)
    path = tmp_path / "meta.json"
    labeler.save(path)
    reloaded = PrimaryMetaLabeler.load(path)
    np.testing.assert_array_equal(labeler.predict_proba(x), reloaded.predict_proba(x))
    assert reloaded.threshold == .75
