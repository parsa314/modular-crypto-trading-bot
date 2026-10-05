from __future__ import annotations

from datetime import datetime, timezone

import pytest

from research_bot.execution_adapters.mt5_demo import MT5DemoExecutionResult
from research_bot.tradingview_mt5_bridge import (
    TradingViewAuthError,
    TradingViewDuplicateError,
    TradingViewMT5Bridge,
    TradingViewPayloadError,
    TradingViewSignal,
    TradingViewWebhookJournal,
    deterministic_tv_client_order_id,
    verify_route_token,
)


T0 = datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc)


def payload(**overrides):
    base = {
        "event_id": "BTCUSDT-20261005T140000Z-LONG",
        "symbol": "BTC/USDT",
        "action": "BUY",
        "quantity": 0.01,
        "reference_price": 62000.0,
        "stop_loss": 61000.0,
        "take_profit": 64000.0,
        "event_time": "2026-10-05T14:00:00Z",
        "strategy_version": "TV_TEST_V1",
        "model_version": "champion-001",
        "metadata": {"timeframe": "4h"},
    }
    base.update(overrides)
    return base


class FakeExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, request, *, stop_loss, take_profit=None, now=None):
        self.calls.append(
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
            order_ticket=101,
            deal_ticket=202,
            requested_quantity=request.quantity,
            submitted_lots=0.01,
            filled_lots=0.01,
            filled_base_quantity=0.01,
            decision_reference_price=request.reference_price,
            executable_price=62001.0,
            fill_price=62002.0,
            implementation_shortfall_bps=0.161,
            account_login=314590,
            account_server="UnitTest-Demo",
            account_trade_mode=0,
            contract_size=1.0,
            timestamp=T0,
            comment="Done",
        )


def test_route_token_uses_exact_constant_time_semantics():
    token = "a" * 32
    verify_route_token(token, token)

    with pytest.raises(TradingViewAuthError):
        verify_route_token(token, "b" * 32)

    with pytest.raises(TradingViewAuthError):
        verify_route_token("short", "short")


def test_signal_payload_parses_and_normalizes():
    signal = TradingViewSignal.from_payload(payload())
    assert signal.action == "BUY"
    assert signal.canonical_symbol == "BTC/USDT"
    assert signal.event_time == T0
    assert signal.metadata == {"timeframe": "4h"}


def test_invalid_or_unsupported_payload_fails_closed():
    with pytest.raises(TradingViewPayloadError, match="missing field"):
        TradingViewSignal.from_payload(payload(stop_loss=""))

    with pytest.raises(TradingViewPayloadError, match="BUY or SELL"):
        TradingViewSignal.from_payload(payload(action="CLOSE"))

    with pytest.raises(TradingViewPayloadError, match="ISO-8601"):
        TradingViewSignal.from_payload(payload(event_time="not-a-time"))


def test_client_order_id_is_deterministic_and_bounded():
    signal = TradingViewSignal.from_payload(payload())
    first = deterministic_tv_client_order_id(signal)
    second = deterministic_tv_client_order_id(signal)
    assert first == second
    assert first.startswith("tv-")
    assert len(first) <= 32


def test_journal_blocks_duplicate_event_after_restart(tmp_path):
    path = tmp_path / "tv.jsonl"
    signal = TradingViewSignal.from_payload(payload())

    first = TradingViewWebhookJournal(path)
    first.accept_once(signal, received_at=T0)

    restarted = TradingViewWebhookJournal(path)
    with pytest.raises(TradingViewDuplicateError):
        restarted.accept_once(signal, received_at=T0)


def test_bridge_converts_webhook_to_execution_request_and_records_result(tmp_path):
    executor = FakeExecutor()
    journal = TradingViewWebhookJournal(tmp_path / "tv.jsonl")
    bridge = TradingViewMT5Bridge(executor=executor, journal=journal)

    signal = bridge.accept(payload(), received_at=T0)
    result = bridge.execute(signal)

    assert result.status == "FILLED_DEMO"
    assert len(executor.calls) == 1
    call = executor.calls[0]
    assert call["request"].symbol == "BTC/USDT"
    assert call["request"].quantity == 0.01
    assert call["request"].reference_price == 62000.0
    assert call["stop_loss"] == 61000.0
    assert call["take_profit"] == 64000.0

    rows = journal.latest(10)
    assert [row["record_type"] for row in rows] == [
        "RECEIVED",
        "EXECUTION_RESULT",
    ]
    assert rows[-1]["event_id"] == signal.event_id
