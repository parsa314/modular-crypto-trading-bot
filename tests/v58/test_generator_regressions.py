"""Synthetic engineering checks; no historical outcome or model fitting."""
from dataclasses import asdict
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from research_bot.v58.contracts import StrategyArm
from research_bot.v58.features import add_v58_continuous_features
from research_bot.v58.generators import generate_candidates
from research_bot.v58.regimes import RegimeConfig, add_causal_regime


def _chain():
    n = 235
    x = pd.DataFrame({"timestamp": pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC"),
                      "open": np.full(n, 99.9), "high": np.full(n, 101.),
                      "low": np.full(n, 99.), "close": np.full(n, 100.), "volume": np.full(n, 1000.)})
    x.loc[210, "high"] = 105.
    x.loc[220, ["open", "high", "low", "close"]] = [100., 100.5, 97., 99.5]
    x.loc[221, ["open", "high", "low", "close"]] = [98., 100.6, 97.9, 100.5]
    x.loc[222, ["open", "high", "low", "close"]] = [100., 110.1, 99.9, 110.]
    return x


def _events(x):
    return generate_candidates(x, venue="coinex", symbol="BTC/USDT", timeframe="4h")


def test_ordered_chain_and_real_four_family_confluence():
    x = _chain()
    events = _events(x)
    c = [e for e in events if e.strategy_arm is StrategyArm.ARM_C]
    e = [e for e in events if e.strategy_arm is StrategyArm.ARM_E]
    assert len(c) == len(e) == 1
    assert c[0].row_index == e[0].row_index == 222
    values = dict(c[0].feature_values)
    assert [values[k] for k in ("sweep_row_index", "displacement_row_index", "mss_row_index")] == [220, 221, 222]
    assert values["chain_reclaim_strength_atr"] > 0
    assert c[0].feature_schema_version == "58.2"
    assert c[0].atr_at_event > 0 and len(c[0].feature_snapshot_id) == 64
    assert [pd.Timestamp(t) for _, t in c[0].state_timestamps] == [x.loc[i, "timestamp"] + timedelta(hours=4) for i in (220, 221, 222)]
    families = {v.strategy_arm for v in events if v.row_index == 222}
    assert set(StrategyArm).issubset(families)


@pytest.mark.parametrize("failure", ["early_mss", "displacement_before_sweep", "bear_displacement", "expired", "no_sweep", "low_volume"])
def test_invalid_chains_do_not_emit_c_or_e(failure):
    x = _chain()
    if failure == "early_mss":
        x.loc[221, ["open", "high", "low", "close"]] = [98., 110.1, 97.9, 110.]
    elif failure == "displacement_before_sweep":
        x.loc[[220, 221], ["open", "high", "low", "close"]] = x.loc[[221, 220], ["open", "high", "low", "close"]].to_numpy()
    elif failure == "bear_displacement":
        x.loc[221, "open"] = 100.55
    elif failure == "expired":
        x.loc[227, ["open", "high", "low", "close"]] = x.loc[222, ["open", "high", "low", "close"]].to_numpy()
        x.loc[222, ["open", "high", "low", "close"]] = [99.9, 101., 99., 100.]
    elif failure == "no_sweep":
        x.loc[220, "low"] = 99.
    else:
        x.loc[221, "volume"] = 1.
    assert not [e for e in _events(x) if e.strategy_arm in (StrategyArm.ARM_C, StrategyArm.ARM_E)]


def test_full_mutated_future_preserves_nonempty_prefix_events_features_and_regimes():
    x = _chain()
    cutoff = 224
    prefix = _events(x.iloc[:cutoff])
    assert prefix and any(e.strategy_arm is StrategyArm.ARM_E for e in prefix)
    changed = x.copy()
    changed.loc[cutoff:, ["open", "high", "low", "close"]] *= 7.
    changed.loc[cutoff:, "volume"] *= 13.
    for full in (x, changed):
        actual = [e for e in _events(full) if e.row_index < cutoff]
        assert [asdict(e) for e in actual] == [asdict(e) for e in prefix]
        pd.testing.assert_frame_equal(add_v58_continuous_features(x.iloc[:cutoff]), add_v58_continuous_features(full).iloc[:cutoff])
        pd.testing.assert_frame_equal(add_causal_regime(x.iloc[:cutoff]), add_causal_regime(full).iloc[:cutoff])


def test_regime_is_available_at_close():
    x = _chain()
    regimes = add_causal_regime(x, timeframe="4h")
    pd.testing.assert_series_equal(regimes.regime_available_at, x.timestamp + timedelta(hours=4), check_names=False)


@pytest.mark.parametrize("column", ["open", "high", "low", "close", "volume"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_ohlcv_is_rejected(column, value):
    x = _chain()
    x.loc[221, column] = value
    with pytest.raises(ValueError, match="finite"):
        _events(x)


@pytest.mark.parametrize("failure", ["gap", "wrong_timeframe", "unsorted", "naive", "nat", "negative_volume"])
def test_invalid_bar_contract_is_rejected(failure):
    x = _chain()
    if failure == "gap":
        x = x.drop(index=10)
    elif failure == "wrong_timeframe":
        x.timestamp = pd.date_range("2025-01-01", periods=len(x), freq="1h", tz="UTC")
    elif failure == "unsorted":
        x = x.iloc[::-1]
    elif failure == "naive":
        x.timestamp = x.timestamp.dt.tz_localize(None)
    elif failure == "nat":
        x.loc[10, "timestamp"] = pd.NaT
    else:
        x.loc[10, "volume"] = -1.
    with pytest.raises(ValueError):
        _events(x)


def test_failed_breakout_must_reclaim_original_breached_level():
    x = _chain().iloc[:222].copy()
    x.loc[220, ["open", "high", "low", "close"]] = [100., 100., 97., 98.]
    x.loc[221, ["open", "high", "low", "close"]] = [98.25, 99., 98., 98.5]
    assert add_v58_continuous_features(x).loc[221, "failed_breakout_score"] == 0.
    x.loc[221, ["high", "close"]] = [100.5, 100.]
    assert add_v58_continuous_features(x).loc[221, "failed_breakout_score"] > 0.


def test_bearish_bar_cannot_emit_bullish_b_or_d():
    x = _chain()
    x.loc[222, ["open", "high", "low", "close"]] = [114., 114.1, 109.9, 110.]
    assert not [e for e in _events(x) if e.row_index == 222 and e.strategy_arm in (StrategyArm.ARM_B, StrategyArm.ARM_D)]


def test_snapshot_ignores_caller_injected_feature_or_outcome_columns():
    x = _chain()
    expected = _events(x)
    x["future_return"] = 999.
    x["trend_strength"] = 999.
    assert _events(x) == expected


@pytest.mark.parametrize("kwargs", [{"trend_threshold_atr": float("nan")}, {"transition_delta": 0.},
                                    {"vol_window": 0}, {"min_periods": 73}, {"min_periods": True}])
def test_regime_configuration_fails_closed(kwargs):
    with pytest.raises(ValueError):
        RegimeConfig(**kwargs)
