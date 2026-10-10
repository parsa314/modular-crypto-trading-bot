from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class StrategySpec:
    """One frozen v0.19 research candidate.

    ``source_basis`` records the educational source family. The implementation is
    an algorithmic crypto research proxy, not a claim that the source itself
    specified these exact crypto rules.
    """

    name: str
    timeframe: str
    family: str
    source_basis: str
    rr: float = 3.0
    stop_atr: float = 1.0
    max_hold_bars: int = 48
    long_short: bool = True


@dataclass(frozen=True)
class TournamentConfig:
    fee_bps: float = 10.0
    slippage_bps: float = 2.0
    risk_per_trade: float = 0.0025
    development_fraction: float = 0.60
    validation_fraction: float = 0.20
    min_pretest_trades: int = 1000
    min_test_trades: int = 200
    min_validation_profit_factor: float = 1.05
    min_test_profit_factor: float = 1.05
    min_positive_asset_fraction: float = 0.60
    max_drawdown: float = 0.05
    bootstrap_samples: int = 500
    bootstrap_block: int = 20
    random_seed: int = 314


_TIMEFRAME_DEFAULTS = {
    "1m": (3.0, 1.1, 60),
    "5m": (3.0, 1.1, 48),
    "15m": (3.0, 1.2, 40),
    "1h": (3.0, 1.3, 36),
    "4h": (3.0, 1.5, 30),
    "1d": (3.0, 1.7, 20),
}


def _spec(name: str, tf: str, family: str, source: str, *, rr: float | None = None) -> StrategySpec:
    d_rr, stop, hold = _TIMEFRAME_DEFAULTS[tf]
    return StrategySpec(name, tf, family, source, rr=d_rr if rr is None else rr, stop_atr=stop, max_hold_bars=hold)


STRATEGY_REGISTRY: tuple[StrategySpec, ...] = (
    _spec("M1_ATM_ORIGIN_RTO", "1m", "atm_origin", "M1Trades: HTF context + Sweep > MSB > Origin RTO"),
    _spec("M1_CONFIRMED_ORDER_BLOCK", "1m", "confirmed_ob", "Order Blocks: HTF OB reaction -> LTF BOS -> LTF OB"),
    _spec("M1_UNICORN", "1m", "unicorn", "Unicorn/TTrades: liquidity sweep + breaker + FVG overlap + DOL"),
    _spec("M1_FVG_RETRACE", "1m", "fvg_retrace", "M1Trades/TTrades: HTF context + LTF FVG retracement"),
    _spec("M1_FRACTAL_CISD", "1m", "fractal_cisd", "TTrades/ICT: higher-timeframe swing + lower-timeframe CISD"),
    _spec("M5_ATM_ORIGIN_RTO", "5m", "atm_origin", "M1Trades ATM setup"),
    _spec("M5_UNICORN", "5m", "unicorn", "Unicorn/TTrades model"),
    _spec("M5_OTE_PD_ARRAY", "5m", "ote", "TTrades/ICT OTE + PD-array confluence"),
    _spec("M5_JUDAS_AMD", "5m", "judas", "ICT/TTrades session Judas Swing / AMD research proxy"),
    _spec("M5_EXTERNAL_INTERNAL_LIQ", "5m", "external_internal", "TTrades external-to-internal liquidity model"),
    _spec("M15_SUPPLY_DEMAND_MTF", "15m", "supply_demand", "Supply & Demand: HTF trend + DBD/DBR/RBR/RBD"),
    _spec("M15_CONFIRMED_ORDER_BLOCK", "15m", "confirmed_ob", "Order Blocks confirmed-entry model"),
    _spec("M15_SILVER_BULLET", "15m", "silver_bullet", "TTrades/ICT Silver Bullet session + liquidity/FVG"),
    _spec("M15_OTE_PD_ARRAY", "15m", "ote", "TTrades/ICT OTE + premium/discount"),
    _spec("M15_ROUND_NUMBER_SWEEP", "15m", "round_number", "Advanced Technical Analysis: 00/20/50/80 adapted to crypto round-number liquidity"),
    _spec("H1_SUPPLY_DEMAND_MTF", "1h", "supply_demand", "Supply & Demand multi-timeframe zones"),
    _spec("H1_OB_BOS_RETEST", "1h", "confirmed_ob", "Order Blocks: BOS -> opposing candle -> mitigation/retest"),
    _spec("H1_FIB_INSTITUTIONAL_RETRACE", "1h", "fib_institutional", "Advanced Technical Analysis: institutional candle + 50/61.8/78.6 retracement"),
    _spec("H1_CORRELATION_DIVERGENCE", "1h", "correlation_divergence", "Advanced Technical Analysis/ICT SMT: correlated-asset divergence"),
    _spec("H1_ICHIMOKU_PULLBACK", "1h", "ichimoku_pullback", "Project Ichimoku + confirmed pullback research family"),
    _spec(
        "H4_V59_CONFLUENCE_DEMO",
        "4h",
        "v59_confluence",
        "Demo confluence: Ichimoku + ICT/SMC + Brooks-style price action + regime/S6",
        rr=2.5,
    ),
    _spec("H4_S6_BREAKOUT", "4h", "s6_breakout", "Project v0.17 S6: 20-bar breakout + positive EMA200 slope"),
    _spec("H4_KUMO_TRIANGLE", "4h", "kumo_triangle", "Project triangle-under-Kumo causal detector"),
    _spec("H4_OB_BOS_RETEST", "4h", "confirmed_ob", "Order Blocks HTF BOS/retest"),
    _spec("H4_SUPPLY_DEMAND", "4h", "supply_demand", "Supply & Demand continuation/reversal zones"),
    _spec("H4_CORRELATION_DIVERGENCE", "4h", "correlation_divergence", "Advanced Technical Analysis/SMT cross-asset divergence"),
    _spec("D1_ICT_SWING_PD", "1d", "ict_swing_pd", "ICT Mentorship: swing bias + PD arrays + premium/discount"),
    _spec("D1_OB_BOS_RETEST", "1d", "confirmed_ob", "Order Blocks daily HTF model"),
    _spec("D1_SUPPLY_DEMAND", "1d", "supply_demand", "Supply & Demand daily continuation/reversal"),
    _spec("D1_FIB_INSTITUTIONAL", "1d", "fib_institutional", "Advanced Technical Analysis: institutional candle + Fibonacci logic"),
    _spec("D1_CORRELATION_DIVERGENCE", "1d", "correlation_divergence", "Advanced Technical Analysis: correlation divergence"),
)


def registry_frame() -> pd.DataFrame:
    return pd.DataFrame([asdict(x) for x in STRATEGY_REGISTRY])


def _validate_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"missing OHLCV columns: {sorted(missing)}")
    x = frame.copy()
    x["timestamp"] = pd.to_datetime(x["timestamp"], utc=True, errors="coerce")
    for c in ("open", "high", "low", "close", "volume"):
        x[c] = pd.to_numeric(x[c], errors="coerce")
    x = x.dropna(subset=list(required)).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    if x.empty:
        return x
    bad = (x[["open", "high", "low", "close"]] <= 0).any(axis=1)
    bad |= x["high"] < x[["open", "close", "low"]].max(axis=1)
    bad |= x["low"] > x[["open", "close", "high"]].min(axis=1)
    if bad.any():
        raise ValueError(f"invalid OHLC rows: {int(bad.sum())}")
    return x


def _atr(x: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = x["close"].shift(1)
    tr = pd.concat([(x["high"] - x["low"]), (x["high"] - prev).abs(), (x["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False).mean()


def build_features(frame: pd.DataFrame, peer: pd.DataFrame | None = None) -> pd.DataFrame:
    """Causal features used by all v0.19 candidates."""

    x = _validate_ohlcv(frame)
    if x.empty:
        return x
    x["atr"] = _atr(x)
    x["atr_pct"] = x["atr"] / x["close"]
    x["ema20"] = x["close"].ewm(span=20, adjust=False).mean()
    x["ema50"] = x["close"].ewm(span=50, adjust=False).mean()
    x["ema200"] = x["close"].ewm(span=200, adjust=False).mean()
    x["ema200_slope"] = x["ema200"].diff(6)
    x["ret1"] = x["close"].pct_change()
    x["ret12"] = x["close"].pct_change(12)

    ph = x["high"].shift(1).where((x["high"].shift(1) > x["high"].shift(2)) & (x["high"].shift(1) >= x["high"]))
    pl = x["low"].shift(1).where((x["low"].shift(1) < x["low"].shift(2)) & (x["low"].shift(1) <= x["low"]))
    x["last_swing_high"] = ph.ffill()
    x["last_swing_low"] = pl.ffill()
    x["bos_up"] = (x["close"] > x["last_swing_high"]) & (x["close"].shift(1) <= x["last_swing_high"].shift(1))
    x["bos_down"] = (x["close"] < x["last_swing_low"]) & (x["close"].shift(1) >= x["last_swing_low"].shift(1))
    x["sweep_down"] = (x["low"] < x["last_swing_low"]) & (x["close"] > x["last_swing_low"])
    x["sweep_up"] = (x["high"] > x["last_swing_high"]) & (x["close"] < x["last_swing_high"])

    x["bull_fvg"] = x["low"] > x["high"].shift(2)
    x["bear_fvg"] = x["high"] < x["low"].shift(2)
    bull_mid_event = ((x["high"].shift(2) + x["low"]) / 2).where(x["bull_fvg"])
    bear_mid_event = ((x["low"].shift(2) + x["high"]) / 2).where(x["bear_fvg"])
    x["bull_fvg_mid"] = bull_mid_event.ffill(limit=20)
    x["bear_fvg_mid"] = bear_mid_event.ffill(limit=20)

    last_down_mid = ((x["open"] + x["low"]) / 2).where(x["close"] < x["open"]).ffill()
    last_up_mid = ((x["open"] + x["high"]) / 2).where(x["close"] > x["open"]).ffill()
    x["bull_ob_mid"] = last_down_mid.where(x["bos_up"]).ffill(limit=30)
    x["bear_ob_mid"] = last_up_mid.where(x["bos_down"]).ffill(limit=30)
    x["bull_origin"] = x["open"].where(x["sweep_down"]).ffill(limit=20)
    x["bear_origin"] = x["open"].where(x["sweep_up"]).ffill(limit=20)
    x["last_down_open"] = x["open"].where(x["close"] < x["open"]).ffill()
    x["last_up_open"] = x["open"].where(x["close"] > x["open"]).ffill()

    tenkan = (x["high"].rolling(9).max() + x["low"].rolling(9).min()) / 2
    kijun = (x["high"].rolling(26).max() + x["low"].rolling(26).min()) / 2
    span_a = (tenkan + kijun) / 2
    span_b = (x["high"].rolling(52).max() + x["low"].rolling(52).min()) / 2
    x["tenkan"] = tenkan
    x["kijun"] = kijun
    x["cloud_top"] = pd.concat([span_a, span_b], axis=1).max(axis=1)
    x["cloud_bottom"] = pd.concat([span_a, span_b], axis=1).min(axis=1)

    hi20 = x["high"].shift(1).rolling(20).max()
    lo20 = x["low"].shift(1).rolling(20).min()
    rng = (hi20 - lo20).replace(0, np.nan)
    x["prior_high20"] = hi20
    x["prior_low20"] = lo20
    x["bull_retracement"] = (hi20 - x["close"]) / rng
    x["bear_retracement"] = (x["close"] - lo20) / rng

    # Causal Al-Brooks-style price-action proxies. These are algorithmic
    # research features inspired by trend/range/breakout/follow-through ideas;
    # they are not a claim to reproduce discretionary Brooks reading verbatim.
    bar_range = (x["high"] - x["low"]).replace(0, np.nan)
    body_abs = (x["close"] - x["open"]).abs()
    close_location = (x["close"] - x["low"]) / bar_range
    overlap = (
        pd.concat([x["high"], x["high"].shift(1)], axis=1).min(axis=1)
        - pd.concat([x["low"], x["low"].shift(1)], axis=1).max(axis=1)
    ).clip(lower=0.0)
    x["brooks_body_ratio"] = body_abs / bar_range
    x["brooks_close_location"] = close_location
    x["brooks_overlap_ratio"] = overlap / bar_range
    x["brooks_trend_strength"] = (x["ema20"] - x["ema50"]) / x["atr"].replace(0, np.nan)
    x["brooks_breakout_up_atr"] = (x["close"] - x["prior_high20"]) / x["atr"].replace(0, np.nan)
    x["brooks_breakout_down_atr"] = (x["prior_low20"] - x["close"]) / x["atr"].replace(0, np.nan)

    bull_signal_bar = (
        (x["close"] > x["open"])
        & (x["brooks_body_ratio"] >= 0.55)
        & (x["brooks_close_location"] >= 0.70)
    )
    bear_signal_bar = (
        (x["close"] < x["open"])
        & (x["brooks_body_ratio"] >= 0.55)
        & (x["brooks_close_location"] <= 0.30)
    )
    x["brooks_bull_signal_bar"] = bull_signal_bar
    x["brooks_bear_signal_bar"] = bear_signal_bar
    x["brooks_bull_follow_through"] = bull_signal_bar & bull_signal_bar.shift(1, fill_value=False)
    x["brooks_bear_follow_through"] = bear_signal_bar & bear_signal_bar.shift(1, fill_value=False)
    x["brooks_microchannel_up"] = (
        (x["low"] > x["low"].shift(1))
        & (x["low"].shift(1) > x["low"].shift(2))
    )
    x["brooks_microchannel_down"] = (
        (x["high"] < x["high"].shift(1))
        & (x["high"].shift(1) < x["high"].shift(2))
    )

    body = (x["close"] - x["open"]).abs()
    x["base_candle"] = body <= 0.35 * x["atr"]
    up_impulse = (x["close"] - x["open"]) >= 0.80 * x["atr"]
    down_impulse = (x["open"] - x["close"]) >= 0.80 * x["atr"]
    x["rbr"] = up_impulse.shift(2, fill_value=False) & x["base_candle"].shift(1, fill_value=False) & up_impulse
    x["dbr"] = down_impulse.shift(2, fill_value=False) & x["base_candle"].shift(1, fill_value=False) & up_impulse
    x["dbd"] = down_impulse.shift(2, fill_value=False) & x["base_candle"].shift(1, fill_value=False) & down_impulse
    x["rbd"] = up_impulse.shift(2, fill_value=False) & x["base_candle"].shift(1, fill_value=False) & down_impulse
    base_mid = (x["open"].shift(1) + x["close"].shift(1)) / 2
    x["demand_mid"] = base_mid.where(x["rbr"] | x["dbr"]).ffill(limit=30).shift(1)
    x["supply_mid"] = base_mid.where(x["dbd"] | x["rbd"]).ffill(limit=30).shift(1)

    ny = x["timestamp"].dt.tz_convert("America/New_York")
    x["ny_hour"] = ny.dt.hour
    x["ny_london_window"] = x["ny_hour"].between(2, 4)
    x["ny_am_window"] = x["ny_hour"].between(7, 10)
    x["silver_bullet_window"] = x["ny_hour"].eq(10)

    magnitude = np.power(10.0, np.floor(np.log10(x["close"].clip(lower=1e-12))))
    step = pd.Series(magnitude / 100.0, index=x.index).clip(lower=x["close"] * 0.0005)
    x["round_step"] = step
    x["round_level"] = (x["close"] / step).round() * step

    if peer is not None and not peer.empty:
        p = _validate_ohlcv(peer)[["timestamp", "close"]].rename(columns={"close": "peer_close"})
        x = pd.merge_asof(x.sort_values("timestamp"), p.sort_values("timestamp"), on="timestamp", direction="backward")
        x["peer_high20"] = x["peer_close"].shift(1).rolling(20).max()
        x["peer_low20"] = x["peer_close"].shift(1).rolling(20).min()
        x["peer_break_high"] = x["peer_close"] > x["peer_high20"]
        x["peer_break_low"] = x["peer_close"] < x["peer_low20"]
    else:
        x["peer_close"] = np.nan
        x["peer_break_high"] = False
        x["peer_break_low"] = False

    return x.replace([np.inf, -np.inf], np.nan)


def _recent(flag: pd.Series, bars: int) -> pd.Series:
    return flag.astype(float).rolling(bars, min_periods=1).max().gt(0)


def _trend_long(x: pd.DataFrame) -> pd.Series:
    return (x["close"] > x["ema200"]) & (x["ema200_slope"] >= 0)


def _trend_short(x: pd.DataFrame) -> pd.Series:
    return (x["close"] < x["ema200"]) & (x["ema200_slope"] <= 0)


def generate_direction(spec: StrategySpec, frame: pd.DataFrame, peer: pd.DataFrame | None = None) -> tuple[pd.Series, pd.DataFrame]:
    """Return {-1,0,+1} closed-bar decisions and the causal feature frame."""

    x = build_features(frame, peer=peer)
    if x.empty:
        return pd.Series(dtype=float), x
    long = pd.Series(False, index=x.index)
    short = pd.Series(False, index=x.index)
    tl, ts = _trend_long(x), _trend_short(x)

    if spec.family == "atm_origin":
        long = tl & _recent(x["sweep_down"], 12) & _recent(x["bos_up"], 8) & (x["low"] <= x["bull_origin"]) & (x["close"] >= x["bull_origin"])
        short = ts & _recent(x["sweep_up"], 12) & _recent(x["bos_down"], 8) & (x["high"] >= x["bear_origin"]) & (x["close"] <= x["bear_origin"])
    elif spec.family == "confirmed_ob":
        long = tl & _recent(x["bos_up"], 16) & (x["low"] <= x["bull_ob_mid"]) & (x["close"] > x["bull_ob_mid"])
        short = ts & _recent(x["bos_down"], 16) & (x["high"] >= x["bear_ob_mid"]) & (x["close"] < x["bear_ob_mid"])
    elif spec.family == "unicorn":
        long = tl & _recent(x["sweep_down"], 12) & _recent(x["bos_up"], 8) & _recent(x["bull_fvg"], 8) & (x["low"] <= x["bull_fvg_mid"]) & (x["close"] > x["bull_fvg_mid"])
        short = ts & _recent(x["sweep_up"], 12) & _recent(x["bos_down"], 8) & _recent(x["bear_fvg"], 8) & (x["high"] >= x["bear_fvg_mid"]) & (x["close"] < x["bear_fvg_mid"])
    elif spec.family == "fvg_retrace":
        long = tl & _recent(x["bull_fvg"], 12) & (x["low"] <= x["bull_fvg_mid"]) & (x["close"] > x["bull_fvg_mid"])
        short = ts & _recent(x["bear_fvg"], 12) & (x["high"] >= x["bear_fvg_mid"]) & (x["close"] < x["bear_fvg_mid"])
    elif spec.family == "fractal_cisd":
        long = tl & _recent(x["sweep_down"], 8) & (x["close"] > x["last_down_open"]) & (x["close"].shift(1) <= x["last_down_open"].shift(1))
        short = ts & _recent(x["sweep_up"], 8) & (x["close"] < x["last_up_open"]) & (x["close"].shift(1) >= x["last_up_open"].shift(1))
    elif spec.family == "ote":
        long = tl & x["bull_retracement"].between(0.62, 0.79) & (x["close"] > x["open"])
        short = ts & x["bear_retracement"].between(0.62, 0.79) & (x["close"] < x["open"])
    elif spec.family == "judas":
        session = x["ny_london_window"] | x["ny_am_window"]
        long = session & tl & x["sweep_down"]
        short = session & ts & x["sweep_up"]
    elif spec.family == "silver_bullet":
        long = x["silver_bullet_window"] & tl & _recent(x["sweep_down"], 4) & _recent(x["bull_fvg"], 4)
        short = x["silver_bullet_window"] & ts & _recent(x["sweep_up"], 4) & _recent(x["bear_fvg"], 4)
    elif spec.family == "external_internal":
        long = tl & x["sweep_down"] & (_recent(x["bull_fvg"], 6) | x["bos_up"])
        short = ts & x["sweep_up"] & (_recent(x["bear_fvg"], 6) | x["bos_down"])
    elif spec.family == "supply_demand":
        long = tl & x["demand_mid"].notna() & (x["low"] <= x["demand_mid"]) & (x["close"] > x["demand_mid"])
        short = ts & x["supply_mid"].notna() & (x["high"] >= x["supply_mid"]) & (x["close"] < x["supply_mid"])
    elif spec.family == "round_number":
        level = x["round_level"]
        buffer = 0.15 * x["atr"]
        long = tl & (x["low"] < level - buffer) & (x["close"] > level)
        short = ts & (x["high"] > level + buffer) & (x["close"] < level)
    elif spec.family == "fib_institutional":
        long = tl & x["bull_retracement"].between(0.50, 0.786) & (x["low"] <= x["bull_ob_mid"]) & (x["close"] > x["open"])
        short = ts & x["bear_retracement"].between(0.50, 0.786) & (x["high"] >= x["bear_ob_mid"]) & (x["close"] < x["open"])
    elif spec.family == "correlation_divergence":
        asset_break_low = x["low"] < x["low"].shift(1).rolling(20).min()
        asset_break_high = x["high"] > x["high"].shift(1).rolling(20).max()
        long = x["peer_break_low"].fillna(False) & ~asset_break_low & (x["close"] > x["ema50"])
        short = x["peer_break_high"].fillna(False) & ~asset_break_high & (x["close"] < x["ema50"])
    elif spec.family == "ichimoku_pullback":
        bull = (x["close"] > x["cloud_top"]) & (x["tenkan"] > x["kijun"])
        bear = (x["close"] < x["cloud_bottom"]) & (x["tenkan"] < x["kijun"])
        long = bull & (x["low"] <= x["tenkan"]) & (x["close"] > x["open"])
        short = bear & (x["high"] >= x["tenkan"]) & (x["close"] < x["open"])
    elif spec.family == "v59_confluence":
        # 1) Ichimoku state / momentum.
        kijun_slope = x["kijun"].diff(3)
        ichi_long = (
            (x["close"] > x["cloud_top"])
            & (x["tenkan"] > x["kijun"])
            & (kijun_slope > 0)
        )
        ichi_short = (
            (x["close"] < x["cloud_bottom"])
            & (x["tenkan"] < x["kijun"])
            & (kijun_slope < 0)
        )

        # 2) ICT/SMC structure: sweep/BOS, FVG rejection or OB mitigation.
        ict_long = (
            (_recent(x["sweep_down"], 12) & _recent(x["bos_up"], 8))
            | (
                _recent(x["bull_fvg"], 8)
                & x["bull_fvg_mid"].notna()
                & (x["low"] <= x["bull_fvg_mid"])
                & (x["close"] > x["bull_fvg_mid"])
            )
            | (
                _recent(x["bos_up"], 16)
                & x["bull_ob_mid"].notna()
                & (x["low"] <= x["bull_ob_mid"])
                & (x["close"] > x["bull_ob_mid"])
            )
        )
        ict_short = (
            (_recent(x["sweep_up"], 12) & _recent(x["bos_down"], 8))
            | (
                _recent(x["bear_fvg"], 8)
                & x["bear_fvg_mid"].notna()
                & (x["high"] >= x["bear_fvg_mid"])
                & (x["close"] < x["bear_fvg_mid"])
            )
            | (
                _recent(x["bos_down"], 16)
                & x["bear_ob_mid"].notna()
                & (x["high"] >= x["bear_ob_mid"])
                & (x["close"] < x["bear_ob_mid"])
            )
        )

        # 3) Brooks-style price-action quality.
        brooks_long = (
            (x["brooks_trend_strength"] >= 0.30)
            & (x["brooks_overlap_ratio"] <= 0.70)
            & (
                x["brooks_bull_signal_bar"].fillna(False)
                | x["brooks_bull_follow_through"].fillna(False)
                | x["brooks_microchannel_up"].fillna(False)
                | (x["brooks_breakout_up_atr"] >= 0.10)
            )
        )
        brooks_short = (
            (x["brooks_trend_strength"] <= -0.30)
            & (x["brooks_overlap_ratio"] <= 0.70)
            & (
                x["brooks_bear_signal_bar"].fillna(False)
                | x["brooks_bear_follow_through"].fillna(False)
                | x["brooks_microchannel_down"].fillna(False)
                | (x["brooks_breakout_down_atr"] >= 0.10)
            )
        )

        # 4) Regime / structural trend and 20-bar breakout evidence.
        regime_long = tl & (x["ema20"] > x["ema50"])
        regime_short = ts & (x["ema20"] < x["ema50"])
        breakout_long = (x["close"] > x["prior_high20"]) & (x["ema200_slope"] > 0)
        breakout_short = (x["close"] < x["prior_low20"]) & (x["ema200_slope"] < 0)

        long_score = (
            ichi_long.astype(int)
            + ict_long.astype(int)
            + brooks_long.astype(int)
            + regime_long.astype(int)
            + breakout_long.astype(int)
        )
        short_score = (
            ichi_short.astype(int)
            + ict_short.astype(int)
            + brooks_short.astype(int)
            + regime_short.astype(int)
            + breakout_short.astype(int)
        )

        x["v59_ichi_long"] = ichi_long.astype(float)
        x["v59_ichi_short"] = ichi_short.astype(float)
        x["v59_ict_smc_long"] = ict_long.astype(float)
        x["v59_ict_smc_short"] = ict_short.astype(float)
        x["v59_brooks_long"] = brooks_long.astype(float)
        x["v59_brooks_short"] = brooks_short.astype(float)
        x["v59_regime_long"] = regime_long.astype(float)
        x["v59_regime_short"] = regime_short.astype(float)
        x["v59_breakout_long"] = breakout_long.astype(float)
        x["v59_breakout_short"] = breakout_short.astype(float)
        x["v59_long_score"] = long_score.astype(float)
        x["v59_short_score"] = short_score.astype(float)

        # Require broad confirmation and a two-vote margin over the opposite
        # side. This avoids a weak 3-vs-2 mixed-state trade.
        long = (long_score >= 3) & ((long_score - short_score) >= 2)
        short = (short_score >= 3) & ((short_score - long_score) >= 2)
    elif spec.family == "s6_breakout":
        long = (x["close"] > x["prior_high20"]) & (x["ema200_slope"] > 0)
        short = (x["close"] < x["prior_low20"]) & (x["ema200_slope"] < 0)
    elif spec.family == "kumo_triangle":
        hi_slope = x["high"].diff(10).rolling(10).mean()
        lo_slope = x["low"].diff(10).rolling(10).mean()
        width = (x["prior_high20"] - x["prior_low20"]) / x["close"]
        contracting = width < width.shift(5)
        below_cloud = (x["close"] < x["cloud_bottom"]).shift(1).rolling(20).mean() >= 0.70
        long = contracting & (hi_slope < 0) & (lo_slope > 0) & below_cloud & (x["close"] > x["prior_high20"] + 0.10 * x["atr"]) & (x["tenkan"] > x["kijun"])
        short = pd.Series(False, index=x.index)
    elif spec.family == "ict_swing_pd":
        long = tl & x["bull_retracement"].between(0.50, 0.79) & (_recent(x["sweep_down"], 10) | (x["close"] > x["kijun"]))
        short = ts & x["bear_retracement"].between(0.50, 0.79) & (_recent(x["sweep_up"], 10) | (x["close"] < x["kijun"]))
    else:
        raise ValueError(f"unknown strategy family: {spec.family}")

    direction = pd.Series(0, index=x.index, dtype="int8")
    direction.loc[long.fillna(False)] = 1
    direction.loc[short.fillna(False)] = -1
    direction.loc[long.fillna(False) & short.fillna(False)] = 0
    return direction, x


def _segment(index: int, n: int, cfg: TournamentConfig) -> str:
    p = (index + 1) / max(n, 1)
    if p <= cfg.development_fraction:
        return "development"
    if p <= cfg.development_fraction + cfg.validation_fraction:
        return "validation"
    return "test"


def simulate_bracket_trades(spec: StrategySpec, frame: pd.DataFrame, direction: pd.Series, features: pd.DataFrame, symbol: str, config: TournamentConfig | None = None) -> pd.DataFrame:
    """Conservative non-overlapping bracket simulation; same-bar collision is stop-first."""

    cfg = config or TournamentConfig()
    x = features.reset_index(drop=True)
    d = pd.Series(direction).reset_index(drop=True).fillna(0).astype(int)
    records: list[dict] = []
    occupied_until = -1
    one_way = (cfg.fee_bps + cfg.slippage_bps) / 10_000.0
    n = len(x)
    for i in np.flatnonzero(d.to_numpy() != 0):
        if i <= occupied_until or i + 1 >= n:
            continue
        side = int(d.iloc[i])
        atr = float(x["atr"].iloc[i]) if np.isfinite(x["atr"].iloc[i]) else np.nan
        entry = float(x["open"].iloc[i + 1])
        if not np.isfinite(atr) or atr <= 0 or entry <= 0:
            continue
        stop_dist = max(spec.stop_atr * atr, entry * 0.0005)
        stop = entry - side * stop_dist
        target = entry + side * spec.rr * stop_dist
        exit_j = min(i + 1 + spec.max_hold_bars, n - 1)
        exit_price = float(x["close"].iloc[exit_j])
        reason = "timeout"
        for j in range(i + 1, min(n, i + 2 + spec.max_hold_bars)):
            high, low = float(x["high"].iloc[j]), float(x["low"].iloc[j])
            hit_stop = low <= stop if side > 0 else high >= stop
            hit_target = high >= target if side > 0 else low <= target
            if hit_stop:
                exit_price, reason, exit_j = stop, "stop", j
                break
            if hit_target:
                exit_price, reason, exit_j = target, "target", j
                break
        gross = side * (exit_price / entry - 1.0)
        net = gross - 2.0 * one_way
        stop_fraction = stop_dist / entry
        r_multiple = net / stop_fraction
        records.append({
            "strategy": spec.name, "family": spec.family, "timeframe": spec.timeframe,
            "symbol": symbol, "signal_time": x["timestamp"].iloc[i],
            "entry_time": x["timestamp"].iloc[i + 1], "exit_time": x["timestamp"].iloc[exit_j],
            "side": side, "entry": entry, "exit": exit_price, "stop": stop, "target": target,
            "exit_reason": reason, "gross_return": gross, "net_return": net,
            "r_multiple": r_multiple, "account_return": cfg.risk_per_trade * r_multiple,
            "segment": _segment(i, n, cfg), "source_basis": spec.source_basis,
        })
        occupied_until = exit_j
    return pd.DataFrame(records)


def _compound(r: pd.Series) -> float:
    return float((1.0 + r).prod() - 1.0) if len(r) else np.nan


def summarize_trades(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"trades": 0, "total_return": np.nan, "profit_factor": np.nan, "win_rate": np.nan, "expectancy_r": np.nan, "median_r": np.nan, "max_drawdown": np.nan, "mean_account_return": np.nan}
    r = trades["account_return"].astype(float)
    rm = trades["r_multiple"].astype(float)
    eq = (1.0 + r).cumprod()
    dd = eq / eq.cummax() - 1.0
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    pf = float(wins / losses) if losses > 0 else (np.inf if wins > 0 else np.nan)
    return {"trades": int(len(trades)), "total_return": _compound(r), "profit_factor": pf,
            "win_rate": float((r > 0).mean()), "expectancy_r": float(rm.mean()),
            "median_r": float(rm.median()), "max_drawdown": float(dd.min()),
            "mean_account_return": float(r.mean())}


def moving_block_mean_ci(values: Iterable[float], *, samples: int = 500, block: int = 20, seed: int = 314) -> tuple[float, float]:
    a = np.asarray(list(values), dtype=float)
    a = a[np.isfinite(a)]
    if len(a) < max(30, block * 2):
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    block = max(2, min(block, len(a)))
    starts = np.arange(0, len(a) - block + 1)
    means = np.empty(samples)
    blocks_needed = int(np.ceil(len(a) / block))
    for s in range(samples):
        picks = rng.choice(starts, size=blocks_needed, replace=True)
        sample = np.concatenate([a[p:p + block] for p in picks])[:len(a)]
        means[s] = sample.mean()
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def evaluate_candidate(trades: pd.DataFrame, config: TournamentConfig | None = None) -> dict:
    cfg = config or TournamentConfig()
    out: dict[str, object] = {}
    for segment in ("development", "validation", "test"):
        m = summarize_trades(trades[trades["segment"] == segment]) if not trades.empty else summarize_trades(trades)
        for k, v in m.items():
            out[f"{segment}_{k}"] = v
    pretest = trades[trades["segment"].isin(["development", "validation"])] if not trades.empty else trades
    out["pretest_trades"] = int(len(pretest))
    out["all_trades"] = int(len(trades))
    val = trades[trades["segment"] == "validation"] if not trades.empty else trades
    test = trades[trades["segment"] == "test"] if not trades.empty else trades
    val_assets = val.groupby("symbol")["account_return"].sum() if len(val) else pd.Series(dtype=float)
    test_assets = test.groupby("symbol")["account_return"].sum() if len(test) else pd.Series(dtype=float)
    out["validation_positive_asset_fraction"] = float((val_assets > 0).mean()) if len(val_assets) else 0.0
    out["test_positive_asset_fraction"] = float((test_assets > 0).mean()) if len(test_assets) else 0.0
    lo, hi = moving_block_mean_ci(test["account_return"] if len(test) else [], samples=cfg.bootstrap_samples, block=cfg.bootstrap_block, seed=cfg.random_seed)
    out["test_bootstrap_mean_ci_low"], out["test_bootstrap_mean_ci_high"] = lo, hi
    val_pf = float(out.get("validation_profit_factor", np.nan))
    val_exp = float(out.get("validation_expectancy_r", np.nan))
    val_dd = abs(float(out.get("validation_max_drawdown", np.nan)))
    n_val = int(out.get("validation_trades", 0))
    out["validation_score"] = float(val_exp * np.sqrt(max(n_val, 1)) + 0.10 * (min(val_pf, 5.0) - 1.0) - 0.50 * val_dd) if np.isfinite(val_exp) and np.isfinite(val_pf) and np.isfinite(val_dd) else -np.inf
    out["pretest_eligible"] = bool(
        int(out["pretest_trades"]) >= cfg.min_pretest_trades and n_val >= max(100, cfg.min_test_trades // 2)
        and np.isfinite(val_pf) and val_pf >= cfg.min_validation_profit_factor
        and np.isfinite(val_exp) and val_exp > 0
        and float(out["validation_positive_asset_fraction"]) >= cfg.min_positive_asset_fraction
        and np.isfinite(float(out["validation_max_drawdown"]))
        and abs(float(out["validation_max_drawdown"])) <= cfg.max_drawdown
    )
    return out


def choose_provisional_winner(summary: pd.DataFrame, config: TournamentConfig | None = None) -> dict:
    """Select on validation only; then use untouched test as a one-shot gate."""

    cfg = config or TournamentConfig()
    eligible = summary[summary["pretest_eligible"] == True].copy()  # noqa: E712
    if eligible.empty:
        return {"decision": "NO_STRATEGY_PROMOTED", "reason": "no candidate cleared the frozen pre-test gate", "winner": None, "live_execution_authorized": False, "paper_replacement_authorized": False}
    selected = eligible.sort_values("validation_score", ascending=False).iloc[0]
    test_pass = bool(
        int(selected["test_trades"]) >= cfg.min_test_trades
        and float(selected["test_profit_factor"]) >= cfg.min_test_profit_factor
        and float(selected["test_expectancy_r"]) > 0
        and float(selected["test_positive_asset_fraction"]) >= cfg.min_positive_asset_fraction
        and np.isfinite(float(selected["test_max_drawdown"]))
        and abs(float(selected["test_max_drawdown"])) <= cfg.max_drawdown
        and np.isfinite(float(selected["test_bootstrap_mean_ci_low"]))
        and float(selected["test_bootstrap_mean_ci_low"]) > 0
    )
    return {"decision": "FORWARD_PAPER_CANDIDATE" if test_pass else "NO_STRATEGY_PROMOTED",
            "reason": "selected on validation; final test passed frozen gates" if test_pass else "validation winner failed one or more untouched-test gates",
            "winner": str(selected["strategy"]), "timeframe": str(selected["timeframe"]),
            "family": str(selected["family"]), "live_execution_authorized": False,
            "paper_replacement_authorized": False}
