from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from research_bot.v59.native_dataset import build_native_events
from research_bot.v59.native_features import add_native_features
from research_bot.v59.native_strategy import NativeStrategyConfig, scan_native


def trending(n=180):
    p = 100 + np.arange(n) * .5
    return pd.DataFrame({"timestamp": pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC"),
                         "open": p, "high": p + .6, "low": p - .2, "close": p + .4,
                         "volume": 100 + np.arange(n) % 10})


def scan(frame, as_of=None):
    return scan_native(frame, venue="binance", symbol="BTC/USDT", data_version="TEST_FIXTURE",
                       as_of=as_of or frame.timestamp.iloc[-1] + pd.Timedelta(hours=1),
                       config=NativeStrategyConfig(timeframe="1h"))


def test_native_features_prefix_is_unchanged_by_future_prices():
    a = trending()
    b = a.copy()
    b.loc[130:, ["open", "high", "low", "close"]] *= 2
    pd.testing.assert_frame_equal(add_native_features(a).iloc[:130], add_native_features(b).iloc[:130])


def test_signal_identity_and_snapshot_are_prefix_stable():
    a = trending()
    full, full_snapshots = scan(a)
    partial, partial_snapshots = scan(a.iloc[:130])
    past = [s for s in full if pd.Timestamp(s.decision_at) <= a.timestamp.iloc[129] + pd.Timedelta(hours=1)]
    assert [s.event_id for s in past] == [s.event_id for s in partial]
    assert full_snapshots | partial_snapshots == full_snapshots
    assert partial


def test_available_close_strictly_precedes_entry_and_reference_has_no_future_open():
    f = trending()
    signals, snapshots = scan(f)
    for signal in signals:
        assert signal.decision_at < signal.entry_time
        assert pd.Timestamp(signal.entry_time) - pd.Timestamp(signal.decision_at) == pd.Timedelta(hours=1)
        decision_bar = f.loc[f.timestamp + pd.Timedelta(hours=1) == pd.Timestamp(signal.decision_at)].iloc[0]
        assert signal.entry_price == decision_bar.close
        assert snapshots[signal.feature_snapshot_id]["available_at"] == pd.Timestamp(signal.decision_at).isoformat()
        assert signal.regime_confidence == 0  # no fabricated regime probability


def test_visible_cloud_requires_both_displaced_spans():
    f = trending()
    x = add_native_features(f)
    assert x.price_cloud_distance_atr.iloc[:77].isna().all()
    high, low = f.high, f.low
    tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2
    kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2
    a = ((tenkan + kijun) / 2).shift(26)
    b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
    expected = (f.close.iloc[100] - max(a.iloc[100], b.iloc[100])) / (x.ATR_percent.iloc[100] * f.close.iloc[100])
    assert x.price_cloud_distance_atr.iloc[100] == pytest.approx(expected)


def test_labels_keep_information_end_and_censor_unobserved_future():
    f = trending()
    events, report = build_native_events(f, venue="binance", symbol="BTC/USDT", data_version="TEST_FIXTURE",
                                         as_of=f.timestamp.iloc[-1] + pd.Timedelta(hours=1),
                                         config=NativeStrategyConfig(timeframe="1h"))
    assert len(events)
    assert (events.information_end > events.entry_time).all()
    assert (events.feature_available_at <= events.decision_at).all()
    assert (events.decision_at < events.entry_time).all()
    assert report["execution_authorized"] is False
    assert report["economic_portfolio_metrics"] is None
    assert any(e["reason"] in {"RIGHT_CENSORED", "NO_OBSERVED_ENTRY"} for e in report["excluded"])
    assert np.allclose(events.timeout_loss_fraction, events.loss_fraction)


def test_open_candle_and_future_path_do_not_enter_candidate_features():
    f = trending()
    cutoff = f.timestamp.iloc[130]
    expected, _ = scan(f.iloc[:130])
    actual, _ = scan(f, as_of=cutoff)
    assert expected == actual


def test_missing_hour_and_naive_timestamps_rejected():
    with pytest.raises(ValueError, match="contiguous"):
        scan(trending().drop(index=100))
    f = trending()
    f.timestamp = f.timestamp.dt.tz_localize(None)
    with pytest.raises(ValueError, match="UTC"):
        scan(f, as_of=pd.Timestamp("2025-01-01T00:00:00Z"))


def test_native_config_rejects_short_warmup():
    with pytest.raises(ValueError):
        replace(NativeStrategyConfig(), minimum_history=20)


def test_future_coinex_holdout_is_sealed():
    f = trending()
    f.timestamp += pd.Timedelta(days=1000)
    with pytest.raises(ValueError, match="SEALED"):
        scan_native(f, venue="coinex", symbol="BTC/USDT", data_version="FORBIDDEN",
                    as_of=f.timestamp.iloc[-1] + pd.Timedelta(hours=4))
