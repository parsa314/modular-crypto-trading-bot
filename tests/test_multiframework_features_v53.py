from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research_bot.multiframework_features_v53 import (
    V53FeatureConfig,
    _confirmed_pivot,
    asof_join_available_features_v53,
    build_v53_feature_frame,
    validate_ohlcv_v53,
)
from research_bot.ontology_v53 import ontology_by_id, ontology_manifest, output_to_concept


def _sample(n: int = 320, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ret = rng.normal(0.0002, 0.004, n)
    close = 100.0 * np.exp(np.cumsum(ret))
    open_ = np.r_[close[0], close[:-1]]
    spread = rng.uniform(0.001, 0.006, n)
    high = np.maximum(open_, close) * (1.0 + spread)
    low = np.minimum(open_, close) * (1.0 - spread)
    vol = rng.lognormal(8.0, 0.3, n)
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC"),
        "open": open_, "high": high, "low": low, "close": close, "volume": vol,
    })


def test_ontology_is_complete_unique_and_machine_readable():
    registry = ontology_by_id()
    assert len(registry) >= 20
    assert {"ict_order_block", "ict_fvg", "ict_breaker_block", "ict_killzones"} <= set(registry)
    assert {"brooks_always_in", "brooks_h1_h2_l1_l2", "ichimoku_chikou_context"} <= set(registry)
    outputs = output_to_concept()
    assert len(outputs) == len(set(outputs))
    manifest = ontology_manifest()
    assert len(manifest) == len(registry)
    assert all(row["operational_definition"] for row in manifest)
    assert all(row["interpretation_boundary"] for row in manifest)


def test_input_validation_fails_closed():
    x = _sample(20)
    bad = x.copy(); bad.loc[3, "high"] = bad.loc[3, "low"] - 1
    with pytest.raises(ValueError, match="high below"): validate_ohlcv_v53(bad)
    dup = pd.concat([x, x.iloc[[0]]], ignore_index=True).sort_values("timestamp").reset_index(drop=True)
    with pytest.raises(ValueError, match="duplicate"): validate_ohlcv_v53(dup)


def test_confirmed_swing_is_stamped_only_after_right_delay():
    s = pd.Series([1.0, 2.0, 5.0, 2.0, 1.0, 1.5])
    evt, level = _confirmed_pivot(s, left=2, right=2, mode="high")
    assert evt.iloc[4] == 1.0 and level.iloc[4] == 5.0
    assert evt.iloc[:4].sum() == 0.0


def test_full_feature_frame_contains_all_major_families():
    feat = build_v53_feature_frame(_sample())
    required = {
        "smc_bos_bull", "smc_choch_bear", "ict_fvg_bull", "ict_sweep_bull",
        "ict_bull_ob_candidate", "ict_bull_breaker_retest", "ict_inducement_long_proxy",
        "ict_killzone_ny_am", "brooks_always_in", "brooks_h2_long_proxy",
        "brooks_failed_bull_breakout", "brooks_bull_signal_quality", "ichi_tenkan",
        "ichi_cloud_width_pct", "ichi_chikou_context_pct", "micro_trade_imbalance",
    }
    assert required <= set(feat.columns)
    assert len(feat) == 320


@pytest.mark.parametrize("cut", [80, 120, 200, 300])
def test_prefix_invariance_proves_no_future_lookahead(cut):
    raw = _sample(340)
    full = build_v53_feature_frame(raw)
    truncated = build_v53_feature_frame(raw.iloc[: cut + 1].copy())
    common = [c for c in truncated.columns if c in full.columns and c != "timestamp" and pd.api.types.is_numeric_dtype(truncated[c])]
    np.testing.assert_allclose(full.loc[cut, common].astype(float).to_numpy(), truncated.loc[cut, common].astype(float).to_numpy(), rtol=0, atol=1e-12, equal_nan=True)


def test_future_price_perturbation_cannot_change_past_features():
    raw = _sample(260); cut = 170
    baseline = build_v53_feature_frame(raw)
    altered = raw.copy(); altered.loc[cut + 1 :, ["open", "high", "low", "close"]] *= 3.0
    perturbed = build_v53_feature_frame(altered)
    numeric = [c for c in baseline.columns if c != "timestamp" and pd.api.types.is_numeric_dtype(baseline[c])]
    np.testing.assert_allclose(baseline.loc[:cut, numeric].astype(float).to_numpy(), perturbed.loc[:cut, numeric].astype(float).to_numpy(), rtol=0, atol=1e-12, equal_nan=True)


def test_ichimoku_chikou_context_is_lagged_not_future_shifted():
    raw = _sample(80); feat = build_v53_feature_frame(raw); i = 40
    expected = (raw.loc[i, "close"] - raw.loc[i - 26, "close"]) / raw.loc[i, "close"]
    assert feat.loc[i, "ichi_chikou_context_pct"] == pytest.approx(expected)
    changed = raw.copy(); changed.loc[i + 1 :, ["open", "high", "low", "close"]] *= 9.0
    feat2 = build_v53_feature_frame(changed)
    assert feat2.loc[i, "ichi_chikou_context_pct"] == pytest.approx(expected)


def test_fvg_definition_is_three_candle_and_causal():
    raw = _sample(70); t = 60; base = raw.loc[t - 2, "high"]
    raw.loc[t, "open"] = base * 1.03; raw.loc[t, "low"] = base * 1.02
    raw.loc[t, "close"] = base * 1.04; raw.loc[t, "high"] = base * 1.05
    feat = build_v53_feature_frame(raw)
    assert feat.loc[t, "ict_fvg_bull"] == 1.0
    assert feat.loc[t, "ict_fvg_bull_size_pct"] > 0


def test_optional_microstructure_is_validated_and_not_imputed():
    raw = _sample(100); raw["trade_imbalance"] = np.nan; raw["depth_imbalance"] = np.nan
    raw.loc[70:, "trade_imbalance"] = 0.4; raw.loc[70:, "depth_imbalance"] = 0.2
    feat = build_v53_feature_frame(raw)
    assert np.isnan(feat.loc[30, "smc_order_flow_confirmation"])
    bad = raw.copy(); bad.loc[90, "trade_imbalance"] = 1.5
    with pytest.raises(ValueError, match=r"outside \[-1, 1\]"): build_v53_feature_frame(bad)


def test_killzones_are_dst_aware_new_york_local():
    raw = _sample(3)
    raw["timestamp"] = pd.to_datetime(["2026-01-01T12:30:00Z", "2026-07-01T11:30:00Z", "2026-07-01T16:30:00Z"], utc=True)
    feat = build_v53_feature_frame(raw)
    assert feat.loc[0, "ict_killzone_ny_am"] == 1.0
    assert feat.loc[1, "ict_killzone_ny_am"] == 1.0
    assert feat.loc[2, "ict_killzone_ny_am"] == 0.0


def test_asof_join_uses_available_at_not_bar_timestamp():
    low = pd.DataFrame({"timestamp": pd.to_datetime(["2026-01-01T10:30:00Z", "2026-01-01T11:00:00Z", "2026-01-01T11:30:00Z"], utc=True), "close": [1.0, 2.0, 3.0]})
    high = pd.DataFrame({"timestamp": pd.to_datetime(["2026-01-01T10:00:00Z"], utc=True), "available_at": pd.to_datetime(["2026-01-01T11:00:00Z"], utc=True), "state": [7.0]})
    joined = asof_join_available_features_v53(low, high)
    assert np.isnan(joined.loc[0, "htf_state"])
    assert joined.loc[1, "htf_state"] == 7.0 and joined.loc[2, "htf_state"] == 7.0


def test_feature_config_rejects_invalid_thresholds():
    with pytest.raises(ValueError):
        build_v53_feature_frame(_sample(60), V53FeatureConfig(brooks_trend_slope_atr=0.01, brooks_range_slope_atr=0.02))


def test_every_ontology_output_is_materialized_by_feature_engine():
    feat = build_v53_feature_frame(_sample())
    missing = set(output_to_concept()) - set(feat.columns)
    assert missing == set()
