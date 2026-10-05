from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from research_bot.execution_adapters.mt5_demo import MT5DemoExecutionResult
from research_bot.mt5_direct_strategy import (
    DirectMT5StrategyRunner,
    DirectStrategyJournal,
    MT5DirectStrategyConfig,
)


T0 = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


class FakeExecutor:
    def __init__(self, *, submission_enabled=False):
        self.connected = True
        self.submission_enabled = submission_enabled
        self.config = SimpleNamespace(
            max_spread_bps=35.0,
            max_order_notional=5_000.0,
            allowed_symbols=("BTC/USDT",),
            symbol_map={"BTC/USDT": "BTCUSD"},
        )
        self.executions = []
        self._positions = []

    def copy_closed_bars(self, venue_symbol, timeframe, *, count=500):
        assert venue_symbol == "BTCUSD"
        assert timeframe == "4h"
        rows = []
        start = int((T0 - timedelta(hours=4 * count)).timestamp())
        for i in range(count):
            close = 60_000.0 + i
            rows.append(
                {
                    "time": start + i * 4 * 3600,
                    "open": close - 10.0,
                    "high": close + 50.0,
                    "low": close - 50.0,
                    "close": close,
                    "tick_volume": 1000.0,
                    "spread": 10.0,
                    "real_volume": 0.0,
                }
            )
        return rows

    def bot_positions(self, venue_symbol):
        assert venue_symbol == "BTCUSD"
        return list(self._positions)

    def market_snapshot(self, venue_symbol):
        assert venue_symbol == "BTCUSD"
        return {
            "bid": 60_999.0,
            "ask": 61_001.0,
            "mid": 61_000.0,
            "spread_bps": 0.327868852,
        }

    def account_summary(self):
        return {
            "connected": True,
            "equity": 100_000.0,
            "balance": 100_000.0,
            "currency": "USD",
        }

    def execute(self, request, *, stop_loss, take_profit=None, now=None):
        self.executions.append(
            {
                "request": request,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "now": now,
            }
        )
        return MT5DemoExecutionResult(
            client_order_id=request.client_order_id,
            canonical_symbol=request.symbol,
            venue_symbol="BTCUSD",
            side=request.side.value,
            status="FILLED_DEMO",
            retcode=10009,
            order_ticket=1001,
            deal_ticket=2002,
            requested_quantity=request.quantity,
            submitted_lots=0.01,
            filled_lots=0.01,
            filled_base_quantity=0.01,
            decision_reference_price=request.reference_price,
            executable_price=request.reference_price,
            fill_price=request.reference_price,
            implementation_shortfall_bps=0.0,
            account_login=1,
            account_server="UnitTest-Demo",
            account_trade_mode=0,
            contract_size=1.0,
            timestamp=now or T0,
            comment="Done",
        )


def config(tmp_path):
    return MT5DirectStrategyConfig(
        canonical_symbol="BTC/USDT",
        venue_symbol="BTCUSD",
        strategy_name="H4_S6_BREAKOUT",
        bars=300,
        risk_fraction=0.0025,
        journal_path=str(tmp_path / "direct.jsonl"),
    )


def fake_signal(direction_value):
    def _impl(spec, frame):
        n = len(frame)
        direction = pd.Series([0] * n, dtype="int8")
        direction.iloc[-1] = direction_value
        features = frame.copy()
        features["atr"] = 500.0
        return direction, features

    return _impl


def test_direct_strategy_dry_run_sizes_and_builds_bracket(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "research_bot.mt5_direct_strategy.generate_direction",
        fake_signal(1),
    )
    executor = FakeExecutor(submission_enabled=False)
    runner = DirectMT5StrategyRunner(executor, config(tmp_path))

    outcome = runner.evaluate_once()

    assert outcome.status == "READY_DRY_RUN"
    assert outcome.side == "BUY"
    assert outcome.stop_loss < outcome.reference_price < outcome.take_profit
    assert outcome.requested_notional <= 5_000.0
    assert outcome.risk_fraction == pytest.approx(0.0025)
    assert executor.executions == []


def test_direct_strategy_executes_demo_and_persists_idempotency(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "research_bot.mt5_direct_strategy.generate_direction",
        fake_signal(1),
    )
    executor = FakeExecutor(submission_enabled=True)
    cfg = config(tmp_path)
    runner = DirectMT5StrategyRunner(executor, cfg)

    first = runner.evaluate_once()
    assert first.status == "EXECUTED_MT5_DEMO"
    assert first.order_ticket == 1001
    assert len(executor.executions) == 1

    # Rebuild runner/journal to simulate a process restart with no open position.
    restarted = DirectMT5StrategyRunner(
        executor,
        cfg,
        journal=DirectStrategyJournal(cfg.journal_path),
    )
    second = restarted.evaluate_once()

    assert second.status == "DUPLICATE_SIGNAL"
    assert len(executor.executions) == 1


def test_direct_strategy_no_signal_does_not_touch_execution(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "research_bot.mt5_direct_strategy.generate_direction",
        fake_signal(0),
    )
    executor = FakeExecutor(submission_enabled=True)
    runner = DirectMT5StrategyRunner(executor, config(tmp_path))

    outcome = runner.evaluate_once()

    assert outcome.status == "NO_SIGNAL"
    assert executor.executions == []


def test_direct_strategy_blocks_when_bot_position_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "research_bot.mt5_direct_strategy.generate_direction",
        fake_signal(-1),
    )
    executor = FakeExecutor(submission_enabled=True)
    executor._positions = [{"ticket": 77}]
    runner = DirectMT5StrategyRunner(executor, config(tmp_path))

    outcome = runner.evaluate_once()

    assert outcome.status == "POSITION_EXISTS"
    assert outcome.side == "SELL"
    assert executor.executions == []
