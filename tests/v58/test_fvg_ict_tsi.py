"""Regression tests for the supplied MTF FVG/ICT/TSI strategy and unified signal finder."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research_bot.v58 import fvg_ict_tsi as strategy
from research_bot.v58.fvg_ict_tsi import FVG, FVGICTTSIConfig
from research_bot.v58.signal_finder import CombinedSignalConfig, scan_combined_signals


def raw_minutes(rows: int = 1200) -> pd.DataFrame:
    t = pd.date_range("2026-01-01", periods=rows, freq="1min", tz="UTC")
    base = 100.0 + np.linspace(0, 1, rows)
    return pd.DataFrame(
        {
            "timestamp": t,
            "open": base,
            "high": base + 0.10,
            "low": base - 0.10,
            "close": base + 0.02,
            "volume": np.full(rows, 100.0),
        }
    )


def test_config_freezes_user_requested_tsi_and_financial_ceiling():
    cfg = FVGICTTSIConfig()
    assert (cfg.tsi_long, cfg.tsi_short, cfg.tsi_signal) == (25, 13, 13)
    assert cfg.confirmation == "either"
    assert cfg.stop_mode == "zone_edge"
    assert cfg.risk_per_trade == pytest.approx(0.0025)
    with pytest.raises(ValueError, match="0.25"):
        FVGICTTSIConfig(risk_per_trade=0.01)


def test_input_requires_full_timestamp_ohlcv_contract():
    frame = raw_minutes(10).drop(columns=["volume"])
    with pytest.raises(ValueError, match="volume"):
        strategy.normalize_ohlcv(frame)


def test_three_candle_fvg_requires_displacement_body():
    idx = pd.date_range("2026-01-01", periods=16, freq="1h", tz="UTC")
    frame = pd.DataFrame(
        {
            "open": np.full(16, 100.0),
            "high": np.full(16, 100.4),
            "low": np.full(16, 99.6),
            "close": np.full(16, 100.1),
            "volume": np.full(16, 100.0),
        },
        index=idx,
    )
    frame.iloc[14, frame.columns.get_loc("open")] = 100.0
    frame.iloc[14, frame.columns.get_loc("close")] = 103.0
    frame.iloc[14, frame.columns.get_loc("high")] = 103.2
    frame.iloc[14, frame.columns.get_loc("low")] = 99.9
    frame.iloc[15, frame.columns.get_loc("open")] = 101.0
    frame.iloc[15, frame.columns.get_loc("low")] = 100.8
    frame.iloc[15, frame.columns.get_loc("high")] = 101.5
    frame.iloc[15, frame.columns.get_loc("close")] = 101.2
    found = strategy.detect_fvgs(frame, FVGICTTSIConfig(htf="1h", ltf="5min"))
    assert any(f.direction == "long" for f in found)

    quiet = frame.copy()
    quiet.iloc[14, quiet.columns.get_loc("close")] = 100.05
    quiet.iloc[14, quiet.columns.get_loc("high")] = 100.2
    quiet.iloc[14, quiet.columns.get_loc("low")] = 99.9
    assert not strategy.detect_fvgs(
        quiet,
        FVGICTTSIConfig(htf="1h", ltf="5min", displacement_body_atr=5.0),
    )


@pytest.mark.parametrize(
    "mode,tsi_hit,structure_hit,expected",
    [
        ("tsi", True, False, True),
        ("structure", False, True, True),
        ("either", False, True, True),
        ("either", False, False, False),
        ("both", True, True, True),
        ("both", True, False, False),
    ],
)
def test_confirmation_modes(mode, tsi_hit, structure_hit, expected):
    assert strategy._confirmation(tsi_hit, structure_hit, mode) is expected


def test_three_stop_modes_are_deterministic():
    idx = pd.date_range("2026-01-01", periods=10, freq="5min", tz="UTC")
    df = pd.DataFrame(
        {
            "open": 100.0,
            "high": np.linspace(100.4, 101.0, 10),
            "low": np.linspace(99.5, 99.9, 10),
            "close": 100.2,
            "volume": 100.0,
            "atr": 1.0,
        },
        index=idx,
    )
    fvg = FVG(1, "long", idx[0], 99.8, 100.2, 100.0, 1.0, 0, 1.0)
    midpoint = strategy.stop_price(df, 9, fvg, FVGICTTSIConfig(stop_mode="midpoint"))
    edge = strategy.stop_price(df, 9, fvg, FVGICTTSIConfig(stop_mode="zone_edge"))
    swing = strategy.stop_price(df, 9, fvg, FVGICTTSIConfig(stop_mode="swing"))
    assert midpoint == pytest.approx(99.9)
    assert edge == pytest.approx(99.7)
    assert swing < midpoint


def test_next_bar_open_no_lookahead_fvg_is_consumed_once(monkeypatch):
    raw = raw_minutes(500)
    cfg = FVGICTTSIConfig(htf="1h", ltf="5min", confirmation="either")
    formed = pd.Timestamp("2026-01-01T02:00:00Z")
    fake = FVG(7, "long", formed, 99.0, 200.0, 99.5, 1.0, 1, 1.2)
    monkeypatch.setattr(strategy, "detect_fvgs", lambda *_: [fake])
    monkeypatch.setattr(strategy, "midpoint_rejection", lambda *_: True)
    monkeypatch.setattr(strategy, "tsi_cross", lambda *_: True)
    monkeypatch.setattr(strategy, "structure_break", lambda *_: False)
    monkeypatch.setattr(strategy, "stop_price", lambda *_: 99.0)

    out = strategy.scan_signals(raw, cfg, symbol="BTC/USDT")
    signals = out["signals"]
    assert len(signals) == 1
    row = signals.iloc[0]
    assert row["signal_time"] == row["entry_time"]
    entry_stamp = pd.Timestamp(row["entry_time"])
    assert float(row["entry_reference_price"]) == pytest.approx(float(out["ltf"].at[entry_stamp, "open"]))
    assert int(row["fvg_id"]) == 7


def test_bearish_fvg_is_detected_but_not_authorized_by_current_spot_portfolio(monkeypatch):
    raw = raw_minutes(500)
    cfg = FVGICTTSIConfig(htf="1h", ltf="5min")
    fake = FVG(8, "short", pd.Timestamp("2026-01-01T02:00:00Z"), 50.0, 200.0, 150.0, 1.0, 1, 1.2)
    monkeypatch.setattr(strategy, "detect_fvgs", lambda *_: [fake])
    monkeypatch.setattr(strategy, "midpoint_rejection", lambda *_: True)
    monkeypatch.setattr(strategy, "tsi_cross", lambda *_: True)
    monkeypatch.setattr(strategy, "structure_break", lambda *_: False)
    monkeypatch.setattr(strategy, "stop_price", lambda *_: 200.0)
    out = strategy.scan_signals(raw, cfg, symbol="BTC/USDT")
    assert len(out["signals"]) == 1
    row = out["signals"].iloc[0]
    assert row["direction"] == "short"
    assert bool(row["portfolio_execution_eligible"]) is False
    assert "SHORT_NOT_AUTHORIZED" in row["execution_note"]


def test_combined_signal_registry_contains_ten_confluence_hypotheses_plus_supplied_family():
    result = scan_combined_signals(
        raw_minutes(),
        CombinedSignalConfig(htf="1h", ltf="5min"),
        symbol="BTC/USDT",
    )
    assert len(result["registry"]["confluence10"]) == 10
    assert result["registry"]["fvg_ict_tsi"]["strategy_id"] == "FVG_ICT_TSI_MTF"
    assert result["summary"]["ai_gate_required"] is True
    assert result["summary"]["financial_gate_required"] is True
    assert result["summary"]["paper_execution"] is False
    assert result["summary"]["live_execution"] is False
