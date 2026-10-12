from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research_bot.candle_time_v53 import (
    CandleTimeContractV53,
    annotate_candle_times_v53,
    closed_bar_snapshot_v53,
    resample_closed_ohlcv_v53,
)
from research_bot.multitimeframe_v53 import (
    add_ichimoku_visibility_v53,
    build_multitimeframe_feature_frame_v53,
)
from research_bot.zone_lifecycle_v53 import add_zone_lifecycle_v53


def _ohlcv(n=160, freq="1h", seed=4):
    rng = np.random.default_rng(seed)
    ret = rng.normal(0.0, 0.003, n)
    close = 100 * np.exp(np.cumsum(ret))
    open_ = np.r_[close[0], close[:-1]]
    width = rng.uniform(0.001, 0.004, n) * close
    high = np.maximum(open_, close) + width
    low = np.minimum(open_, close) - width
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq=freq, tz="UTC"),
        "open": open_, "high": high, "low": low, "close": close,
        "volume": rng.lognormal(7, 0.2, n),
    })


def test_bar_open_timestamp_is_not_available_until_close():
    raw = _ohlcv(3, "4h")
    contract = CandleTimeContractV53("4h")
    timed = annotate_candle_times_v53(raw, contract=contract)
    assert timed.loc[0, "bar_open_at"] == pd.Timestamp("2026-01-01T00:00:00Z")
    assert timed.loc[0, "bar_close_at"] == pd.Timestamp("2026-01-01T04:00:00Z")
    assert timed.loc[0, "available_at"] == pd.Timestamp("2026-01-01T04:00:00Z")
    before = closed_bar_snapshot_v53(raw, decision_time="2026-01-01T03:59:59Z", contract=contract)
    after = closed_bar_snapshot_v53(raw, decision_time="2026-01-01T04:00:00Z", contract=contract)
    assert len(before) == 0
    assert len(after) == 1


def test_future_clock_skew_fails_closed():
    raw = _ohlcv(3, "1h")
    raw.loc[2, "timestamp"] = pd.Timestamp("2030-01-01T00:00:00Z")
    with pytest.raises(ValueError, match="future clock skew"):
        closed_bar_snapshot_v53(
            raw,
            decision_time="2026-01-01T01:30:00Z",
            contract=CandleTimeContractV53("1h", max_future_clock_skew=pd.Timedelta(seconds=5)),
        )


def test_resampled_4h_bar_available_only_after_all_four_1h_components_close():
    raw = _ohlcv(8, "1h")
    high = resample_closed_ohlcv_v53(raw, source_timeframe="1h", target_timeframe="4h")
    assert len(high) == 2
    assert high.loc[0, "bar_open_at"] == pd.Timestamp("2026-01-01T00:00:00Z")
    assert high.loc[0, "available_at"] == pd.Timestamp("2026-01-01T04:00:00Z")
    assert high.loc[0, "component_count"] == 4


def test_ichimoku_visible_cloud_is_shifted_past_not_future():
    n = 100
    frame = pd.DataFrame({
        "close": np.arange(n, dtype=float) + 100,
        "ichi_span_a_now": np.arange(n, dtype=float) + 10,
        "ichi_span_b_now": np.arange(n, dtype=float) + 20,
    })
    out = add_ichimoku_visibility_v53(frame)
    i = 60
    assert out.loc[i, "ichi_span_a_visible_now"] == out.loc[i - 26, "ichi_span_a_now"]
    assert out.loc[i, "ichi_span_b_visible_now"] == out.loc[i - 26, "ichi_span_b_now"]
    assert out.loc[i, "ichi_span_a_projected_t_plus_26"] == out.loc[i, "ichi_span_a_now"]


def test_fvg_is_not_filled_on_formation_bar_and_can_fill_later():
    x = pd.DataFrame({
        "high": [100, 101, 106, 108, 109, 110, 111],
        "low": [98, 99, 104, 105, 106, 103, 99],
        "close": [99, 100, 105, 107, 108, 104, 100],
        "ict_fvg_bull": [0, 0, 1, 0, 0, 0, 0],
        "ict_fvg_bear": [0] * 7,
        "ict_bull_ob_candidate": [0] * 7,
        "ict_bear_ob_candidate": [0] * 7,
        "ict_bull_ob_lower": [np.nan] * 7,
        "ict_bull_ob_upper": [np.nan] * 7,
        "ict_bear_ob_lower": [np.nan] * 7,
        "ict_bear_ob_upper": [np.nan] * 7,
    })
    out = add_zone_lifecycle_v53(x)
    assert out.loc[2, "ict_active_fvg_bull_count"] == 1
    assert out.loc[2, "ict_fvg_bull_fill"] == 0
    assert out.loc[5, "ict_active_fvg_bull_count"] >= 1
    assert out.loc[6, "ict_fvg_bull_fill"] == 1


def test_ob_invalidation_activates_breaker_only_on_later_bar():
    n = 6
    x = pd.DataFrame({
        "high": [10, 12, 13, 14, 13, 12],
        "low": [9, 10, 11, 12, 10, 9],
        "close": [9.5, 11, 12, 13.5, 10.5, 9.5],
        "ict_fvg_bull": [0] * n,
        "ict_fvg_bear": [0] * n,
        "ict_bull_ob_candidate": [0, 1, 0, 0, 0, 0],
        "ict_bear_ob_candidate": [0] * n,
        "ict_bull_ob_lower": [np.nan, 10, np.nan, np.nan, np.nan, np.nan],
        "ict_bull_ob_upper": [np.nan, 12, np.nan, np.nan, np.nan, np.nan],
        "ict_bear_ob_lower": [np.nan] * n,
        "ict_bear_ob_upper": [np.nan] * n,
    })
    out = add_zone_lifecycle_v53(x)
    assert out.loc[5, "ict_bull_ob_invalidation"] == 1
    assert out.loc[5, "ict_bear_breaker_retest"] == 0


def test_mtf_join_uses_decision_at_and_does_not_expose_4h_early():
    one_h = _ohlcv(140, "1h")
    four_h = resample_closed_ohlcv_v53(one_h, source_timeframe="1h", target_timeframe="4h")
    raw_four_h = four_h[["timestamp", "open", "high", "low", "close", "volume"]].copy()
    out = build_multitimeframe_feature_frame_v53(
        {"1h": one_h, "4h": raw_four_h},
        decision_timeframe="1h",
    )
    assert "decision_at" in out.columns
    assert (out["decision_at"] == out["1h_available_at"]).all()
    assert (out["decision_at"] >= out["bar_close_at"]).all()
    valid = out["4h_available_at"].notna()
    assert (out.loc[valid, "4h_available_at"] <= out.loc[valid, "decision_at"]).all()


def test_future_4h_price_change_cannot_change_prior_joined_1h_features():
    one_h = _ohlcv(160, "1h")
    four_h = resample_closed_ohlcv_v53(one_h, source_timeframe="1h", target_timeframe="4h")
    raw_four_h = four_h[["timestamp", "open", "high", "low", "close", "volume"]].copy()
    base = build_multitimeframe_feature_frame_v53({"1h": one_h, "4h": raw_four_h}, decision_timeframe="1h")
    altered = raw_four_h.copy()
    cut = 80
    altered.loc[altered["timestamp"] > one_h.loc[cut, "timestamp"], ["open", "high", "low", "close"]] *= 5
    changed = build_multitimeframe_feature_frame_v53({"1h": one_h, "4h": altered}, decision_timeframe="1h")
    cols = [c for c in base.columns if c.startswith("4h_") and pd.api.types.is_numeric_dtype(base[c])]
    np.testing.assert_allclose(
        base.loc[:cut, cols].astype(float).to_numpy(),
        changed.loc[:cut, cols].astype(float).to_numpy(),
        rtol=0, atol=1e-12, equal_nan=True,
    )
