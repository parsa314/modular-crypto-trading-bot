import numpy as np
import pandas as pd
import pytest

from research_bot.features import FEATURE_COLUMNS as EXISTING_FEATURE_COLUMNS
from research_bot.features import add_features
from research_bot.research.profit_features import FEATURE_COLUMNS, add_profit_labels, build_profit_features


def bars(n=240):
    rng = np.random.default_rng(42)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.006, n)))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"timestamp": pd.date_range("2022-01-01", periods=n, freq="h", tz="UTC"),
                         "open": open_, "high": np.maximum(open_, close) + 0.1,
                         "low": np.minimum(open_, close) - 0.1, "close": close,
                         "volume": rng.uniform(1, 500, n)})


def test_existing_features_are_reused_without_changing_historical_definitions():
    source = bars()
    existing, new = add_features(source), build_profit_features(source)
    assert FEATURE_COLUMNS == tuple(EXISTING_FEATURE_COLUMNS)
    pd.testing.assert_frame_equal(existing[list(FEATURE_COLUMNS)], new[list(FEATURE_COLUMNS)])
    assert not new["feature_ready"].iloc[:95].any()
    assert new["feature_ready"].iloc[95:].all()
    assert new["feature_available_at"].equals(new["timestamp"] + pd.Timedelta(hours=1))


def test_features_are_prefix_invariant_and_suffix_perturbation_proof():
    source = bars()
    full = build_profit_features(source)
    for cut in (40, 96, 150, 201):
        prefix = build_profit_features(source.iloc[:cut])
        pd.testing.assert_frame_equal(full.iloc[:cut], prefix)
    changed = source.copy()
    changed.loc[150:, ["open", "high", "low", "close"]] *= 3
    changed.loc[150:, "volume"] *= 7
    pd.testing.assert_frame_equal(full.iloc[:150], build_profit_features(changed).iloc[:150])


@pytest.mark.parametrize("direction,expected", [(0, 0.5), (1, 1.0), (-1, 0.0)])
def test_rsi_and_constant_volume_edges_are_explicit(direction, expected):
    source = bars()
    close = 100 + direction * np.arange(len(source)) * 0.1
    source["open"] = source["close"] = close
    source["low"], source["high"], source["volume"] = close - 0.1, close + 0.1, 50.0
    featured = build_profit_features(source)
    assert (featured["rsi_14"].iloc[14:] == expected).all()
    assert (featured["volume_z_24"].iloc[23:] == 0).all()
    assert featured["feature_ready"].iloc[95:].all()


def test_zero_volume_is_not_invented_as_liquidity_and_input_is_unchanged():
    source = bars()
    source.loc[130, "volume"] = 0.0
    saved = source.copy(deep=True)
    featured = build_profit_features(source)
    assert not featured["feature_ready"].iloc[130:154].any()
    pd.testing.assert_frame_equal(source, saved)


def test_hourly_contiguous_candles_required():
    source = bars()
    with pytest.raises(ValueError, match="gaps"):
        build_profit_features(source.drop(index=50))
    source["timestamp"] = pd.date_range("2022-01-01", periods=len(source), freq="4h", tz="UTC")
    with pytest.raises(ValueError, match="1h"):
        build_profit_features(source)


def test_target_uses_next_open_future_close_costs_and_availability_time():
    source = bars()
    labeled = add_profit_labels(build_profit_features(source))
    for t in (0, 95, 120):
        expected = source.loc[t + 24, "close"] * 0.9995 * 0.999 / (source.loc[t + 1, "open"] * 1.0005 * 1.001) - 1
        assert labeled.loc[t, "target_net_return"] == pytest.approx(expected)
        assert labeled.loc[t, "target"] == float(expected > 0)
        assert labeled.loc[t, "label_end"] == source.loc[t + 24, "timestamp"] + pd.Timedelta(hours=1)
    assert labeled["target"].iloc[-24:].isna().all()
    assert labeled["label_end"].iloc[-24:].isna().all()
    assert not set(("target", "target_net_return", "label_end")) & set(FEATURE_COLUMNS)


@pytest.mark.parametrize("keyword,value", [("horizon_bars", 0), ("horizon_bars", True), ("horizon_bars", 1.5),
                                           ("fee_bps", -1), ("slippage_bps", float("nan")), ("fee_bps", True)])
def test_bad_label_parameters_rejected(keyword, value):
    with pytest.raises(ValueError):
        add_profit_labels(build_profit_features(bars()), **{keyword: value})
