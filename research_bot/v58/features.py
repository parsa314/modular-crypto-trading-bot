from __future__ import annotations

import numpy as np
import pandas as pd


_REQUIRED = {"timestamp", "open", "high", "low", "close", "volume"}


def _bars_since(flag: pd.Series) -> pd.Series:
    out = np.full(len(flag), np.nan)
    last: int | None = None
    values = flag.fillna(False).to_numpy(dtype=bool)
    for i, hit in enumerate(values):
        if hit:
            last = i
            out[i] = 0.0
        elif last is not None:
            out[i] = float(i - last)
    return pd.Series(out, index=flag.index, dtype=float)


def _rolling_slope(series: pd.Series, window: int) -> pd.Series:
    x = np.arange(window, dtype=float)
    x -= x.mean()
    denom = float(np.sum(x * x))

    def slope(values: np.ndarray) -> float:
        if len(values) != window or np.isnan(values).any():
            return np.nan
        y = values.astype(float)
        y -= y.mean()
        return float(np.sum(x * y) / denom)

    return series.rolling(window, min_periods=window).apply(slope, raw=True)


def _micro_channel_length(high: pd.Series, low: pd.Series) -> pd.Series:
    out = np.zeros(len(high), dtype=float)
    up = 0
    down = 0
    h = high.to_numpy(float)
    l = low.to_numpy(float)
    for i in range(1, len(h)):
        up = up + 1 if l[i] > l[i - 1] else 0
        down = down + 1 if h[i] < h[i - 1] else 0
        out[i] = float(up if up >= down else -down)
    return pd.Series(out, index=high.index)


def add_v58_continuous_features(frame: pd.DataFrame) -> pd.DataFrame:
    missing = _REQUIRED - set(frame.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")
    x = frame.copy()
    x["timestamp"] = pd.to_datetime(x["timestamp"], utc=True, errors="raise")
    x = x.sort_values("timestamp", kind="mergesort").reset_index(drop=True)
    if x["timestamp"].duplicated().any():
        raise ValueError("duplicate timestamps forbidden")

    o = x["open"].astype(float)
    h = x["high"].astype(float)
    l = x["low"].astype(float)
    c = x["close"].astype(float)
    v = x["volume"].astype(float)
    if ((o <= 0) | (h <= 0) | (l <= 0) | (c <= 0)).any():
        raise ValueError("OHLC prices must be positive")
    if ((h < l) | (h < pd.concat([o, c], axis=1).max(axis=1)) | (l > pd.concat([o, c], axis=1).min(axis=1))).any():
        raise ValueError("invalid OHLC geometry")

    prev_close = c.shift(1)
    tr = pd.concat([h - l, (h - prev_close).abs(), (l - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / 14.0, adjust=False, min_periods=14).mean()
    atr_safe = atr.replace(0.0, np.nan)

    # Market/base family.
    x["ATR_percent"] = atr_safe / c
    logret = np.log(c / c.shift(1))
    x["realized_volatility"] = logret.rolling(24, min_periods=12).std(ddof=1)
    vol_mean = v.shift(1).rolling(48, min_periods=16).mean()
    vol_std = v.shift(1).rolling(48, min_periods=16).std(ddof=1).replace(0.0, np.nan)
    x["volume_zscore"] = (v - vol_mean) / vol_std
    for n in (1, 3, 6, 12):
        x[f"returns_{n}"] = c / c.shift(n) - 1.0
    ema = c.ewm(span=20, adjust=False, min_periods=20).mean()
    x["EMA_distance"] = (c - ema) / atr_safe
    x["trend_slope"] = _rolling_slope(c, 20) / atr_safe
    prior_range = h.shift(1).rolling(20, min_periods=10).max() - l.shift(1).rolling(20, min_periods=10).min()
    x["range_compression"] = prior_range / atr_safe
    x["liquidity_proxy"] = v / v.shift(1).rolling(48, min_periods=16).median().replace(0.0, np.nan)

    # Ichimoku family. No Chikou input and no backward future shift.
    tenkan = (h.rolling(9, min_periods=9).max() + l.rolling(9, min_periods=9).min()) / 2.0
    kijun = (h.rolling(26, min_periods=26).max() + l.rolling(26, min_periods=26).min()) / 2.0
    span_a_now = (tenkan + kijun) / 2.0
    span_b_now = (h.rolling(52, min_periods=52).max() + l.rolling(52, min_periods=52).min()) / 2.0
    cloud_top = pd.concat([span_a_now, span_b_now], axis=1).max(axis=1)
    cloud_bottom = pd.concat([span_a_now, span_b_now], axis=1).min(axis=1)
    x["tenkan_kijun_distance_atr"] = (tenkan - kijun) / atr_safe
    x["price_kijun_distance_atr"] = (c - kijun) / atr_safe
    cloud_distance = pd.Series(0.0, index=x.index)
    cloud_distance = cloud_distance.mask(c > cloud_top, (c - cloud_top) / atr_safe)
    cloud_distance = cloud_distance.mask(c < cloud_bottom, (c - cloud_bottom) / atr_safe)
    x["price_cloud_distance_atr"] = cloud_distance
    x["cloud_width_atr"] = (cloud_top - cloud_bottom) / atr_safe
    x["kijun_slope"] = _rolling_slope(kijun, 8) / atr_safe
    x["tenkan_slope"] = _rolling_slope(tenkan, 8) / atr_safe
    tk_state = tenkan > kijun
    tk_cross = tk_state.ne(tk_state.shift(1)) & tenkan.notna() & kijun.notna()
    x["bars_since_tk_cross"] = _bars_since(tk_cross)
    above = c > cloud_top
    below = c < cloud_bottom
    cloud_break = (above & ~above.shift(1).fillna(False)) | (below & ~below.shift(1).fillna(False))
    x["bars_since_cloud_break"] = _bars_since(cloud_break)
    width = (cloud_top - cloud_bottom).replace(0.0, np.nan)
    x["price_vs_cloud_percentile"] = (c - cloud_bottom) / width

    # ICT/SMC continuous causal proxies based only on prior reference levels.
    prior_high = h.shift(1).rolling(20, min_periods=10).max()
    prior_low = l.shift(1).rolling(20, min_periods=10).min()
    bull_sweep = (l < prior_low) & (c > prior_low)
    bear_sweep = (h > prior_high) & (c < prior_high)
    sweep_depth = pd.Series(0.0, index=x.index)
    sweep_depth = sweep_depth.mask(bull_sweep, (prior_low - l) / atr_safe)
    sweep_depth = sweep_depth.mask(bear_sweep, -((h - prior_high) / atr_safe))
    x["sweep_depth_atr"] = sweep_depth
    x["liquidity_distance_atr"] = pd.concat([(prior_high - c).abs(), (c - prior_low).abs()], axis=1).min(axis=1) / atr_safe
    bar_range = (h - l).replace(0.0, np.nan)
    x["displacement_body_ratio"] = (c - o).abs() / bar_range
    x["displacement_range_atr"] = (h - l) / atr_safe
    x["relative_volume"] = v / v.shift(1).rolling(24, min_periods=8).median().replace(0.0, np.nan)
    bull_fvg = l > h.shift(2)
    bear_fvg = h < l.shift(2)
    fvg_width = pd.Series(0.0, index=x.index)
    fvg_width = fvg_width.mask(bull_fvg, (l - h.shift(2)) / atr_safe)
    fvg_width = fvg_width.mask(bear_fvg, -((l.shift(2) - h) / atr_safe))
    x["fvg_width_atr"] = fvg_width
    x["fvg_age"] = _bars_since(bull_fvg | bear_fvg)
    bull_mss = c > prior_high
    bear_mss = c < prior_low
    mss_dist = pd.Series(0.0, index=x.index)
    mss_dist = mss_dist.mask(bull_mss, (c - prior_high) / atr_safe)
    mss_dist = mss_dist.mask(bear_mss, -((prior_low - c) / atr_safe))
    x["mss_break_distance_atr"] = mss_dist
    x["bars_since_sweep"] = _bars_since(bull_sweep | bear_sweep)
    x["bars_since_mss"] = _bars_since(bull_mss | bear_mss)
    x["close_location_value"] = ((c - l) - (h - c)) / bar_range
    x["structure_strength"] = mss_dist.clip(-5.0, 5.0) + 0.5 * sweep_depth.clip(-5.0, 5.0)

    # Al Brooks-inspired continuous proxies.
    ema_fast = c.ewm(span=10, adjust=False, min_periods=10).mean()
    ema_slow = c.ewm(span=20, adjust=False, min_periods=20).mean()
    x["trend_strength"] = (ema_fast - ema_slow) / atr_safe
    prev_overlap = (pd.concat([h, h.shift(1)], axis=1).min(axis=1) - pd.concat([l, l.shift(1)], axis=1).max(axis=1)).clip(lower=0.0)
    x["bar_overlap"] = prev_overlap / bar_range
    breakout = pd.Series(0.0, index=x.index)
    breakout = breakout.mask(c > prior_high, (c - prior_high) / atr_safe)
    breakout = breakout.mask(c < prior_low, -((prior_low - c) / atr_safe))
    x["breakout_strength"] = breakout
    body_direction = np.sign(c - o)
    body_quality = (c - o).abs() / bar_range
    x["follow_through_strength"] = (body_direction * body_quality).rolling(3, min_periods=2).mean()
    prev_break_up = c.shift(1) > prior_high.shift(1)
    prev_break_dn = c.shift(1) < prior_low.shift(1)
    failed = pd.Series(0.0, index=x.index)
    failed = failed.mask(prev_break_up & (c < prior_high), -((prior_high - c) / atr_safe))
    failed = failed.mask(prev_break_dn & (c > prior_low), (c - prior_low) / atr_safe)
    x["failed_breakout_score"] = failed
    x["signal_bar_quality"] = body_quality * x["close_location_value"].abs()
    x["micro_channel_length"] = _micro_channel_length(h, l)
    rolling_high = h.shift(1).rolling(20, min_periods=10).max()
    rolling_low = l.shift(1).rolling(20, min_periods=10).min()
    trend_sign = np.sign(x["trend_strength"])
    pullback = pd.Series(np.nan, index=x.index)
    pullback = pullback.mask(trend_sign >= 0, (rolling_high - c) / atr_safe)
    pullback = pullback.mask(trend_sign < 0, (c - rolling_low) / atr_safe)
    x["pullback_depth_atr"] = pullback
    denom = (rolling_high - rolling_low).replace(0.0, np.nan)
    x["trading_range_position"] = (c - rolling_low) / denom
    x["bars_since_breakout"] = _bars_since((breakout != 0.0) & breakout.notna())

    return x.replace([np.inf, -np.inf], np.nan)
