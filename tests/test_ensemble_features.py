import numpy as np
import pandas as pd
import pytest

from research_bot.ensemble_features import FEATURE_COLUMNS, extract_all_features, validate_ohlcv


def bars(n=140, seed=314):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.006, n)))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC"),
        "open": open_, "high": np.maximum(open_, close) + 0.5,
        "low": np.minimum(open_, close) - 0.5, "close": close,
        "volume": rng.uniform(0, 1000, n),
    })


@pytest.mark.parametrize("dtype", ["int64", "object"])
def test_numeric_timestamp_requires_explicit_epoch_conversion(dtype):
    source = bars()
    source["timestamp"] = pd.Series(np.arange(len(source)) * 14_400_000 + 1_700_000_000_000, dtype=dtype)
    with pytest.raises(ValueError, match="ambiguous numeric epoch"):
        validate_ohlcv(source)


def test_features_are_prefix_invariant_and_future_perturbations_do_not_rewrite_history():
    source = bars()
    full, columns = extract_all_features(source)
    for cut in (35, 78, 109):
        prefix, _ = extract_all_features(source.iloc[:cut])
        pd.testing.assert_frame_equal(full[columns].iloc[:cut], prefix[columns])
    changed = source.copy()
    changed.loc[109:, ["open", "high", "low", "close"]] *= 4
    changed.loc[109:, "volume"] *= 10
    modified, _ = extract_all_features(changed)
    pd.testing.assert_frame_equal(full[columns].iloc[:109], modified[columns].iloc[:109])


def test_warmup_index_and_column_contract():
    source = bars()
    source.index = pd.Index(np.arange(len(source)) * 7 + 100, name="original_row")
    saved = source.copy(deep=True)
    featured, columns = extract_all_features(source)
    assert columns == FEATURE_COLUMNS
    assert featured.index.equals(source.index)
    assert featured[columns].iloc[:77].isna().any(axis=1).all()
    assert np.isfinite(featured[columns].iloc[77:].to_numpy()).all()
    pd.testing.assert_frame_equal(source, saved)
    columns.append("caller_added")
    assert "caller_added" not in FEATURE_COLUMNS


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_non_finite_input_fails(bad):
    source = bars()
    source.loc[10, "volume"] = bad
    with pytest.raises(ValueError, match="finite"):
        extract_all_features(source)


@pytest.mark.parametrize("column,value,message", [
    ("open", 0, "positive"),
    ("close", -1, "positive"),
    ("volume", -1, "non-negative"),
    ("high", 1, "bounds"),
    ("low", 1000, "bounds"),
    ("open", "bad", "numeric"),
    ("open", 100 + 2j, "numeric"),
])
def test_bad_prices_and_volumes_fail(column, value, message):
    source = bars().astype({column: object})
    source.loc[10, column] = value
    with pytest.raises(ValueError, match=message):
        validate_ohlcv(source)


def test_bad_time_and_series_inputs_are_rejected_without_repair():
    source = bars()
    duplicate = source.copy()
    duplicate.loc[10, "timestamp"] = duplicate.loc[9, "timestamp"]
    with pytest.raises(ValueError, match="duplicate"):
        validate_ohlcv(duplicate)
    with pytest.raises(ValueError, match="sorted"):
        validate_ohlcv(source.iloc[::-1])
    with pytest.raises(ValueError, match="gaps"):
        validate_ohlcv(source.drop(index=10))
    missing = source.copy()
    missing.loc[10, "timestamp"] = pd.NaT
    with pytest.raises(ValueError, match="timestamps"):
        validate_ohlcv(missing)
    malformed = source.copy().astype({"timestamp": object})
    malformed.loc[10, "timestamp"] = "not-a-time"
    with pytest.raises(ValueError, match="timestamps"):
        validate_ohlcv(malformed)
    mixed = source.copy()
    mixed["symbol"] = ["BTC/USDT"] * 70 + ["ETH/USDT"] * 70
    with pytest.raises(ValueError, match="multiple series"):
        validate_ohlcv(mixed)
    with pytest.raises(ValueError, match="missing"):
        validate_ohlcv(source.drop(columns="volume"))
    with pytest.raises(ValueError, match="empty"):
        validate_ohlcv(source.iloc[:0])
    with pytest.raises(ValueError, match="column names"):
        validate_ohlcv(pd.concat([source, source[["close"]]], axis=1))


def test_numeric_csv_and_naive_timestamps_are_normalized_to_utc():
    source = bars()
    source["timestamp"] = source["timestamp"].dt.tz_localize(None).astype(str)
    source["volume"] = source["volume"].astype(str)
    validated = validate_ohlcv(source)
    assert str(validated["timestamp"].dt.tz) == "UTC"
    assert validated["volume"].dtype == float


@pytest.mark.parametrize("direction,expected", [(0, 0.5), (1, 1.0), (-1, 0.0)])
def test_rsi_handles_flat_and_one_sided_prices(direction, expected):
    source = bars()
    close = 100 + direction * np.arange(len(source)) * 0.1
    source[["open", "close"]] = np.column_stack([close, close])
    source["high"], source["low"], source["volume"] = close + 1, close - 1, 0
    featured, columns = extract_all_features(source)
    assert featured["rsi"].iloc[:14].isna().all()
    assert (featured["rsi"].iloc[14:] == expected).all()
    assert (featured["signed_volume_proxy"].iloc[9:] == 0).all()
    assert np.isfinite(featured[columns].iloc[77:].to_numpy()).all()


def test_atr_counts_price_gap_from_previous_close():
    source = bars(100)
    source[["open", "close"]] = 100.0
    source["high"], source["low"] = 101.0, 99.0
    source.loc[15:, ["open", "close"]] = 110.0
    source.loc[15:, "high"], source.loc[15:, "low"] = 111.0, 109.0
    featured, _ = extract_all_features(source)
    # The gap bar contributes 11, rather than its intrabar range of 2.
    assert featured["atr_pct"].iloc[15] == pytest.approx((13 * 2 + 11) / 14 / 110)


def test_fvg_and_confirmed_structure_events_are_stamped_when_known():
    source = bars(5)
    source["open"] = [100, 102, 108, 106, 107]
    source["close"] = [100, 102, 108, 107, 111]
    source["high"] = [101, 104, 110, 108, 112]
    source["low"] = [99, 100, 105, 104, 106]
    featured, _ = extract_all_features(source)
    assert featured["fvg_bull"].iloc[:2].isna().all()
    assert featured["fvg_bull"].iloc[2] == 1
    assert featured["bos_bull"].iloc[2:4].tolist() == [0, 0]
    assert featured["bos_bull"].iloc[4] == 1
