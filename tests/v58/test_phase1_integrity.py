from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from research_bot.v58.contracts import (
    LIVE_EXECUTION,
    PAPER_EXECUTION,
    Direction,
    PITValue,
    RegimeLabel,
    StrategyArm,
    TargetClass,
    assert_research_only,
)
from research_bot.v58.events import EventRecord, EventRegistry, make_event_id, make_feature_snapshot_id
from research_bot.v58.execution_sim import next_bar_open_entry
from research_bot.v58.features import add_v58_continuous_features
from research_bot.v58.ledger import EvidenceLedger
from research_bot.v58.manifest import build_dataset_manifest, manifest_hash
from research_bot.v58.regimes import add_causal_regime
from research_bot.v58.targets import OHLCBar, resolve_target


UTC = timezone.utc


def _raw(n: int = 240, seed: int = 58) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    r = rng.normal(0.0001, 0.003, n)
    close = 100 * np.exp(np.cumsum(r))
    open_ = np.r_[close[0], close[:-1]]
    width = close * rng.uniform(0.001, 0.004, n)
    return pd.DataFrame(
        {
            "timestamp": ts,
            "open": open_,
            "high": np.maximum(open_, close) + width,
            "low": np.minimum(open_, close) - width,
            "close": close,
            "volume": rng.lognormal(8, 0.3, n),
        }
    )


def _event(direction: Direction = Direction.LONG) -> EventRecord:
    t = datetime(2026, 1, 1, 12, tzinfo=UTC)
    setup = t - timedelta(hours=1)
    features = {"trend_strength": 0.4, "ATR_percent": 0.02}
    snapshot = make_feature_snapshot_id(event_timestamp=t, feature_version="58.0", features=features)
    event_id = make_event_id(
        symbol="BTC/USDT",
        venue="coinex",
        strategy_arm=StrategyArm.ARM_A,
        direction=direction,
        setup_timestamp=setup,
        entry_reference="next_bar_open",
        feature_snapshot_id=snapshot,
        strategy_version="58.0",
    )
    if direction is Direction.LONG:
        stop, entry, target = 99.0, 100.0, 102.0
    else:
        target, entry, stop = 98.0, 100.0, 101.0
    return EventRecord(
        event_id=event_id,
        symbol="BTC/USDT",
        venue="coinex",
        timestamp=t,
        setup_timestamp=setup,
        strategy_arm=StrategyArm.ARM_A,
        direction=direction,
        entry_reference="next_bar_open",
        entry_time=t + timedelta(hours=1),
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        risk_R=2.0,
        holding_horizon=3,
        decision_timeframe="1h",
        regime=RegimeLabel.TREND_UP,
        regime_confidence=0.8,
        feature_snapshot_id=snapshot,
        data_version="data-v1",
        code_version="code-v58",
        strategy_version="58.0",
        available_at=t,
        source_event_hash="a" * 64,
    )


def test_event_timestamp():
    event = _event()
    assert event.entry_time > event.timestamp
    with pytest.raises(ValueError, match="available_at"):
        replace(event, available_at=event.timestamp + timedelta(seconds=1))


def test_event_id_determinism():
    event = _event()
    again = make_event_id(
        symbol=event.symbol,
        venue=event.venue,
        strategy_arm=event.strategy_arm,
        direction=event.direction,
        setup_timestamp=event.setup_timestamp,
        entry_reference=event.entry_reference,
        feature_snapshot_id=event.feature_snapshot_id,
        strategy_version=event.strategy_version,
    )
    assert again == event.event_id
    changed = make_event_id(
        symbol="ETH/USDT",
        venue=event.venue,
        strategy_arm=event.strategy_arm,
        direction=event.direction,
        setup_timestamp=event.setup_timestamp,
        entry_reference=event.entry_reference,
        feature_snapshot_id=event.feature_snapshot_id,
        strategy_version=event.strategy_version,
    )
    assert changed != event.event_id


def test_duplicate_event():
    registry = EventRegistry()
    event = _event()
    registry.add(event)
    with pytest.raises(RuntimeError, match="duplicate event_id"):
        registry.add(event)


def test_stop_target_ambiguity():
    event = _event()
    bars = [
        OHLCBar(event.entry_time, 100.0, 103.0, 98.0, 101.0),
        OHLCBar(event.entry_time + timedelta(hours=1), 101.0, 102.0, 100.0, 101.5),
        OHLCBar(event.entry_time + timedelta(hours=2), 101.5, 102.0, 100.5, 101.0),
    ]
    out = resolve_target(event, bars)
    assert out.target_class is TargetClass.SL
    assert out.intrabar_ambiguity is True
    assert out.ambiguity_policy == "STOP_FIRST"


def test_next_bar_execution():
    decision = datetime(2026, 1, 1, 12, tzinfo=UTC)
    bars = [
        OHLCBar(decision, 100, 101, 99, 100.5),
        OHLCBar(decision + timedelta(hours=1), 101, 102, 100, 101.5),
    ]
    fill = next_bar_open_entry(decision_at=decision, bars=bars, direction=Direction.LONG, spread_bps=10, slippage_bps=5)
    assert fill.entry_time == decision + timedelta(hours=1)
    assert fill.reference_open == 101
    assert fill.fill_price > fill.reference_open


def test_pit_availability():
    decision = datetime(2026, 1, 1, 12, tzinfo=UTC)
    item = PITValue(
        effective_at=decision - timedelta(hours=1),
        available_at=decision + timedelta(seconds=1),
        observed_at=decision - timedelta(minutes=30),
        source="source",
        source_hash="b" * 64,
        value=1.0,
    )
    with pytest.raises(ValueError, match="availability"):
        item.validate_for(decision)


def test_feature_causality():
    a = _raw()
    b = a.copy()
    cutoff = 170
    b.loc[cutoff + 1 :, "close"] *= np.linspace(1.0, 2.0, len(b) - cutoff - 1)
    b.loc[cutoff + 1 :, "high"] = np.maximum(b.loc[cutoff + 1 :, "high"], b.loc[cutoff + 1 :, "close"] * 1.001)
    b.loc[cutoff + 1 :, "low"] = np.minimum(b.loc[cutoff + 1 :, "low"], b.loc[cutoff + 1 :, "close"] * 0.999)
    fa = add_v58_continuous_features(a)
    fb = add_v58_continuous_features(b)
    cols = [
        "ATR_percent", "returns_12", "tenkan_kijun_distance_atr",
        "sweep_depth_atr", "trend_strength", "bars_since_breakout",
    ]
    pd.testing.assert_frame_equal(fa.loc[:cutoff, cols], fb.loc[:cutoff, cols])


def test_no_lookahead():
    a = _raw()
    b = a.copy()
    b.loc[200:, ["open", "high", "low", "close", "volume"]] *= 5.0
    fa = add_v58_continuous_features(a)
    fb = add_v58_continuous_features(b)
    pd.testing.assert_frame_equal(fa.loc[:199], fb.loc[:199])


def test_regime_causality():
    a = _raw(320)
    b = a.copy()
    b.loc[260:, "close"] *= np.linspace(1.0, 1.8, len(b) - 260)
    b.loc[260:, "high"] = np.maximum(b.loc[260:, "high"], b.loc[260:, "close"] * 1.002)
    b.loc[260:, "low"] = np.minimum(b.loc[260:, "low"], b.loc[260:, "close"] * 0.998)
    ra = add_causal_regime(a)
    rb = add_causal_regime(b)
    pd.testing.assert_series_equal(ra.loc[:259, "regime"], rb.loc[:259, "regime"])
    pd.testing.assert_series_equal(ra.loc[:259, "regime_confidence"], rb.loc[:259, "regime_confidence"])


def test_hash_reproducibility():
    frame = _raw(100)
    a = build_dataset_manifest(frame, source="unit", symbol="BTC/USDT", venue="coinex", timeframe="1h", data_version="v1")
    b = build_dataset_manifest(frame.copy(), source="unit", symbol="BTC/USDT", venue="coinex", timeframe="1h", data_version="v1")
    assert a["frame_sha256"] == b["frame_sha256"]
    assert a["schema_sha256"] == b["schema_sha256"]
    assert manifest_hash(a) == manifest_hash(b)


def test_replay_determinism():
    event = _event()
    first = event.canonical_payload()
    second = _event().canonical_payload()
    assert first == second
    ledger = EvidenceLedger()
    t = datetime(2026, 1, 1, 13, tzinfo=UTC)
    e1 = ledger.append(event_id=event.event_id, stage="SNAPSHOT_FROZEN", status="OK", payload={"x": 1}, timestamp=t)
    e2 = ledger.append(event_id=event.event_id, stage="LEDGER_FINALIZED", status="NO_TRADE", payload={"reason": "TEST"}, timestamp=t + timedelta(seconds=1))
    assert e1.previous_hash == "0" * 64
    assert e2.previous_hash == e1.entry_hash
    assert ledger.verify() is True


def test_live_execution_hard_false():
    assert LIVE_EXECUTION is False
    assert_research_only()


def test_paper_execution_hard_false():
    assert PAPER_EXECUTION is False
    assert_research_only()
