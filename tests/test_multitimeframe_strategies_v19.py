import numpy as np
import pandas as pd

from research_bot.multitimeframe_strategies_v19 import (
    STRATEGY_REGISTRY,
    TournamentConfig,
    build_features,
    choose_provisional_winner,
    generate_direction,
    registry_frame,
    simulate_bracket_trades,
)


def synthetic(n=1200, seed=7):
    rng = np.random.default_rng(seed)
    ret = rng.normal(0.00015, 0.008, n)
    close = 100 * np.exp(np.cumsum(ret))
    open_ = np.r_[close[0], close[:-1]]
    spread = np.maximum(0.002 * close, rng.uniform(0.001, 0.01, n) * close)
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC"),
        "open": open_, "high": high, "low": low, "close": close,
        "volume": rng.lognormal(8, 0.5, n),
    })


def test_registry_has_five_per_timeframe():
    r = registry_frame()
    assert len(r) >= 30
    counts = r.groupby("timeframe").size().to_dict()
    for tf in ("1m", "5m", "15m", "1h", "4h", "1d"):
        assert counts[tf] >= 5


def test_features_are_prefix_invariant():
    df = synthetic()
    full = build_features(df)
    cut = 800
    prefix = build_features(df.iloc[:cut].copy())
    cols = [
        "atr", "ema200", "last_swing_high", "last_swing_low",
        "bos_up", "sweep_down", "bull_fvg",
        "brooks_body_ratio", "brooks_overlap_ratio",
        "brooks_trend_strength", "brooks_breakout_up_atr",
        "brooks_bull_signal_bar", "brooks_bull_follow_through",
        "brooks_microchannel_up",
    ]
    for c in cols:
        a = full.loc[:cut - 1, c].reset_index(drop=True)
        b = prefix[c].reset_index(drop=True)
        if a.dtype == bool or b.dtype == bool:
            assert a.fillna(False).equals(b.fillna(False))
        else:
            assert np.allclose(a.to_numpy(float), b.to_numpy(float), equal_nan=True)


def test_all_families_generate_causal_direction():
    df = synthetic(1500)
    peer = synthetic(1500, seed=8)
    for spec in STRATEGY_REGISTRY:
        d, f = generate_direction(spec, df, peer=peer)
        assert len(d) == len(f) == len(df)
        assert set(pd.Series(d).dropna().unique()).issubset({-1, 0, 1})


def test_bracket_same_bar_is_stop_first():
    df = synthetic(300)
    spec = STRATEGY_REGISTRY[0]
    d, f = generate_direction(spec, df)
    d[:] = 0
    i = 220
    d.iloc[i] = 1
    entry = float(f["open"].iloc[i + 1])
    stop_dist = max(spec.stop_atr * float(f["atr"].iloc[i]), entry * 0.0005)
    f.loc[i + 1, "low"] = entry - 2 * stop_dist
    f.loc[i + 1, "high"] = entry + 4 * stop_dist
    trades = simulate_bracket_trades(spec, f, d, f, "TEST/USDT", TournamentConfig(min_pretest_trades=1, min_test_trades=1))
    assert len(trades) == 1
    assert trades.iloc[0]["exit_reason"] == "stop"


def test_selection_does_not_promote_without_gates():
    s = pd.DataFrame([{
        "strategy": "X", "timeframe": "1h", "family": "x",
        "pretest_eligible": False, "validation_score": 99.0,
        "test_trades": 999, "test_profit_factor": 9.0, "test_expectancy_r": 1.0,
        "test_positive_asset_fraction": 1.0, "test_max_drawdown": -0.01,
        "test_bootstrap_mean_ci_low": 0.1,
    }])
    decision = choose_provisional_winner(s)
    assert decision["decision"] == "NO_STRATEGY_PROMOTED"


def test_v59_confluence_is_prefix_invariant():
    df = synthetic(1400, seed=19)
    spec = next(s for s in STRATEGY_REGISTRY if s.name == "H4_V59_CONFLUENCE_DEMO")

    d_full, f_full = generate_direction(spec, df)
    cut = 1000
    d_prefix, f_prefix = generate_direction(spec, df.iloc[:cut].copy())

    assert d_full.iloc[:cut].reset_index(drop=True).equals(
        d_prefix.reset_index(drop=True)
    )
    for col in (
        "v59_long_score",
        "v59_short_score",
        "v59_ichi_long",
        "v59_ict_smc_long",
        "v59_brooks_long",
        "v59_regime_long",
        "v59_breakout_long",
    ):
        a = f_full[col].iloc[:cut].reset_index(drop=True)
        b = f_prefix[col].reset_index(drop=True)
        assert np.allclose(a.to_numpy(float), b.to_numpy(float), equal_nan=True)


def test_v59_confluence_registry_contract():
    spec = next(s for s in STRATEGY_REGISTRY if s.name == "H4_V59_CONFLUENCE_DEMO")
    assert spec.timeframe == "4h"
    assert spec.family == "v59_confluence"
    assert spec.rr == 2.5
    assert spec.stop_atr == 1.5
