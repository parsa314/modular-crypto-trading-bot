from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from research_bot.execution_adapters.mt5_demo import (
    MT5DemoCloseResult,
    MT5DemoExecutionResult,
)
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
        self.close_calls = []
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


    def close_bot_position(self, *, ticket, venue_symbol, now=None):
        self.close_calls.append(
            {"ticket": ticket, "venue_symbol": venue_symbol, "now": now}
        )
        self._positions = [
            p for p in self._positions if int(p.get("ticket", 0)) != int(ticket)
        ]
        return MT5DemoCloseResult(
            ticket=int(ticket),
            venue_symbol=venue_symbol,
            closed_side="SELL",
            status="CLOSED_DEMO",
            retcode=10009,
            order_ticket=3003,
            deal_ticket=4004,
            requested_lots=0.01,
            filled_lots=0.01,
            executable_price=60_999.0,
            fill_price=60_999.0,
            account_login=1,
            account_server="UnitTest-Demo",
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
    executor._positions = [{
        "ticket": 77,
        "time": int((T0 - timedelta(hours=8)).timestamp()),
    }]
    runner = DirectMT5StrategyRunner(executor, config(tmp_path))

    outcome = runner.evaluate_once()

    assert outcome.status == "POSITION_EXISTS"
    assert outcome.side == "SELL"
    assert executor.executions == []


def test_ai_gate_rejection_blocks_demo_order(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "research_bot.mt5_direct_strategy.generate_direction",
        fake_signal(1),
    )
    monkeypatch.setattr(
        "research_bot.mt5_direct_strategy.evaluate_ai_confirmation",
        lambda *args, **kwargs: SimpleNamespace(
            approved=False,
            probability_up=0.53,
            probability_down=None,
            probability_favorable=0.53,
            confidence=0.06,
            validation_brier=0.24,
            reason="AI_REJECTS_LONG",
        ),
    )
    executor = FakeExecutor(submission_enabled=True)
    cfg = MT5DirectStrategyConfig(
        canonical_symbol="BTC/USDT",
        venue_symbol="BTCUSD",
        strategy_name="H4_V59_CONFLUENCE_DEMO",
        bars=400,
        risk_fraction=0.0025,
        journal_path=str(tmp_path / "ai-reject.jsonl"),
        ai_gate_enabled=True,
    )

    outcome = DirectMT5StrategyRunner(executor, cfg).evaluate_once()

    assert outcome.status == "AI_REJECTED"
    assert outcome.ai_gate_enabled is True
    assert outcome.ai_probability_up == pytest.approx(0.53)
    assert outcome.ai_validation_brier == pytest.approx(0.24)
    assert executor.executions == []


def test_ai_gate_confirmation_allows_demo_execution(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "research_bot.mt5_direct_strategy.generate_direction",
        fake_signal(1),
    )
    monkeypatch.setattr(
        "research_bot.mt5_direct_strategy.evaluate_ai_confirmation",
        lambda *args, **kwargs: SimpleNamespace(
            approved=True,
            probability_up=0.71,
            probability_down=None,
            probability_favorable=0.71,
            confidence=0.42,
            validation_brier=0.21,
            reason="AI_CONFIRMS_LONG",
        ),
    )
    executor = FakeExecutor(submission_enabled=True)
    cfg = MT5DirectStrategyConfig(
        canonical_symbol="BTC/USDT",
        venue_symbol="BTCUSD",
        strategy_name="H4_V59_CONFLUENCE_DEMO",
        bars=400,
        risk_fraction=0.0025,
        journal_path=str(tmp_path / "ai-confirm.jsonl"),
        ai_gate_enabled=True,
    )

    outcome = DirectMT5StrategyRunner(executor, cfg).evaluate_once()

    assert outcome.status == "EXECUTED_MT5_DEMO"
    assert outcome.ai_gate_enabled is True
    assert outcome.ai_probability_up == pytest.approx(0.71)
    assert outcome.ai_reason == "AI_CONFIRMS_LONG"
    assert len(executor.executions) == 1


def test_direct_strategy_timeout_is_reported_in_dry_run(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "research_bot.mt5_direct_strategy.generate_direction",
        fake_signal(0),
    )
    executor = FakeExecutor(submission_enabled=False)
    executor._positions = [{
        "ticket": 501,
        "time": int((T0 - timedelta(hours=4 * 40)).timestamp()),
    }]
    runner = DirectMT5StrategyRunner(executor, config(tmp_path))

    outcome = runner.evaluate_once()

    assert outcome.status == "TIMEOUT_READY_DRY_RUN"
    assert outcome.position_ticket == 501
    assert outcome.held_bars >= 30
    assert executor.close_calls == []


def test_direct_strategy_timeout_closes_exact_ticket(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "research_bot.mt5_direct_strategy.generate_direction",
        fake_signal(0),
    )
    executor = FakeExecutor(submission_enabled=True)
    executor._positions = [{
        "ticket": 502,
        "time": int((T0 - timedelta(hours=4 * 40)).timestamp()),
    }]
    runner = DirectMT5StrategyRunner(executor, config(tmp_path))

    outcome = runner.evaluate_once()

    assert outcome.status == "TIMEOUT_CLOSED_MT5_DEMO"
    assert outcome.position_ticket == 502
    assert outcome.close_status == "CLOSED_DEMO"
    assert outcome.order_ticket == 3003
    assert outcome.deal_ticket == 4004
    assert executor.close_calls[0]["ticket"] == 502
