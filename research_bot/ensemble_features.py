"""Closed-bar features for an independent, offline ensemble-RL challenger.

SMC and Brooks features are explicit research proxies, not implementations of
every discretionary rule. ``signed_volume_proxy`` uses candle direction and
OHLCV volume; it is neither order-book OFI nor observed taker-side volume.
"""

from __future__ import annotations

from numbers import Number
import numpy as np
import pandas as pd


FEATURE_COLUMNS = [
    "tenkan_distance", "kijun_distance", "senkou_a_distance", "senkou_b_distance",
    "above_cloud", "below_cloud", "tk_cross",
    "bos_bull", "bos_bear", "choch_bull", "choch_bear",
    "sweep_bull", "sweep_bear", "fvg_bull", "fvg_bear",
    "ema20_distance", "always_in_long", "bull_signal_bar", "bear_signal_bar",
    "signed_volume_proxy", "rsi", "atr_pct",
]


def validate_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Validate one chronological, regularly sampled OHLCV series without repair.

    Timestamps denote a consistent bar timestamp supplied by the caller. Only
    closed bars may be passed to feature extraction. This function cannot infer
    whether a most recent exchange bar has closed. Index labels are preserved.
    """

    if not isinstance(df, pd.DataFrame):
        raise TypeError("OHLCV input must be a pandas DataFrame")
    if df.columns.duplicated().any():
        raise ValueError("duplicate column names are forbidden")
    required = ["timestamp", "open", "high", "low", "close", "volume"]
    missing = set(required).difference(df.columns)
    if missing:
        raise ValueError(f"missing OHLCV columns: {sorted(missing)}")
    if df.empty:
        raise ValueError("OHLCV input must not be empty")
    x = df.copy()
    if pd.api.types.is_numeric_dtype(x["timestamp"]) or x["timestamp"].map(lambda value: isinstance(value, Number)).any():
        raise ValueError("timestamp must be datetime/ISO 8601; ambiguous numeric epoch units are forbidden")
    try:
        x["timestamp"] = pd.to_datetime(x["timestamp"], utc=True, errors="raise", format="mixed")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("invalid OHLCV timestamps") from exc
    if x["timestamp"].isna().any():
        raise ValueError("invalid OHLCV timestamps: NaT is forbidden")
    if x["timestamp"].duplicated().any():
        raise ValueError("duplicate OHLCV timestamps are forbidden")
    if not x["timestamp"].is_monotonic_increasing:
        raise ValueError("OHLCV timestamps must already be sorted chronologically")
    intervals = x["timestamp"].diff().iloc[1:]
    if len(intervals) and (intervals <= pd.Timedelta(0)).any():
        raise ValueError("OHLCV cadence must be positive")
    if len(intervals) and not intervals.eq(intervals.iloc[0]).all():
        raise ValueError("OHLCV cadence must be regular; gaps are forbidden")

    for column in ("symbol", "market", "asset", "exchange", "venue"):
        if column in x and x[column].nunique(dropna=False) != 1:
            raise ValueError(f"multiple series in {column} are forbidden")
    numeric = ["open", "high", "low", "close", "volume"]
    for column in numeric:
        try:
            converted = pd.to_numeric(x[column], errors="raise")
            if np.iscomplexobj(converted.to_numpy()):
                raise ValueError("complex values are forbidden")
            x[column] = converted.astype(float)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"OHLCV {column} must be numeric") from exc
    if not np.isfinite(x[numeric].to_numpy()).all():
        raise ValueError("OHLCV values must be finite")
    if (x[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("OHLC prices must be positive")
    if (x["volume"] < 0).any():
        raise ValueError("OHLCV volume must be non-negative")
    if ((x["high"] < x[["open", "low", "close"]].max(axis=1)).any()
            or (x["low"] > x[["open", "high", "close"]].min(axis=1)).any()):
        raise ValueError("invalid OHLC bounds")
    return x


def _flag(condition: pd.Series, available: pd.Series) -> pd.Series:
    return condition.astype(float).where(available)


def extract_all_features(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Return causal, scale-independent features and their ordered column names.

    A row uses its closed candle and earlier candles only. The plotted Ichimoku
    spans are shifted forward 26 bars, so the first completely warmed-up row is
    position 77 (78 input bars). Warm-up NaNs remain explicit. No feature uses
    future-confirmed library output, negative shifts, or backward filling.
    """

    x = validate_ohlcv(df)
    close, high, low = x["close"], x["high"], x["low"]
    tenkan = high.rolling(9).max() / 2 + low.rolling(9).min() / 2
    kijun = high.rolling(26).max() / 2 + low.rolling(26).min() / 2
    senkou_a = (tenkan / 2 + kijun / 2).shift(26)
    senkou_b = (high.rolling(52).max() / 2 + low.rolling(52).min() / 2).shift(26)
    for name, line in (("tenkan", tenkan), ("kijun", kijun), ("senkou_a", senkou_a), ("senkou_b", senkou_b)):
        x[f"{name}_distance"] = (close - line) / close
    cloud_available = senkou_a.notna() & senkou_b.notna()
    cloud_top = pd.concat([senkou_a, senkou_b], axis=1).max(axis=1)
    cloud_bottom = pd.concat([senkou_a, senkou_b], axis=1).min(axis=1)
    x["above_cloud"] = _flag(close > cloud_top, cloud_available)
    x["below_cloud"] = _flag(close < cloud_bottom, cloud_available)
    tk_available = tenkan.notna() & kijun.notna() & tenkan.shift(1).notna() & kijun.shift(1).notna()
    x["tk_cross"] = _flag((tenkan > kijun) & (tenkan.shift(1) <= kijun.shift(1)), tk_available)

    # A pivot at t-1 becomes known only when bar t closes, following v19's
    # confirmation-time convention. Events are recorded at t, never at t-1.
    pivot_high = high.shift(1).where((high.shift(1) > high.shift(2)) & (high.shift(1) >= high))
    pivot_low = low.shift(1).where((low.shift(1) < low.shift(2)) & (low.shift(1) <= low))
    swing_high, swing_low = pivot_high.ffill(), pivot_low.ffill()
    structure_available = high.shift(2).notna()
    bos_bull = (close > swing_high) & (close.shift(1) <= swing_high)
    bos_bear = (close < swing_low) & (close.shift(1) >= swing_low)
    prior_structure = (bos_bull.astype(float) - bos_bear.astype(float)).replace(0, np.nan).ffill().shift(1)
    x["bos_bull"] = _flag(bos_bull, structure_available)
    x["bos_bear"] = _flag(bos_bear, structure_available)
    x["choch_bull"] = _flag(bos_bull & prior_structure.eq(-1), structure_available)
    x["choch_bear"] = _flag(bos_bear & prior_structure.eq(1), structure_available)
    x["sweep_bull"] = _flag((low < swing_low) & (close > swing_low), structure_available)
    x["sweep_bear"] = _flag((high > swing_high) & (close < swing_high), structure_available)
    x["fvg_bull"] = _flag(low > high.shift(2), structure_available)
    x["fvg_bear"] = _flag(high < low.shift(2), structure_available)

    # Brooks-inspired closed-bar breakout proxies; these are not H2/L2 setups.
    ema20 = close.ewm(span=20, adjust=False, min_periods=20).mean()
    volume_reference = x["volume"].shift(1).rolling(20).mean()
    x["ema20_distance"] = (close - ema20) / close
    x["always_in_long"] = _flag(close > ema20, ema20.notna())
    signal_available = high.shift(1).notna() & volume_reference.notna()
    x["bull_signal_bar"] = _flag(
        (close > x["open"]) & (close > high.shift(1)) & (x["volume"] > volume_reference), signal_available,
    )
    x["bear_signal_bar"] = _flag(
        (close < x["open"]) & (close < low.shift(1)) & (x["volume"] > volume_reference), signal_available,
    )
    signed_volume = np.sign(close - x["open"]) * x["volume"]
    total_volume = x["volume"].rolling(10).sum()
    x["signed_volume_proxy"] = signed_volume.rolling(10).sum().div(total_volume.replace(0, np.nan)).mask(total_volume.eq(0), 0.0)

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    x["rsi"] = gain.div(gain + loss).mask(gain.eq(0) & loss.eq(0), 0.5)
    previous_close = close.shift(1)
    true_range = pd.concat([high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1).max(axis=1)
    x["atr_pct"] = true_range.rolling(14).mean() / close

    if np.isinf(x[FEATURE_COLUMNS].to_numpy()).any():
        raise ValueError("OHLCV magnitudes generated non-finite feature values")
    return x, FEATURE_COLUMNS.copy()
