from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from research_bot.execution import ExecutionRequest, OrderSide
from research_bot.execution_adapters.mt5_demo import (
    MT5DemoConfig,
    MT5DemoExecutor,
    MT5DemoSafetyError,
)


NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


class FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO = 0

    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_RETURN = 2

    TRADE_RETCODE_PLACED = 10008
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_DONE_PARTIAL = 10010

    def __init__(
        self,
        *,
        trade_mode=0,
        tick_time=NOW,
        bid=99.95,
        ask=100.05,
        contract_size=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
    ):
        self._account = SimpleNamespace(
            login=314590,
            server="UnitTest-Demo",
            trade_mode=trade_mode,
            trade_allowed=True,
            trade_expert=True,
        )
        self._tick = SimpleNamespace(
            bid=bid,
            ask=ask,
            time=int(tick_time.timestamp()),
            time_msc=int(tick_time.timestamp() * 1000),
        )
        self._symbol = SimpleNamespace(
            visible=True,
            trade_contract_size=contract_size,
            volume_min=volume_min,
            volume_max=volume_max,
            volume_step=volume_step,
            point=0.01,
            trade_stops_level=0,
        )
        self.initialized = False
        self.shutdown_called = False
        self.checked_payload = None
        self.sent_payload = None

    def initialize(self, *args, **kwargs):
        self.initialized = True
        return True

    def login(self, *args, **kwargs):
        return True

    def shutdown(self):
        self.shutdown_called = True
        return True

    def last_error(self):
        return (0, "ok")

    def account_info(self):
        return self._account

    def symbol_info(self, symbol):
        return self._symbol if symbol == "BTCUSD" else None

    def symbol_select(self, symbol, selected):
        return symbol == "BTCUSD" and selected

    def symbol_info_tick(self, symbol):
        return self._tick if symbol == "BTCUSD" else None

    def order_check(self, payload):
        self.checked_payload = dict(payload)
        return SimpleNamespace(retcode=0, comment="Done")

    def order_send(self, payload):
        self.sent_payload = dict(payload)
        return SimpleNamespace(
            retcode=self.TRADE_RETCODE_DONE,
            order=12345,
            deal=67890,
            volume=payload["volume"],
            price=payload["price"] + 0.01,
            comment="Done",
        )


def config(**kwargs):
    values = dict(
        allowed_symbols=("BTC/USDT",),
        symbol_map={"BTC/USDT": "BTCUSD"},
        max_order_notional=5_000.0,
        max_spread_bps=35.0,
        max_tick_age_seconds=30.0,
        submit_enabled=True,
    )
    values.update(kwargs)
    return MT5DemoConfig(**values)


def request(*, client_order_id="mbot-test-1", quantity=10.0, price=100.0):
    return ExecutionRequest(
        client_order_id=client_order_id,
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        quantity=quantity,
        reference_price=price,
        created_at=NOW,
    )


def test_real_account_is_refused_at_connect():
    mt5 = FakeMT5(trade_mode=2)
    executor = MT5DemoExecutor(config(), mt5_module=mt5)

    with pytest.raises(MT5DemoSafetyError, match="REAL_OR_NON_DEMO_ACCOUNT_REFUSED"):
        executor.connect()

    assert mt5.shutdown_called is True


def test_submission_is_disabled_unless_explicitly_enabled():
    mt5 = FakeMT5()
    executor = MT5DemoExecutor(config(submit_enabled=False), mt5_module=mt5)
    executor.connect()

    with pytest.raises(MT5DemoSafetyError, match="MT5_DEMO_SUBMISSION_DISABLED"):
        executor.execute(request(), stop_loss=95.0, take_profit=110.0, now=NOW)


def test_stale_tick_is_refused_before_order_check():
    mt5 = FakeMT5(tick_time=NOW - timedelta(seconds=31))
    executor = MT5DemoExecutor(config(), mt5_module=mt5)
    executor.connect()

    with pytest.raises(MT5DemoSafetyError, match="STALE_MT5_TICK"):
        executor.execute(request(), stop_loss=95.0, take_profit=110.0, now=NOW)

    assert mt5.checked_payload is None
    assert mt5.sent_payload is None


def test_server_side_stop_is_required_and_direction_checked():
    mt5 = FakeMT5()
    executor = MT5DemoExecutor(config(), mt5_module=mt5)
    executor.connect()

    with pytest.raises(MT5DemoSafetyError, match="SERVER_SIDE_STOP_REQUIRED"):
        executor.execute(request(), stop_loss=None, take_profit=110.0, now=NOW)

    with pytest.raises(MT5DemoSafetyError, match="BUY_STOP_MUST_BE_BELOW"):
        executor.execute(request(), stop_loss=101.0, take_profit=110.0, now=NOW)


def test_max_notional_and_symbol_allowlist_fail_closed():
    mt5 = FakeMT5()
    executor = MT5DemoExecutor(config(max_order_notional=500.0), mt5_module=mt5)
    executor.connect()

    with pytest.raises(MT5DemoSafetyError, match="MAX_ORDER_NOTIONAL_BREACH"):
        executor.execute(
            request(quantity=10.0, price=100.0),
            stop_loss=95.0,
            take_profit=110.0,
            now=NOW,
        )

    bad = ExecutionRequest(
        client_order_id="mbot-eth",
        symbol="ETH/USDT",
        side=OrderSide.BUY,
        quantity=1.0,
        reference_price=100.0,
        created_at=NOW,
    )
    executor2 = MT5DemoExecutor(config(), mt5_module=FakeMT5())
    executor2.connect()
    with pytest.raises(MT5DemoSafetyError, match="SYMBOL_NOT_ALLOWLISTED"):
        executor2.execute(
            bad,
            stop_loss=95.0,
            take_profit=110.0,
            now=NOW,
        )


def test_demo_market_order_is_checked_sent_and_idempotent_in_process():
    mt5 = FakeMT5()
    executor = MT5DemoExecutor(config(), mt5_module=mt5)
    executor.connect()
    req = request()

    result = executor.execute(
        req,
        stop_loss=95.0,
        take_profit=110.0,
        now=NOW,
    )

    assert result.status == "FILLED_DEMO"
    assert result.account_trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
    assert result.venue_symbol == "BTCUSD"
    assert result.order_ticket == 12345
    assert result.deal_ticket == 67890
    assert mt5.checked_payload == mt5.sent_payload
    assert mt5.sent_payload["sl"] == 95.0
    assert mt5.sent_payload["tp"] == 110.0
    assert mt5.sent_payload["volume"] <= req.notional / (
        mt5.sent_payload["price"] * mt5._symbol.trade_contract_size
    ) + 1e-12

    with pytest.raises(MT5DemoSafetyError, match="DUPLICATE_CLIENT_ORDER_ID"):
        executor.execute(
            req,
            stop_loss=95.0,
            take_profit=110.0,
            now=NOW,
        )


def test_runtime_submission_toggle_requires_connected_demo_account():
    mt5 = FakeMT5()
    executor = MT5DemoExecutor(config(submit_enabled=False), mt5_module=mt5)

    with pytest.raises(MT5DemoSafetyError, match="MT5_DEMO_NOT_CONNECTED"):
        executor.set_demo_submission_enabled(True)

    executor.connect()
    assert executor.submission_enabled is False
    executor.set_demo_submission_enabled(True)
    assert executor.submission_enabled is True
    executor.set_demo_submission_enabled(False)
    assert executor.submission_enabled is False


def test_account_summary_and_symbol_search_are_ui_safe():
    mt5 = FakeMT5()
    mt5._account.balance = 10000.0
    mt5._account.equity = 10050.0
    mt5._account.margin = 100.0
    mt5._account.margin_free = 9950.0
    mt5._account.currency = "USD"
    mt5._account.company = "UnitTest Broker"
    mt5.symbols_get = lambda: [
        SimpleNamespace(name="BTCUSD"),
        SimpleNamespace(name="ETHUSD"),
        SimpleNamespace(name="EURUSD"),
    ]

    executor = MT5DemoExecutor(config(submit_enabled=False), mt5_module=mt5)
    executor.connect()

    summary = executor.account_summary()
    assert summary["connected"] is True
    assert summary["login"] == 314590
    assert summary["server"] == "UnitTest-Demo"
    assert summary["balance"] == 10000.0
    assert "password" not in summary

    assert executor.search_symbols("USD", limit=2) == ["BTCUSD", "ETHUSD"]
    assert executor.search_symbols("BTC") == ["BTCUSD"]


def test_closed_bar_fetch_excludes_forming_bar_and_normalizes_rows():
    mt5 = FakeMT5()
    mt5.TIMEFRAME_H4 = 240
    calls = []

    class Rate:
        def __init__(self, t, o, h, l, close, tv, spread, rv):
            self.time = t
            self.open = o
            self.high = h
            self.low = l
            self.close = close
            self.tick_volume = tv
            self.spread = spread
            self.real_volume = rv

    def copy_rates_from_pos(symbol, timeframe, start_pos, count):
        calls.append((symbol, timeframe, start_pos, count))
        return [
            Rate(1, 100, 110, 90, 105, 1000, 5, 0),
            Rate(2, 105, 115, 95, 110, 1200, 6, 0),
        ]

    mt5.copy_rates_from_pos = copy_rates_from_pos
    executor = MT5DemoExecutor(config(submit_enabled=False), mt5_module=mt5)
    executor.connect()

    rows = executor.copy_closed_bars("BTCUSD", "4h", count=2)

    assert calls == [("BTCUSD", 240, 1, 2)]
    assert rows[0]["time"] == 1
    assert rows[0]["close"] == 105.0
    assert rows[1]["tick_volume"] == 1200.0


def test_bot_positions_filter_by_magic_and_market_snapshot():
    mt5 = FakeMT5()
    mt5.positions_get = lambda symbol=None: [
        SimpleNamespace(
            ticket=1,
            symbol="BTCUSD",
            type=0,
            volume=0.01,
            price_open=100.0,
            sl=95.0,
            tp=110.0,
            profit=5.0,
            magic=314590,
        ),
        SimpleNamespace(
            ticket=2,
            symbol="BTCUSD",
            type=0,
            volume=0.01,
            price_open=100.0,
            sl=95.0,
            tp=110.0,
            profit=0.0,
            magic=999,
        ),
    ]
    executor = MT5DemoExecutor(config(submit_enabled=False), mt5_module=mt5)
    executor.connect()

    positions = executor.bot_positions("BTCUSD")
    quote = executor.market_snapshot("BTCUSD")

    assert [p["ticket"] for p in positions] == [1]
    assert quote["bid"] == pytest.approx(99.95)
    assert quote["ask"] == pytest.approx(100.05)
    assert quote["spread_bps"] > 0.0
