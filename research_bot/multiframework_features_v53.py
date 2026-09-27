from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


REQUIRED_OHLCV = ("timestamp", "open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class V53FeatureConfig:
    swing_left: int = 3
    swing_right: int = 2
    minor_swing_left: int = 1
    minor_swing_right: int = 1
    atr_window: int = 14
    displacement_body_atr: float = 1.20
    displacement_close_location: float = 0.75
    liquidity_sequence_window: int = 3
    brooks_ema_window: int = 20
    brooks_overlap_window: int = 10
    brooks_trend_slope_atr: float = 0.08
    brooks_range_slope_atr: float = 0.04
    brooks_range_overlap: float = 0.45
    micro_double_tolerance_atr: float = 0.15
    breakout_window: int = 20
    ny_timezone: str = "America/New_York"


def _validate_config(cfg: V53FeatureConfig) -> None:
    positive_ints = (
        cfg.swing_left, cfg.swing_right, cfg.minor_swing_left, cfg.minor_swing_right,
        cfg.atr_window, cfg.liquidity_sequence_window, cfg.brooks_ema_window,
        cfg.brooks_overlap_window, cfg.breakout_window,
    )
    if any(v <= 0 for v in positive_ints):
        raise ValueError("all V53 integer windows must be positive")
    if cfg.displacement_body_atr <= 0:
        raise ValueError("displacement_body_atr must be positive")
    if not 0.5 <= cfg.displacement_close_location < 1.0:
        raise ValueError("displacement_close_location must be in [0.5, 1)")
    if cfg.brooks_trend_slope_atr <= cfg.brooks_range_slope_atr:
        raise ValueError("trend slope threshold must exceed range threshold")
    if not 0 <= cfg.brooks_range_overlap <= 1:
        raise ValueError("brooks_range_overlap must be in [0, 1]")


def validate_ohlcv_v53(df: pd.DataFrame) -> pd.DataFrame:
    """Return a strictly ordered, finite, structurally valid OHLCV frame."""
    missing = set(REQUIRED_OHLCV) - set(df.columns)
    if missing:
        raise ValueError(f"missing OHLCV columns: {sorted(missing)}")
    x = df.copy()
    x["timestamp"] = pd.to_datetime(x["timestamp"], utc=True, errors="raise")
    if x["timestamp"].duplicated().any():
        raise ValueError("duplicate OHLCV timestamps")
    if not x["timestamp"].is_monotonic_increasing:
        raise ValueError("OHLCV timestamps must be strictly increasing")
    for col in ("open", "high", "low", "close", "volume"):
        x[col] = pd.to_numeric(x[col], errors="coerce")
        if not np.isfinite(x[col].to_numpy(dtype=float)).all():
            raise ValueError(f"non-finite OHLCV values in {col}")
    if (x["volume"] < 0).any():
        raise ValueError("negative volume")
    upper = x[["open", "close", "low"]].max(axis=1)
    lower = x[["open", "close", "high"]].min(axis=1)
    if (x["high"] < upper).any():
        raise ValueError("high below candle body/low")
    if (x["low"] > lower).any():
        raise ValueError("low above candle body/high")
    if (x[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("non-positive price")
    return x.reset_index(drop=True)


def _true_range(x: pd.DataFrame) -> pd.Series:
    prev_close = x["close"].shift(1)
    return pd.concat([
        x["high"] - x["low"],
        (x["high"] - prev_close).abs(),
        (x["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)


def _confirmed_pivot(series: pd.Series, *, left: int, right: int, mode: str) -> tuple[pd.Series, pd.Series]:
    """Causally confirm a pivot after ``right`` bars and stamp at confirmation time."""
    window = left + right + 1
    candidate = series.shift(right)
    if mode == "high":
        extreme = series.rolling(window, min_periods=window).max()
        event = candidate.eq(extreme)
    elif mode == "low":
        extreme = series.rolling(window, min_periods=window).min()
        event = candidate.eq(extreme)
    else:
        raise ValueError("mode must be high or low")
    event = event.fillna(False)
    level = candidate.where(event)
    return event.astype(float), level


def add_causal_structure_v53(df: pd.DataFrame, config: V53FeatureConfig | None = None) -> pd.DataFrame:
    cfg = config or V53FeatureConfig()
    _validate_config(cfg)
    x = validate_ohlcv_v53(df)

    sh_evt, sh_level = _confirmed_pivot(x["high"], left=cfg.swing_left, right=cfg.swing_right, mode="high")
    sl_evt, sl_level = _confirmed_pivot(x["low"], left=cfg.swing_left, right=cfg.swing_right, mode="low")
    minor_sh_evt, minor_sh_level = _confirmed_pivot(x["high"], left=cfg.minor_swing_left, right=cfg.minor_swing_right, mode="high")
    minor_sl_evt, minor_sl_level = _confirmed_pivot(x["low"], left=cfg.minor_swing_left, right=cfg.minor_swing_right, mode="low")

    x["smc_swing_high_confirmed"] = sh_evt
    x["smc_swing_low_confirmed"] = sl_evt
    x["smc_prior_swing_high"] = sh_level.ffill().shift(1)
    x["smc_prior_swing_low"] = sl_level.ffill().shift(1)
    x["_minor_swing_high_confirmed"] = minor_sh_evt
    x["_minor_swing_low_confirmed"] = minor_sl_evt
    x["_minor_swing_high_level"] = minor_sh_level
    x["_minor_swing_low_level"] = minor_sl_level

    prev_close = x["close"].shift(1)
    x["smc_bos_bull"] = (
        x["smc_prior_swing_high"].notna()
        & (x["close"] > x["smc_prior_swing_high"])
        & (prev_close <= x["smc_prior_swing_high"])
    ).astype(float)
    x["smc_bos_bear"] = (
        x["smc_prior_swing_low"].notna()
        & (x["close"] < x["smc_prior_swing_low"])
        & (prev_close >= x["smc_prior_swing_low"])
    ).astype(float)

    state = 0
    states, choch_bull, choch_bear = [], [], []
    for bull, bear in zip(x["smc_bos_bull"], x["smc_bos_bear"]):
        cb = cs = 0.0
        if bull == 1.0:
            cb = float(state < 0)
            state = 1
        elif bear == 1.0:
            cs = float(state > 0)
            state = -1
        choch_bull.append(cb); choch_bear.append(cs); states.append(state)
    x["smc_choch_bull"] = choch_bull
    x["smc_choch_bear"] = choch_bear
    x["smc_structure_state"] = states

    valid_range = x["smc_prior_swing_high"].notna() & x["smc_prior_swing_low"].notna() & (x["smc_prior_swing_high"] > x["smc_prior_swing_low"])
    width = x["smc_prior_swing_high"] - x["smc_prior_swing_low"]
    x["smc_dealing_range_position"] = np.where(valid_range, (x["close"] - x["smc_prior_swing_low"]) / width, np.nan)
    x["smc_premium"] = (valid_range & (x["smc_dealing_range_position"] > 0.5)).astype(float)
    x["smc_discount"] = (valid_range & (x["smc_dealing_range_position"] < 0.5)).astype(float)

    x["ict_inducement_long_proxy"] = (
        (x["smc_structure_state"] == 1) & x["_minor_swing_low_confirmed"].eq(1.0)
        & x["_minor_swing_low_level"].notna() & x["smc_prior_swing_low"].notna()
        & (x["_minor_swing_low_level"] > x["smc_prior_swing_low"])
        & (x["_minor_swing_low_level"] < x["close"])
    ).astype(float)
    x["ict_inducement_short_proxy"] = (
        (x["smc_structure_state"] == -1) & x["_minor_swing_high_confirmed"].eq(1.0)
        & x["_minor_swing_high_level"].notna() & x["smc_prior_swing_high"].notna()
        & (x["_minor_swing_high_level"] < x["smc_prior_swing_high"])
        & (x["_minor_swing_high_level"] > x["close"])
    ).astype(float)
    return x


def add_ict_smc_features_v53(df: pd.DataFrame, config: V53FeatureConfig | None = None) -> pd.DataFrame:
    cfg = config or V53FeatureConfig()
    x = add_causal_structure_v53(df, cfg)
    tr = _true_range(x)
    atr = tr.ewm(alpha=1.0 / cfg.atr_window, adjust=False).mean()
    bar_range = (x["high"] - x["low"]).replace(0, np.nan)
    body = (x["close"] - x["open"]).abs()
    close_loc = (x["close"] - x["low"]) / bar_range
    x["_atr"] = atr
    x["ict_body_atr"] = body / atr.replace(0, np.nan)
    x["ict_displacement_bull"] = ((x["close"] > x["open"]) & (x["ict_body_atr"] >= cfg.displacement_body_atr) & (close_loc >= cfg.displacement_close_location)).astype(float)
    x["ict_displacement_bear"] = ((x["close"] < x["open"]) & (x["ict_body_atr"] >= cfg.displacement_body_atr) & (close_loc <= 1.0 - cfg.displacement_close_location)).astype(float)

    x["ict_fvg_bull"] = (x["low"] > x["high"].shift(2)).astype(float)
    x["ict_fvg_bear"] = (x["high"] < x["low"].shift(2)).astype(float)
    x["ict_fvg_bull_size_pct"] = np.where(x["ict_fvg_bull"].eq(1.0), (x["low"] - x["high"].shift(2)) / x["close"], 0.0)
    x["ict_fvg_bear_size_pct"] = np.where(x["ict_fvg_bear"].eq(1.0), (x["low"].shift(2) - x["high"]) / x["close"], 0.0)

    x["ict_sweep_bear"] = (x["smc_prior_swing_high"].notna() & (x["high"] > x["smc_prior_swing_high"]) & (x["close"] < x["smc_prior_swing_high"])).astype(float)
    x["ict_sweep_bull"] = (x["smc_prior_swing_low"].notna() & (x["low"] < x["smc_prior_swing_low"]) & (x["close"] > x["smc_prior_swing_low"])).astype(float)

    prev_bear = x["close"].shift(1) < x["open"].shift(1)
    prev_bull = x["close"].shift(1) > x["open"].shift(1)
    x["ict_bull_ob_candidate"] = (x["smc_bos_bull"].eq(1.0) & x["ict_displacement_bull"].eq(1.0) & prev_bear).astype(float)
    x["ict_bear_ob_candidate"] = (x["smc_bos_bear"].eq(1.0) & x["ict_displacement_bear"].eq(1.0) & prev_bull).astype(float)
    x["ict_bull_ob_lower"] = x["low"].shift(1).where(x["ict_bull_ob_candidate"].eq(1.0))
    x["ict_bull_ob_upper"] = x["high"].shift(1).where(x["ict_bull_ob_candidate"].eq(1.0))
    x["ict_bear_ob_lower"] = x["low"].shift(1).where(x["ict_bear_ob_candidate"].eq(1.0))
    x["ict_bear_ob_upper"] = x["high"].shift(1).where(x["ict_bear_ob_candidate"].eq(1.0))

    bull_low = x["ict_bull_ob_lower"].ffill().shift(1); bull_high = x["ict_bull_ob_upper"].ffill().shift(1)
    bear_low = x["ict_bear_ob_lower"].ffill().shift(1); bear_high = x["ict_bear_ob_upper"].ffill().shift(1)
    bull_valid = bull_low.notna() & bull_high.notna(); bear_valid = bear_low.notna() & bear_high.notna()
    bull_overlap = bull_valid & (x["low"] <= bull_high) & (x["high"] >= bull_low)
    bear_overlap = bear_valid & (x["low"] <= bear_high) & (x["high"] >= bear_low)
    x["ict_bull_ob_mitigation"] = bull_overlap.astype(float); x["ict_bear_ob_mitigation"] = bear_overlap.astype(float)
    x["ict_bull_ob_rejection"] = (bull_overlap & (x["close"] >= (bull_low + bull_high) / 2.0)).astype(float)
    x["ict_bear_ob_rejection"] = (bear_overlap & (x["close"] <= (bear_low + bear_high) / 2.0)).astype(float)
    bull_invalid = bull_valid & (x["close"] < bull_low); bear_invalid = bear_valid & (x["close"] > bear_high)

    bull_breaker_low = bull_breaker_high = bear_breaker_low = bear_breaker_high = np.nan
    bull_breaker_retest = np.zeros(len(x), dtype=float); bear_breaker_retest = np.zeros(len(x), dtype=float)
    for i in range(len(x)):
        if i > 0:
            if bool(bear_invalid.iloc[i - 1]): bull_breaker_low, bull_breaker_high = float(bear_low.iloc[i - 1]), float(bear_high.iloc[i - 1])
            if bool(bull_invalid.iloc[i - 1]): bear_breaker_low, bear_breaker_high = float(bull_low.iloc[i - 1]), float(bull_high.iloc[i - 1])
        if np.isfinite(bull_breaker_low) and np.isfinite(bull_breaker_high):
            if x["low"].iloc[i] <= bull_breaker_high and x["high"].iloc[i] >= bull_breaker_low and x["close"].iloc[i] > bull_breaker_high: bull_breaker_retest[i] = 1.0
        if np.isfinite(bear_breaker_low) and np.isfinite(bear_breaker_high):
            if x["low"].iloc[i] <= bear_breaker_high and x["high"].iloc[i] >= bear_breaker_low and x["close"].iloc[i] < bear_breaker_low: bear_breaker_retest[i] = 1.0
    x["ict_bull_breaker_retest"] = bull_breaker_retest; x["ict_bear_breaker_retest"] = bear_breaker_retest

    x["ict_liquidity_high_distance_atr"] = (x["smc_prior_swing_high"] - x["close"]) / atr.replace(0, np.nan)
    x["ict_liquidity_low_distance_atr"] = (x["close"] - x["smc_prior_swing_low"]) / atr.replace(0, np.nan)
    recent_bull_sweep = x["ict_sweep_bull"].rolling(cfg.liquidity_sequence_window, min_periods=1).max().shift(1).fillna(0)
    recent_bear_sweep = x["ict_sweep_bear"].rolling(cfg.liquidity_sequence_window, min_periods=1).max().shift(1).fillna(0)
    x["ict_liquidity_engineering_bull_proxy"] = (recent_bull_sweep.eq(1.0) & x["ict_displacement_bull"].eq(1.0) & (x["smc_bos_bull"].eq(1.0) | x["smc_choch_bull"].eq(1.0))).astype(float)
    x["ict_liquidity_engineering_bear_proxy"] = (recent_bear_sweep.eq(1.0) & x["ict_displacement_bear"].eq(1.0) & (x["smc_bos_bear"].eq(1.0) | x["smc_choch_bear"].eq(1.0))).astype(float)

    local = x["timestamp"].dt.tz_convert(ZoneInfo(cfg.ny_timezone)); mins = local.dt.hour * 60 + local.dt.minute
    x["ict_killzone_london"] = mins.between(120, 299).astype(float)
    x["ict_killzone_ny_am"] = mins.between(420, 599).astype(float)
    x["ict_killzone_london_close"] = mins.between(600, 719).astype(float)
    return x


def _consecutive_counts(mask: pd.Series) -> np.ndarray:
    out = np.zeros(len(mask), dtype=int); count = 0
    for i, flag in enumerate(mask.fillna(False).astype(bool)):
        count = count + 1 if flag else 0; out[i] = count
    return out


def add_brooks_features_v53(df: pd.DataFrame, config: V53FeatureConfig | None = None) -> pd.DataFrame:
    cfg = config or V53FeatureConfig(); x = validate_ohlcv_v53(df)
    tr = _true_range(x); atr = tr.ewm(alpha=1.0 / cfg.atr_window, adjust=False).mean()
    rng = (x["high"] - x["low"]).replace(0, np.nan); body_signed = x["close"] - x["open"]
    body_ratio = body_signed.abs() / rng; close_loc = (x["close"] - x["low"]) / rng
    upper_tail = (x["high"] - x[["open", "close"]].max(axis=1)) / rng
    lower_tail = (x[["open", "close"]].min(axis=1) - x["low"]) / rng
    bull = body_signed > 0; bear = body_signed < 0
    x["brooks_doji"] = (body_ratio <= 0.20).astype(float)
    x["brooks_bull_trend_bar"] = (bull & (body_ratio >= 0.60) & (close_loc >= 0.75)).astype(float)
    x["brooks_bear_trend_bar"] = (bear & (body_ratio >= 0.60) & (close_loc <= 0.25)).astype(float)
    x["brooks_inside_bar"] = ((x["high"] <= x["high"].shift(1)) & (x["low"] >= x["low"].shift(1))).astype(float)
    x["brooks_outside_bar"] = ((x["high"] > x["high"].shift(1)) & (x["low"] < x["low"].shift(1))).astype(float)
    x["brooks_bull_bar_count"] = _consecutive_counts(bull); x["brooks_bear_bar_count"] = _consecutive_counts(bear)

    prev_range = (x["high"].shift(1) - x["low"].shift(1)).replace(0, np.nan)
    overlap = (np.minimum(x["high"], x["high"].shift(1)) - np.maximum(x["low"], x["low"].shift(1))).clip(lower=0)
    overlap_ratio = (overlap / np.minimum(rng, prev_range).replace(0, np.nan)).clip(0, 1)
    x["brooks_overlap_mean"] = overlap_ratio.rolling(cfg.brooks_overlap_window, min_periods=cfg.brooks_overlap_window).mean()
    ema = x["close"].ewm(span=cfg.brooks_ema_window, adjust=False).mean(); ema_slope = (ema - ema.shift(5)) / (5.0 * atr.replace(0, np.nan))
    x["brooks_ema_slope_atr"] = ema_slope
    x["brooks_market_trend"] = np.where((ema_slope >= cfg.brooks_trend_slope_atr) & (x["close"] > ema), 1, np.where((ema_slope <= -cfg.brooks_trend_slope_atr) & (x["close"] < ema), -1, 0))
    x["brooks_trading_range"] = ((ema_slope.abs() <= cfg.brooks_range_slope_atr) & (x["brooks_overlap_mean"] >= cfg.brooks_range_overlap)).astype(float)

    prior_high = x["high"].shift(1).rolling(cfg.breakout_window, min_periods=cfg.breakout_window).max(); prior_low = x["low"].shift(1).rolling(cfg.breakout_window, min_periods=cfg.breakout_window).min()
    bull_breakout = x["close"] > prior_high; bear_breakout = x["close"] < prior_low
    always = 0; states = []
    for i in range(len(x)):
        if (x["brooks_bull_trend_bar"].iloc[i] == 1.0 and pd.notna(ema_slope.iloc[i]) and ema_slope.iloc[i] > 0 and x["close"].iloc[i] > ema.iloc[i]) or bool(bull_breakout.iloc[i] if pd.notna(bull_breakout.iloc[i]) else False): always = 1
        elif (x["brooks_bear_trend_bar"].iloc[i] == 1.0 and pd.notna(ema_slope.iloc[i]) and ema_slope.iloc[i] < 0 and x["close"].iloc[i] < ema.iloc[i]) or bool(bear_breakout.iloc[i] if pd.notna(bear_breakout.iloc[i]) else False): always = -1
        states.append(always)
    x["brooks_always_in"] = states
    low_overlap = (1.0 - overlap_ratio.fillna(0.5)).clip(0, 1)
    x["brooks_bull_signal_quality"] = (0.30 * body_ratio.clip(0,1) + 0.25 * close_loc.clip(0,1) + 0.15 * (1.0-upper_tail.fillna(1.0)).clip(0,1) + 0.15 * (x["brooks_always_in"] >= 0).astype(float) + 0.15 * low_overlap) * bull.astype(float)
    x["brooks_bear_signal_quality"] = (0.30 * body_ratio.clip(0,1) + 0.25 * (1.0-close_loc).clip(0,1) + 0.15 * (1.0-lower_tail.fillna(1.0)).clip(0,1) + 0.15 * (x["brooks_always_in"] <= 0).astype(float) + 0.15 * low_overlap) * bear.astype(float)

    h1=np.zeros(len(x)); h2=np.zeros(len(x)); l1=np.zeros(len(x)); l2=np.zeros(len(x)); direction=0; pullback=False; attempts=0
    for i in range(1, len(x)):
        d=int(x["brooks_always_in"].iloc[i])
        if d != direction: direction=d; pullback=False; attempts=0
        if direction == 1:
            if x["low"].iloc[i] < x["low"].iloc[i-1] or x["close"].iloc[i] < ema.iloc[i]: pullback=True
            if pullback and x["high"].iloc[i] > x["high"].iloc[i-1]:
                attempts += 1
                if attempts == 1: h1[i]=1.0
                elif attempts == 2: h2[i]=1.0; pullback=False; attempts=0
        elif direction == -1:
            if x["high"].iloc[i] > x["high"].iloc[i-1] or x["close"].iloc[i] > ema.iloc[i]: pullback=True
            if pullback and x["low"].iloc[i] < x["low"].iloc[i-1]:
                attempts += 1
                if attempts == 1: l1[i]=1.0
                elif attempts == 2: l2[i]=1.0; pullback=False; attempts=0
    x["brooks_h1_long_proxy"]=h1; x["brooks_h2_long_proxy"]=h2; x["brooks_l1_short_proxy"]=l1; x["brooks_l2_short_proxy"]=l2
    pb = bull_breakout.shift(1, fill_value=False).astype(bool); ps = bear_breakout.shift(1, fill_value=False).astype(bool)
    x["brooks_failed_bull_breakout"] = (pb & (x["close"] < prior_high.shift(1))).astype(float)
    x["brooks_failed_bear_breakout"] = (ps & (x["close"] > prior_low.shift(1))).astype(float)
    x["brooks_micro_double_top"] = (((x["high"] - x["high"].shift(2)).abs() <= cfg.micro_double_tolerance_atr * atr) & (x["low"].shift(1) < np.minimum(x["low"], x["low"].shift(2)))).astype(float)
    x["brooks_micro_double_bottom"] = (((x["low"] - x["low"].shift(2)).abs() <= cfg.micro_double_tolerance_atr * atr) & (x["high"].shift(1) > np.maximum(x["high"], x["high"].shift(2)))).astype(float)
    return x


def add_ichimoku_features_v53(df: pd.DataFrame) -> pd.DataFrame:
    """Leakage-safe Ichimoku; plotting displacement is not data alignment."""
    x = validate_ohlcv_v53(df); high=x["high"]; low=x["low"]; close=x["close"]
    tenkan=(high.rolling(9).max()+low.rolling(9).min())/2.0; kijun=(high.rolling(26).max()+low.rolling(26).min())/2.0
    span_a=(tenkan+kijun)/2.0; span_b=(high.rolling(52).max()+low.rolling(52).min())/2.0
    top=pd.concat([span_a,span_b],axis=1).max(axis=1); bottom=pd.concat([span_a,span_b],axis=1).min(axis=1)
    x["ichi_tenkan"]=tenkan; x["ichi_kijun"]=kijun; x["ichi_span_a_now"]=span_a; x["ichi_span_b_now"]=span_b
    x["ichi_tk_distance_pct"]=(tenkan-kijun)/close; x["ichi_price_kijun_pct"]=(close-kijun)/close; x["ichi_price_cloud_top_pct"]=(close-top)/close; x["ichi_cloud_width_pct"]=(top-bottom)/close
    x["ichi_cloud_bullish"]=(span_a>span_b).astype(float); x["ichi_tk_bullish"]=(tenkan>kijun).astype(float); x["ichi_price_above_cloud"]=(close>top).astype(float); x["ichi_price_below_cloud"]=(close<bottom).astype(float)
    x["ichi_tk_cross_up"] = ((tenkan>kijun)&(tenkan.shift(1)<=kijun.shift(1))).astype(float); x["ichi_tk_cross_down"] = ((tenkan<kijun)&(tenkan.shift(1)>=kijun.shift(1))).astype(float)
    orientation=np.sign(span_a-span_b); x["ichi_cloud_twist"]=(pd.Series(orientation,index=x.index).ne(pd.Series(orientation,index=x.index).shift(1))&span_a.notna()&span_b.notna()).astype(float)
    x["ichi_chikou_context_pct"]=(close-close.shift(26))/close
    return x


def add_optional_microstructure_v53(df: pd.DataFrame) -> pd.DataFrame:
    x=df.copy()
    for source,target in (("trade_imbalance","micro_trade_imbalance"),("depth_imbalance","micro_depth_imbalance")):
        if source in x.columns:
            vals=pd.to_numeric(x[source],errors="coerce"); finite=vals.dropna()
            if not np.isfinite(finite.to_numpy(dtype=float)).all(): raise ValueError(f"non-finite {source}")
            if ((finite < -1)|(finite > 1)).any(): raise ValueError(f"{source} outside [-1, 1]")
            x[target]=vals
        else: x[target]=np.nan
    state=x["smc_structure_state"] if "smc_structure_state" in x else pd.Series(0,index=x.index)
    trade=x["micro_trade_imbalance"]; depth=x["micro_depth_imbalance"]; available=trade.notna()&depth.notna()
    same=((state>0)&(trade>0)&(depth>0))|((state<0)&(trade<0)&(depth<0))
    x["smc_order_flow_confirmation"]=np.where(available,same.astype(float),np.nan)
    return x


def build_v53_feature_frame(df: pd.DataFrame, config: V53FeatureConfig | None = None) -> pd.DataFrame:
    cfg=config or V53FeatureConfig(); base=validate_ohlcv_v53(df)
    ict=add_ict_smc_features_v53(base,cfg); brooks=add_brooks_features_v53(base,cfg); ichi=add_ichimoku_features_v53(base)
    result=base.copy()
    for family in (ict,brooks,ichi):
        for col in family.columns:
            if col not in REQUIRED_OHLCV and not col.startswith("_"): result[col]=family[col].to_numpy()
    for col in ("trade_imbalance","depth_imbalance"):
        if col in df.columns: result[col]=df[col].to_numpy()
    return add_optional_microstructure_v53(result).replace([np.inf,-np.inf],np.nan)


def asof_join_available_features_v53(lower_timeframe: pd.DataFrame, higher_timeframe: pd.DataFrame, *, available_at: str = "available_at", prefix: str = "htf_") -> pd.DataFrame:
    """Point-in-time join higher-timeframe features only after availability."""
    if "timestamp" not in lower_timeframe.columns: raise ValueError("lower_timeframe requires timestamp")
    if available_at not in higher_timeframe.columns: raise ValueError(f"higher_timeframe requires {available_at}")
    low=lower_timeframe.copy(); high=higher_timeframe.copy(); low["timestamp"]=pd.to_datetime(low["timestamp"],utc=True,errors="raise"); high[available_at]=pd.to_datetime(high[available_at],utc=True,errors="raise")
    if low["timestamp"].duplicated().any() or high[available_at].duplicated().any(): raise ValueError("duplicate PIT join keys")
    if not low["timestamp"].is_monotonic_increasing: raise ValueError("lower timestamps must be increasing")
    if not high[available_at].is_monotonic_increasing: raise ValueError("higher available_at must be increasing")
    feature_cols=[c for c in high.columns if c not in {"timestamp",available_at}]
    renamed=high[[available_at]+feature_cols].rename(columns={c:f"{prefix}{c}" for c in feature_cols})
    joined=pd.merge_asof(low,renamed,left_on="timestamp",right_on=available_at,direction="backward",allow_exact_matches=True)
    return joined.drop(columns=[available_at]) if available_at in joined.columns else joined
