"""Genuine MT5Forge with an injected terminal boundary; never real connections."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import sys
import threading
import time
from types import SimpleNamespace as NS

import pytest

if sys.version_info < (3, 12):
    pytest.skip("Reviewed mt5forge 1.0.3 requires Python >=3.12", allow_module_level=True)
pytest.importorskip("mt5forge")

from src.adapters.base import AdapterError, ExecutionRejected
from src.adapters.mt5_broker import MT5Broker


class TerminalBoundary:
    ACCOUNT_TRADE_MODE_DEMO = 0
    TIMEFRAME_H1 = 16385
    ORDER_TYPE_BUY, ORDER_TYPE_SELL, POSITION_TYPE_BUY = 0, 1, 0
    SYMBOL_FILLING_IOC, SYMBOL_FILLING_FOK = 2, 1
    ORDER_FILLING_IOC, ORDER_FILLING_FOK = 1, 0
    TRADE_ACTION_DEAL, ORDER_TIME_GTC = 1, 0

    def __init__(self):
        self.initialized = False
        self.account = NS(login=42, server="Broker-Demo", trade_mode=0, equity=10000.,
                          balance=10000., margin_free=9000., trade_allowed=True, trade_expert=True)
        self.spec = NS(volume_step=.01, volume_min=.01, volume_max=100., point=.00001,
                       digits=5, trade_stops_level=10, filling_mode=2)
        self.initializations, self.logins, self.checks, self.sent = [], [], [], []
        self.reads, self.shutdowns = [], 0
        self.positions = ()
        self.switch_during_read = False
        self.delay = 0.
        self.active, self.max_active = 0, 0
        self.worker_threads = set()
        self.started = threading.Event()

    def account_info(self): return self.account if self.initialized else None
    def terminal_info(self): return NS(connected=self.initialized)
    def initialize(self, *args, **kwargs):
        self.initializations.append((args, kwargs))
        self.initialized = True
        return True
    def login(self, *args, **kwargs):
        self.logins.append((args, kwargs))
        return True
    def shutdown(self):
        self.shutdowns += 1
        self.initialized = False
    def last_error(self): return (0, "secret-value-must-not-leak")
    def symbol_select(self, symbol, visible): return True
    def symbol_info(self, symbol): return self.spec
    def symbol_info_tick(self, symbol): return NS(bid=1.0999, ask=1.1001, time=time.time())
    def _begin(self):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.worker_threads.add(threading.get_ident())
        self.started.set()
        time.sleep(self.delay)
    def copy_rates_from_pos(self, symbol, timeframe, shift, count):
        self._begin()
        self.reads.append((symbol, timeframe, shift, count))
        try:
            if self.switch_during_read:
                self.account.trade_mode = 2
            return [{"time": 1704067200 + i * 3600, "open": 1.1, "high": 1.11,
                     "low": 1.09, "close": 1.105, "tick_volume": 100} for i in range(min(count, 2))]
        finally:
            self.active -= 1
    def positions_get(self):
        self._begin()
        self.active -= 1
        return self.positions
    def order_calc_profit(self, kind, symbol, size, entry, close):
        return (close - entry) * size * 100000 * (1 if kind == 0 else -1)
    def order_calc_margin(self, kind, symbol, size, entry): return size * 1000
    def order_check(self, payload):
        self.checks.append(payload)
        return NS(retcode=0)
    def order_send(self, payload):
        self.sent.append(payload)
        raise AssertionError("Phase 1 must never call order_send")


@pytest.fixture(autouse=True)
def demo_environment(monkeypatch):
    for name in ("MT5_DEMO_LOGIN", "MT5_DEMO_PASSWORD", "MT5_DEMO_SERVER", "MT5_DEMO_TERMINAL_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MT5_DEMO_LOGIN", "42")
    monkeypatch.setenv("MT5_DEMO_PASSWORD", "fake-password-never-log")
    monkeypatch.setenv("MT5_DEMO_SERVER", "Broker-Demo")
    monkeypatch.setenv("MT5_DEMO_TERMINAL_PATH", r"C:\DemoOnly\terminal64.exe")


def test_missing_explicit_demo_configuration_never_initializes(monkeypatch):
    monkeypatch.delenv("MT5_DEMO_LOGIN")
    raw = TerminalBoundary()
    broker = MT5Broker(terminal_backend=raw)
    with pytest.raises(AdapterError, match="default terminal attachment is disabled"):
        asyncio.run(broker.connect())
    assert raw.initializations == [] and raw.logins == [] and raw.sent == []


def test_actual_forge_pins_initialization_and_returns_closed_tick_volume_bars(capsys):
    raw = TerminalBoundary()
    broker = MT5Broker(terminal_backend=raw)

    async def check():
        try:
            connected = await broker.connect()
            assert connected["connected"] and connected["account_type"] == "DEMO"
            assert type(broker._connector).__module__ == "mt5forge.connection.connector"
            assert type(broker._feed).__module__ == "mt5forge.market.data_feed"
            bars = await broker.get_historical_data("EURUSD", "H1", 10)
            assert len(bars) == 2  # Actual short response is never padded.
            assert str(bars.index.tz) == "UTC"
            assert bars.attrs["volume_kind"] == "tick_volume"
            assert raw.reads == [("EURUSD", raw.TIMEFRAME_H1, 1, 10)]
            assert await broker.get_open_positions() == []
        finally:
            await broker.close()

    asyncio.run(check())
    args, settings = raw.initializations[0]
    assert args == (r"C:\DemoOnly\terminal64.exe",)
    assert settings == {"login": 42, "password": "fake-password-never-log", "server": "Broker-Demo", "timeout": 15000}
    assert len(raw.logins) == 1
    assert raw.sent == [] and raw.shutdowns == 1
    captured = capsys.readouterr()
    assert "fake-password" not in captured.out + captured.err
    assert "Broker-Demo" not in captured.out + captured.err


def test_existing_live_terminal_is_not_initialized_or_shut_down():
    raw = TerminalBoundary()
    raw.initialized, raw.account.trade_mode = True, 2
    with pytest.raises(AdapterError, match="could not establish"):
        asyncio.run(MT5Broker(terminal_backend=raw).connect())
    assert raw.initializations == []
    assert raw.logins == [] and raw.shutdowns == 0 and raw.sent == []


@pytest.mark.parametrize("operation", ["history", "positions", "order"])
def test_switch_to_live_rejected_before_every_api_operation(operation):
    raw = TerminalBoundary()
    broker = MT5Broker(terminal_backend=raw)

    async def check():
        try:
            await broker.connect()
            raw.account.trade_mode = 2
            with pytest.raises(ExecutionRejected, match="verification failed"):
                if operation == "history": await broker.get_historical_data("EURUSD", "H1", 2)
                elif operation == "positions": await broker.get_open_positions()
                else: await broker.place_order("EURUSD", "BUY", .01, 1.09, 1.12)
            assert raw.reads == [] and raw.checks == [] and raw.sent == []
        finally:
            await broker.close()
    asyncio.run(check())


def test_switch_during_data_read_discards_result_and_invalidates_session():
    raw = TerminalBoundary()
    broker = MT5Broker(terminal_backend=raw)

    async def check():
        try:
            await broker.connect()
            raw.switch_during_read = True
            with pytest.raises(ExecutionRejected, match="verification failed"):
                await broker.get_historical_data("EURUSD", "H1", 2)
            with pytest.raises(AdapterError, match="disconnected"):
                await broker.get_open_positions()
        finally:
            await broker.close()
    asyncio.run(check())
    assert raw.sent == [] and raw.shutdowns == 1


def test_simulated_order_uses_forge_builder_and_order_check_never_send():
    raw = TerminalBoundary()
    broker = MT5Broker(terminal_backend=raw)

    async def check():
        try:
            await broker.connect()
            checked = await broker.place_order("EURUSD", "BUY", .019, 1.09, 1.12)
            assert checked["status"] == "CHECKED_ONLY"
            assert checked["simulated"] and not checked["submitted"] and not checked["filled"]
            assert checked["volume"] == .01
            assert await broker.get_open_positions() == []
            payload, = raw.checks
            assert (payload["action"], payload["type"], payload["type_filling"]) == (1, 0, raw.ORDER_FILLING_IOC)
            assert payload["sl"] == 1.09 and payload["tp"] == 1.12 and payload["magic"] == 20261017
            for stop, target in ((1.11, 1.12), (1.09, 1.08), (float("nan"), 1.12)):
                with pytest.raises(ExecutionRejected):
                    await broker.place_order("EURUSD", "BUY", .01, stop, target)
            with pytest.raises(ExecutionRejected, match="risk"):
                await broker.place_order("EURUSD", "BUY", 1., 1.09, 1.12)
            assert len(raw.checks) == 1 and raw.sent == []
            with pytest.raises(ExecutionRejected, match="submission is disabled"):
                broker._connector.mt5.order_send(payload)
        finally:
            await broker.close()
    asyncio.run(check())


def test_none_positions_is_not_claimed_flat_and_error_diagnostics_are_redacted():
    raw = TerminalBoundary()
    broker = MT5Broker(terminal_backend=raw)

    async def check():
        try:
            await broker.connect()
            raw.positions = None
            with pytest.raises(AdapterError, match="not treated as flat"):
                await broker.get_open_positions()
            def failed_quote(symbol): raise RuntimeError("fake-password-never-log")
            raw.symbol_info_tick = failed_quote
            with pytest.raises(AdapterError, match="redacted") as error:
                await broker.get_quote("EURUSD")
            assert "fake-password" not in str(error.value)
        finally:
            await broker.close()
    asyncio.run(check())


def test_single_worker_keeps_event_loop_responsive_and_owns_one_session():
    raw = TerminalBoundary()
    raw.delay = .025
    broker, other = MT5Broker(terminal_backend=raw), MT5Broker(terminal_backend=TerminalBoundary())
    main_thread = threading.get_ident()

    async def check():
        beats, done = [], False
        async def heartbeat():
            while not done:
                beats.append(1)
                await asyncio.sleep(.002)
        try:
            await broker.connect()
            with pytest.raises(ExecutionRejected, match="owns"):
                await other.connect()
            beat = asyncio.create_task(heartbeat())
            await asyncio.gather(*(broker.get_historical_data("EURUSD", "H1", 2) if i % 2 else broker.get_open_positions() for i in range(6)))
            done = True
            await beat
            assert len(beats) >= 5 and raw.max_active == 1
            assert len(raw.worker_threads) == 1 and main_thread not in raw.worker_threads
        finally:
            done = True
            await broker.close()
            await other.close()
    asyncio.run(check())


def test_cancelled_read_finishes_before_shutdown_on_the_same_worker():
    raw = TerminalBoundary()
    raw.delay = .05
    broker = MT5Broker(terminal_backend=raw)

    async def check():
        await broker.connect()
        operation = asyncio.create_task(broker.get_historical_data("EURUSD", "H1", 2))
        assert await asyncio.to_thread(raw.started.wait, .5)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError): await operation
        await broker.close()  # Queued behind the actual SDK call, despite cancellation.
        assert raw.active == 0 and raw.max_active == 1 and raw.shutdowns == 1
    asyncio.run(check())


def test_probe_prints_all_returned_candles_and_retains_actual_count(monkeypatch, tmp_path, capsys):
    from scripts import test_mt5_connection as connection_script
    # Fixture-only CLI formatting check; no fabricated result enters results/.
    rows = [{"timestamp": f"2024-01-01T{i // 60:02}:{i % 60:02}:00+00:00",
             "open": 1.1, "high": 1.2, "low": 1., "close": 1.15, "volume": float(i)} for i in range(100)]
    async def probe_fixture(**kwargs):
        assert kwargs["symbol"] == "EURUSD" and kwargs["timeframe"] == "H1" and kwargs["count"] == 100
        return {"phase": "1", "step": "fixture-only", "connected": True, "verified_demo": True,
                "closed_bars_received": 100, "closed_candles": rows, "orders_submitted": 0,
                "fills": 0, "status": "DEMO_CHECK_ONLY_VERIFIED", "finished_at_utc": datetime.now(timezone.utc).isoformat()}
    monkeypatch.setattr(connection_script, "probe", probe_fixture)
    output, log = tmp_path / "probe.json", tmp_path / "probe.log"
    monkeypatch.setattr(sys, "argv", ["test_mt5_connection.py", "--output", str(output), "--log", str(log)])
    assert connection_script.main() == 0
    printed = capsys.readouterr().out
    assert "timestamp" in printed and "open" in printed and "volume" in printed
    assert all(row["timestamp"] in printed for row in rows)
    assert len(json.loads(output.read_text())["closed_candles"]) == 100
    assert json.loads(log.read_text())["orders_submitted"] == 0
