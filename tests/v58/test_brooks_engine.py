from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest
import yaml

from research_bot.v58.brooks import (
    PRIMARY_BROOKS_CONFIG,
    BrooksSetup,
    add_brooks_atomic_features,
    detect_brooks_setups,
    generate_brooks_events,
)
from research_bot.v58.contracts import Direction, StrategyArm


def _random_bars(n: int = 260, seed: int = 5801) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    returns = rng.normal(0.0002, 0.004, n)
    close = 100 * np.exp(np.cumsum(returns))
    open_ = np.r_[close[0], close[:-1]]
    width = close * rng.uniform(0.002, 0.007, n)
    return pd.DataFrame({
        "timestamp": ts,
        "open": open_,
        "high": np.maximum(open_, close) + width,
        "low": np.minimum(open_, close) - width,
        "close": close,
        "volume": rng.lognormal(8.0, 0.25, n),
    })


def _bars_from_close(close: list[float]) -> pd.DataFrame:
    c = np.asarray(close, dtype=float)
    o = np.r_[c[0], c[:-1]]
    width = np.maximum(0.25, c * 0.002)
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=len(c), freq="4h", tz="UTC"),
        "open": o,
        "high": np.maximum(o, c) + width,
        "low": np.minimum(o, c) - width,
        "close": c,
        "volume": np.linspace(1000, 1300, len(c)),
    })


def test_brooks_config_is_frozen_primary_contract():
    c = PRIMARY_BROOKS_CONFIG
    assert c.timeframe == "4h"
    assert c.range_window == 20
    assert c.trend_strength_min_atr == 0.25
    assert c.breakout_strength_min_atr == 0.10
    assert c.failed_breakout_lifetime_bars == 2
    assert c.range_overlap_min == 0.45


def test_atomic_features_are_causal_under_future_mutation():
    a = _random_bars(300)
    b = a.copy()
    cutoff = 230
    b.loc[cutoff + 1 :, "close"] *= np.linspace(1.0, 2.0, len(b) - cutoff - 1)
    b.loc[cutoff + 1 :, "open"] *= np.linspace(1.0, 2.0, len(b) - cutoff - 1)
    b.loc[cutoff + 1 :, "high"] = np.maximum(
        b.loc[cutoff + 1 :, "high"],
        b.loc[cutoff + 1 :, ["open", "close"]].max(axis=1) * 1.002,
    )
    b.loc[cutoff + 1 :, "low"] = np.minimum(
        b.loc[cutoff + 1 :, "low"],
        b.loc[cutoff + 1 :, ["open", "close"]].min(axis=1) * 0.998,
    )
    fa = add_brooks_atomic_features(a)
    fb = add_brooks_atomic_features(b)
    cols = [
        "brooks_body_fraction",
        "brooks_close_location",
        "brooks_prior_range_high",
        "brooks_prior_range_low",
        "brooks_range_state",
        "BRK_B1_signal",
        "BRK_B2_signal",
        "BRK_B3_signal",
        "BRK_B4_signal",
    ]
    pd.testing.assert_frame_equal(fa.loc[:cutoff, cols], fb.loc[:cutoff, cols])


def test_detector_replay_is_deterministic():
    frame = _random_bars()
    a = detect_brooks_setups(frame)
    b = detect_brooks_setups(frame.copy())
    cols = [
        "timestamp",
        "BRK_B1_signal",
        "BRK_B2_signal",
        "BRK_B3_signal",
        "BRK_B4_signal",
        "brooks_signal_quality",
    ]
    pd.testing.assert_frame_equal(a[cols], b[cols])


def test_breakout_follow_through_detects_constructed_long_case():
    close = [100.0 + 0.05 * np.sin(i) for i in range(30)]
    close += [101.5, 102.2, 102.4]
    frame = _bars_from_close(close)
    x = detect_brooks_setups(frame)
    assert (x["BRK_B2_signal"] == 1).any()


def test_failed_breakout_detects_constructed_reversal_case():
    close = [100.0 + 0.05 * np.sin(i) for i in range(30)]
    close += [101.5, 99.7, 99.6]
    frame = _bars_from_close(close)
    # Strengthen failure bar so it satisfies bearish signal-bar quality.
    i = 31
    frame.loc[i, "open"] = 101.4
    frame.loc[i, "high"] = 101.5
    frame.loc[i, "low"] = 99.5
    frame.loc[i, "close"] = 99.7
    x = detect_brooks_setups(frame)
    assert (x["BRK_B3_signal"] == -1).any()


def test_b5_is_study_only_and_cannot_emit_events():
    frame = _random_bars()
    x = detect_brooks_setups(frame)
    assert x["BRK_B5_study_eligible"].any()
    with pytest.raises(ValueError, match="B5 is a study"):
        generate_brooks_events(
            frame,
            symbol="BTC/USDT",
            venue="coinex",
            data_version="unit",
            code_version="unit",
            setups=(BrooksSetup.B5_SIGNAL_BAR_QUALITY_STUDY,),
        )


def test_generated_events_are_arm_d_next_bar_open_and_common_barrier():
    frame = _bars_from_close(
        [100.0 + 0.05 * np.sin(i) for i in range(30)] + [101.5, 102.2, 102.4, 102.5]
    )
    events = generate_brooks_events(
        frame,
        symbol="BTC/USDT",
        venue="coinex",
        data_version="unit-data",
        code_version="unit-code",
        setups=(BrooksSetup.B2_BREAKOUT_FOLLOW_THROUGH,),
    )
    assert events
    item = events[0]
    event = item.event
    assert event.strategy_arm is StrategyArm.ARM_D
    assert event.entry_time == event.timestamp + timedelta(hours=4)
    assert event.entry_reference == "NEXT_EXECUTABLE_BAR_OPEN"
    assert event.risk_R == 3.0
    assert event.holding_horizon == 30
    risk = abs(event.entry_price - event.stop_price)
    reward = abs(event.target_price - event.entry_price)
    assert reward == pytest.approx(3.0 * risk)


def test_generated_event_ids_are_deterministic():
    frame = _bars_from_close(
        [100.0 + 0.05 * np.sin(i) for i in range(30)] + [101.5, 102.2, 102.4, 102.5]
    )
    kwargs = dict(
        symbol="BTC/USDT",
        venue="coinex",
        data_version="unit-data",
        code_version="unit-code",
        setups=(BrooksSetup.B2_BREAKOUT_FOLLOW_THROUGH,),
    )
    a = generate_brooks_events(frame, **kwargs)
    b = generate_brooks_events(frame.copy(), **kwargs)
    assert [x.event.event_id for x in a] == [x.event.event_id for x in b]
    assert [x.event.canonical_payload() for x in a] == [x.event.canonical_payload() for x in b]


def test_config_file_keeps_training_and_execution_closed():
    cfg = yaml.safe_load(open("configs/v58/al_brooks.yaml", encoding="utf-8"))
    assert cfg["model_training_authorized"] is False
    assert cfg["paper_execution"] is False
    assert cfg["live_execution"] is False
    assert cfg["setups"]["BRK_B5_SIGNAL_BAR_QUALITY_STUDY"]["executable_event"] is False
