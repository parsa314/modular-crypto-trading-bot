from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest
import pandas as pd

from research_bot.coinex_depth import DepthSnapshot
from research_bot.forward_paper_v14 import ForwardPaperConfig, ForwardPaperRunner, STRATEGY_VERSION
from research_bot.persistence import MemoryPaperStore, PaperAccount, PaperPosition


def bullish_bars(n=180):
    ts = pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC")
    base = np.linspace(100.0, 170.0, n) + 0.5 * np.sin(np.arange(n) / 4)
    return pd.DataFrame(
        {
            "timestamp": ts,
            "open": base - 0.2,
            "high": base + 0.8,
            "low": base - 0.8,
            "close": base,
            "volume": np.linspace(1000, 1500, n),
            "value": np.linspace(100000, 200000, n),
            "market": "TESTUSDT",
        }
    )


class FakeMarket:
    def __init__(self, frame, *, depth_timestamp, spread_bps=4.0, book_mid=None, bid_depth=100.0, ask_depth=100.0):
        self.frame = frame
        self.spread_bps = spread_bps
        self.depth_timestamp = depth_timestamp
        self.book_mid = float(book_mid) if book_mid is not None else float(frame["close"].iloc[-1])
        self.bid_depth = float(bid_depth)
        self.ask_depth = float(ask_depth)

    def klines(self, symbol, period, bars):
        return self.frame.tail(bars).copy()

    def depth(self, symbol):
        px = self.book_mid
        half = self.spread_bps / 20000.0
        bid = px * (1 - half)
        ask = px * (1 + half)
        return DepthSnapshot(
            symbol=symbol,
            timestamp=self.depth_timestamp,
            best_bid=bid,
            best_ask=ask,
            mid=(bid + ask) / 2,
            spread_bps=self.spread_bps,
            bid_depth=self.bid_depth,
            ask_depth=self.ask_depth,
            imbalance=0.1,
        )


def test_forward_paper_executes_once_per_closed_bar_and_stays_paper_only():
    frame = bullish_bars()
    now = pd.Timestamp(frame["timestamp"].iloc[-1]).to_pydatetime() + timedelta(hours=5)
    store = MemoryPaperStore(10_000.0)
    cfg = ForwardPaperConfig(symbols=("BTC/USDT",), entry_rule_score=0.8)
    runner = ForwardPaperRunner(
        store,
        config=cfg,
        market_client=FakeMarket(frame, depth_timestamp=now),
        paper_execution_enabled=True,
    )
    first = runner.run_cycle(now=now)
    assert first["live_execution"] is False
    assert first["results"][0]["status"] == "EXECUTED_PAPER"
    assert first["results"][0]["fill"]["mode"] == "PAPER"
    assert store.summary()["fills"] == 1
    assert store.get_position("BTC/USDT").quantity > 0
    second = runner.run_cycle(now=now)
    assert second["results"][0]["status"] in {"ALREADY_OBSERVED_BAR", "ALREADY_SETTLED_ORDER"}
    assert store.summary()["fills"] == 1


def test_forward_paper_risk_rejects_wide_spread():
    frame = bullish_bars()
    now = pd.Timestamp(frame["timestamp"].iloc[-1]).to_pydatetime() + timedelta(hours=5)
    store = MemoryPaperStore(10_000.0)
    cfg = ForwardPaperConfig(symbols=("BTC/USDT",), entry_rule_score=0.8, max_spread_bps=35.0)
    runner = ForwardPaperRunner(
        store,
        config=cfg,
        market_client=FakeMarket(frame, depth_timestamp=now, spread_bps=120.0),
        paper_execution_enabled=True,
    )
    out = runner.run_cycle(now=now)
    assert out["results"][0]["status"] == "RISK_REJECTED"
    assert store.summary()["fills"] == 0


def test_forward_paper_rejects_stale_quote_before_execution():
    frame = bullish_bars()
    now = pd.Timestamp(frame["timestamp"].iloc[-1]).to_pydatetime() + timedelta(hours=5)
    stale = now - timedelta(minutes=5)
    store = MemoryPaperStore(10_000.0)
    runner = ForwardPaperRunner(
        store,
        config=ForwardPaperConfig(symbols=("BTC/USDT",), max_quote_age_seconds=30.0),
        market_client=FakeMarket(frame, depth_timestamp=stale),
        paper_execution_enabled=True,
    )
    out = runner.run_cycle(now=now)
    assert out["results"][0]["status"] == "STALE_OR_INVALID_QUOTE"
    assert store.summary()["fills"] == 0


def test_fill_uses_live_executable_quote_not_old_candle_close():
    frame = bullish_bars()
    now = pd.Timestamp(frame["timestamp"].iloc[-1]).to_pydatetime() + timedelta(hours=5)
    candle_close = float(frame["close"].iloc[-1])
    store = MemoryPaperStore(10_000.0)
    market = FakeMarket(frame, depth_timestamp=now, spread_bps=4.0, book_mid=candle_close * 1.10)
    runner = ForwardPaperRunner(
        store,
        config=ForwardPaperConfig(symbols=("BTC/USDT",), entry_rule_score=0.8),
        market_client=market,
        paper_execution_enabled=True,
    )
    out = runner.run_cycle(now=now)["results"][0]
    assert out["status"] == "EXECUTED_PAPER"
    expected_ask = market.book_mid * (1 + market.spread_bps / 20000.0)
    expected_fill = expected_ask * (1 + runner.config.slippage_bps / 10000.0)
    assert abs(out["fill"]["fill_price"] - expected_fill) < 1e-9
    assert abs(out["fill"]["fill_price"] - candle_close) > candle_close * 0.05


def test_restart_recovers_observed_but_unsettled_actionable_bar():
    frame = bullish_bars()
    now = pd.Timestamp(frame["timestamp"].iloc[-1]).to_pydatetime() + timedelta(hours=5)
    bar_ts = pd.Timestamp(frame["timestamp"].iloc[-1]).to_pydatetime()
    store = MemoryPaperStore(10_000.0)
    # Simulate a crash after the observation was committed but before any fill
    # or account/position mutation occurred.
    assert store.record_observation(
        {
            "bar_timestamp": bar_ts.isoformat(),
            "symbol": "BTC/USDT",
            "strategy_version": STRATEGY_VERSION,
        }
    )
    runner = ForwardPaperRunner(
        store,
        config=ForwardPaperConfig(symbols=("BTC/USDT",), entry_rule_score=0.8),
        market_client=FakeMarket(frame, depth_timestamp=now),
        paper_execution_enabled=True,
    )
    result = runner.run_cycle(now=now)["results"][0]
    assert result["status"] == "EXECUTED_PAPER"
    assert result["recovery_attempt"] is True
    assert store.summary()["fills"] == 1
    assert store.get_position("BTC/USDT").quantity > 0


def test_forward_paper_defaults_to_observation_only():
    frame = bullish_bars()
    now = pd.Timestamp(frame["timestamp"].iloc[-1]).to_pydatetime() + timedelta(hours=5)
    store = MemoryPaperStore(10_000.0)
    runner = ForwardPaperRunner(store, market_client=FakeMarket(frame, depth_timestamp=now))
    out = runner.run_cycle(now=now)
    assert out["paper_execution_enabled"] is False
    assert out["results"][0]["status"] == "OBSERVED_ONLY"
    assert store.summary()["fills"] == 0


def test_zero_visible_depth_never_creates_a_phantom_fill():
    frame = bullish_bars()
    now = pd.Timestamp(frame["timestamp"].iloc[-1]).to_pydatetime() + timedelta(hours=5)
    store = MemoryPaperStore(10_000.0)
    runner = ForwardPaperRunner(
        store,
        config=ForwardPaperConfig(symbols=("BTC/USDT",), entry_rule_score=0.8),
        market_client=FakeMarket(frame, depth_timestamp=now, ask_depth=0.0),
        paper_execution_enabled=True,
    )
    result = runner.run_cycle(now=now)["results"][0]
    assert result["status"] == "NO_LIQUIDITY"
    assert result["fill"]["filled_quantity"] == 0.0
    assert store.get_position("BTC/USDT").quantity == 0.0


def test_risk_reducing_exit_is_not_blocked_by_drawdown_gate():
    frame = bullish_bars().copy()
    frame[["open", "high", "low", "close"]] = frame[["open", "high", "low", "close"]].iloc[::-1].to_numpy()
    now = pd.Timestamp(frame["timestamp"].iloc[-1]).to_pydatetime() + timedelta(hours=5)
    store = MemoryPaperStore(8_000.0)
    store.set_account(PaperAccount(cash=8_000.0, equity=8_100.0, peak_equity=10_000.0))
    store.set_position(PaperPosition(symbol="BTC/USDT", quantity=1.0, avg_price=150.0))
    runner = ForwardPaperRunner(
        store,
        config=ForwardPaperConfig(symbols=("BTC/USDT",), entry_rule_score=0.8),
        market_client=FakeMarket(frame, depth_timestamp=now),
        paper_execution_enabled=True,
    )
    result = runner.run_cycle(now=now)["results"][0]
    assert result["signal"]["action"] == "EXIT"
    assert result["status"] == "EXECUTED_PAPER"
    assert store.get_position("BTC/USDT").quantity == 0.0


def test_large_exit_is_reduced_in_safe_notional_chunks():
    frame = bullish_bars().copy()
    frame[["open", "high", "low", "close"]] = frame[["open", "high", "low", "close"]].iloc[::-1].to_numpy()
    now = pd.Timestamp(frame["timestamp"].iloc[-1]).to_pydatetime() + timedelta(hours=5)
    store = MemoryPaperStore(5_000.0)
    store.set_position(PaperPosition(symbol="BTC/USDT", quantity=50.0, avg_price=150.0))
    cfg = ForwardPaperConfig(symbols=("BTC/USDT",), max_order_notional=2_000.0)
    runner = ForwardPaperRunner(
        store,
        config=cfg,
        market_client=FakeMarket(frame, depth_timestamp=now),
        paper_execution_enabled=True,
    )
    result = runner.run_cycle(now=now)["results"][0]
    assert result["status"] == "EXECUTED_PAPER"
    assert result["fill"]["requested_quantity"] * result["fill"]["fill_price"] <= 2_000.0 * 1.001
    assert 0.0 < store.get_position("BTC/USDT").quantity < 50.0



@pytest.mark.parametrize(
    "override",
    [
        {"risk_fraction": 0.005},
        {"max_drawdown": 0.12},
        {"max_gross_exposure": 1.0},
        {"max_asset_weight": 0.40},
        {"risk_fraction": float("nan")},
    ],
)
def test_forward_paper_config_cannot_weaken_risk_constitution(override):
    with pytest.raises(ValueError, match="PAPER_RISK_CONSTITUTION_BREACH"):
        ForwardPaperConfig(**override)


def test_forward_paper_runner_uses_constitution_limits_not_old_one_hundred_pct_gross():
    runner = ForwardPaperRunner(MemoryPaperStore(10_000.0))
    assert runner.config.risk_fraction == 0.0025
    assert runner.risk_engine.limits.max_drawdown == 0.05
    assert runner.risk_engine.limits.max_gross_exposure == 0.70
    assert runner.risk_engine.limits.max_asset_weight <= 0.35
    from research_bot.risk import RiskSnapshot
    risk = runner.risk_engine.evaluate(RiskSnapshot(
        equity=10_000.0,
        peak_equity=10_000.0,
        gross_exposure=0.71,
        asset_weight=0.20,
        turnover=0.10,
        spread_bps=4.0,
        slippage_bps=4.0,
    ))
    assert risk.approved is False
    assert risk.kill_switch is True
    assert "MAX_GROSS_EXPOSURE_BREACH" in risk.reasons


def test_forward_paper_risk_sizing_includes_estimated_round_trip_costs():
    frame = bullish_bars()
    now = pd.Timestamp(frame["timestamp"].iloc[-1]).to_pydatetime() + timedelta(hours=5)
    store = MemoryPaperStore(10_000.0)
    runner = ForwardPaperRunner(
        store,
        config=ForwardPaperConfig(symbols=("BTC/USDT",)),
        market_client=FakeMarket(frame, depth_timestamp=now, spread_bps=4.0),
        paper_execution_enabled=True,
    )
    result = runner.run_cycle(now=now)["results"][0]
    assert result["status"] == "EXECUTED_PAPER"
    from research_bot.forward_paper_v14 import _atr_pct
    stop = max(
        runner.config.min_stop_fraction,
        runner.config.atr_stop_multiple * _atr_pct(frame),
    )
    round_trip_cost = (2 * (runner.config.fee_bps + runner.config.slippage_bps) + 4.0) / 10000.0
    notional = result["fill"]["requested_quantity"] * result["fill"]["fill_price"]
    # The reference quote and executed fill differ slightly; use proposed
    # notional as a strict-upper sizing bound with small slippage allowance.
    assert notional <= (10_000.0 * 0.0025 / (stop + round_trip_cost)) * 1.001
    assert result["fill"]["mode"] == "PAPER"
