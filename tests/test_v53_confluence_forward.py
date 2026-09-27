from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from research_bot.confluence_v53 import ConfluenceConfigV53, decide_confluence_v53
from research_bot.forward_multiframe_v53 import (
    ForwardMultiframeConfigV53,
    observe_multiframe_symbol_v53,
)


def _bars(n: int, freq: str, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ret = rng.normal(0.0004, 0.002, n)
    close = 100.0 * np.exp(np.cumsum(ret))
    open_ = np.r_[close[0], close[:-1]]
    width = close * rng.uniform(0.001, 0.003, n)
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq=freq, tz="UTC"),
        "open": open_,
        "high": np.maximum(open_, close) + width,
        "low": np.minimum(open_, close) - width,
        "close": close,
        "volume": rng.lognormal(7.0, 0.2, n),
    })


class FakeMarket:
    def __init__(self):
        self.one = _bars(520, "1h", 1)
        self.four = _bars(260, "4h", 2)

    def klines(self, symbol: str, period: str, bars: int) -> pd.DataFrame:
        if period == "1hour":
            return self.one.tail(bars).reset_index(drop=True)
        if period == "4hour":
            return self.four.tail(bars).reset_index(drop=True)
        raise ValueError(period)


def test_confluence_never_authorizes_execution():
    row = {
        "smc_structure_state": 1,
        "smc_bos_bull": 1,
        "ict_sweep_bull": 1,
        "brooks_always_in": 1,
        "brooks_market_trend": 1,
        "brooks_bull_signal_quality": 0.9,
        "ichi_tk_bullish": 1,
        "ichi_price_above_visible_cloud": 1,
        "ichi_projected_cloud_bullish": 1,
        "4h_smc_structure_state": 1,
        "4h_brooks_always_in": 1,
        "4h_ichi_projected_cloud_bullish": 1,
    }
    out = decide_confluence_v53(row, ConfluenceConfigV53(minimum_family_quorum=3, minimum_score=0.67))
    assert out.action == "BUY_CANDIDATE"
    assert out.execution_authorized is False
    assert out.long_score > out.short_score


def test_confluence_abstains_without_family_quorum():
    row = {
        "smc_structure_state": 1,
        "smc_bos_bull": 1,
        "ict_sweep_bull": 1,
        "brooks_always_in": 0,
        "brooks_market_trend": 0,
        "brooks_bull_signal_quality": 0.0,
        "ichi_tk_bullish": 0,
        "ichi_price_above_visible_cloud": 0,
        "ichi_projected_cloud_bullish": 0,
        "4h_smc_structure_state": 0,
        "4h_brooks_always_in": 0,
        "4h_ichi_projected_cloud_bullish": 0,
    }
    out = decide_confluence_v53(row)
    assert out.action == "NO_TRADE"
    assert out.execution_authorized is False


def test_real_data_observer_path_is_observation_only():
    market = FakeMarket()
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    result = observe_multiframe_symbol_v53(
        "BTC/USDT",
        now=now,
        config=ForwardMultiframeConfigV53(),
        market_client=market,
    )
    assert result["status"] == "RESEARCH_OBSERVED"
    assert result["execution_authorized"] is False
    assert result["paper_execution"] is False
    assert result["live_execution"] is False
    assert result["decision"]["execution_authorized"] is False


def test_observer_fails_closed_when_not_enough_closed_bars():
    class ShortMarket(FakeMarket):
        def __init__(self):
            self.one = _bars(20, "1h", 3)
            self.four = _bars(20, "4h", 4)

    result = observe_multiframe_symbol_v53(
        "BTC/USDT",
        now=datetime(2026, 1, 10, tzinfo=timezone.utc),
        config=ForwardMultiframeConfigV53(decision_bars=20, higher_bars=20),
        market_client=ShortMarket(),
    )
    assert result["status"] == "INSUFFICIENT_CLOSED_BARS"
    assert result["execution_authorized"] is False
