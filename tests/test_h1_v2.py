"""Causal execution invariants for the preregistered H1 development script."""

import math

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("xgboost")

from scripts.evaluate_h1_v2 import Trade, barrier_trade, entry_features, nav_metrics


def _bars(n=200):
    ts = pd.date_range("2022-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": 100.0, "high": 100.5,
                         "low": 99.5, "close": 100.0})


def test_ambiguous_bar_uses_loss_and_gap_uses_worse_open():
    bars = _bars()
    atr = pd.Series(0.01, index=bars.index)
    bars.loc[2, ["high", "low"]] = [104.0, 96.0]
    trade = barrier_trade(bars, atr, 0.01, entry=2)
    assert trade.exit == 2 and trade.exit_px == 97.0
    assert not trade.exit_at_open and trade.factor < 1
    bars.loc[2, ["high", "low"]] = [100.5, 99.5]
    bars.loc[3, ["open", "high", "low", "close"]] = [96.0, 98.0, 95.0, 97.0]
    gap = barrier_trade(bars, atr, 0.01, entry=2)
    assert gap.exit_px == 96.0 and gap.exit_at_open


def test_entry_features_ignore_future_prices():
    bars = _bars()
    atr = pd.Series(0.01, index=bars.index)
    regime = pd.Series(1.0, index=bars.index)
    before = entry_features(bars, atr, regime, decision=100, score=7)
    bars.loc[101:, ["open", "high", "low", "close"]] = 500.0
    after = entry_features(bars, atr, regime, decision=100, score=7)
    assert before == after


def test_daily_nav_compounds_to_trade_factor():
    bars = _bars(96 * 40)
    trade = Trade(entry=100, exit=400, entry_px=100, exit_px=103,
                  entry_cost=0.0015, exit_cost=0.0015, exit_at_open=True)
    metrics, daily_logs = nav_metrics(bars, [trade], 0, len(bars))
    assert metrics["trades"] == 1
    assert metrics["days"] == 40
    assert math.isclose(metrics["net_return"] + 1, trade.factor, rel_tol=1e-12)
    assert math.isclose(math.exp(float(np.sum(daily_logs))), trade.factor, rel_tol=1e-12)


def test_intrabar_exit_blocks_entry_at_same_bar_open():
    bars = _bars(96 * 40)
    first = Trade(100, 200, 100, 101, 0.0015, 0.0015, False)
    impossible = Trade(200, 300, 100, 101, 0.0015, 0.0015, True)
    with pytest.raises(AssertionError, match="OVERLAP"):
        nav_metrics(bars, [first, impossible], 0, len(bars))


def test_terminal_close_exposure_is_one_not_more():
    bars = _bars(96 * 40)
    hold = Trade(0, len(bars), 100, 100, 0.0015, 0.0015, False)
    metrics, _ = nav_metrics(bars, [hold], 0, len(bars))
    assert metrics["exposure_fraction"] == 1.0
