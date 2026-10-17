"""Async MT5Forge bridge pinned to an explicit Demo terminal; check-only orders.

MT5Forge 1.0.3's connector, candle feed and order builder are used directly.
Its initialize() normally attaches the default terminal, so an injected SDK
boundary pins initialize/login to MT5_DEMO_* credentials and rejects order_send.
All instances share one worker: MetaTrader5's process-global session is never
operated concurrently, and blocking SDK calls never run on the event loop.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import importlib
import importlib.metadata
import math
import os
import platform
import re
from typing import Any, Callable

import pandas as pd

from .base import AdapterError, ExecutionRejected, floor_step, positive, validate_bars

FORGE_VERSION = "1.0.3"
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mt5-demo")
_OWNER: MT5Broker | None = None
_PERIODS = {"M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min",
            "H1": "1h", "H4": "4h", "D1": "1d"}


class _DemoSessionRejected(ExecutionRejected):
    pass


@dataclass(frozen=True)
class _DemoSettings:
    login: int
    password: str = field(repr=False)
    server: str = field(repr=False)
    terminal_path: str = field(repr=False)

    @classmethod
    def from_environment(cls) -> _DemoSettings:
        values = {key: os.environ.get(key, "") for key in
                  ("MT5_DEMO_LOGIN", "MT5_DEMO_PASSWORD", "MT5_DEMO_SERVER", "MT5_DEMO_TERMINAL_PATH")}
        if not all(values.values()):
            raise AdapterError("Set MT5_DEMO_LOGIN, MT5_DEMO_PASSWORD, MT5_DEMO_SERVER and MT5_DEMO_TERMINAL_PATH; default terminal attachment is disabled")
        try:
            login = int(values["MT5_DEMO_LOGIN"])
        except ValueError:
            raise AdapterError("MT5_DEMO_LOGIN must select a positive integer Demo login") from None
        if login <= 0:
            raise AdapterError("MT5_DEMO_LOGIN must select a positive integer Demo login")
        return cls(login, values["MT5_DEMO_PASSWORD"], values["MT5_DEMO_SERVER"], values["MT5_DEMO_TERMINAL_PATH"])


class _QuietForgeLogger:
    """Suppress upstream account/server/error fields; the probe logs safe events."""
    def info(self, *args: Any, **kwargs: Any) -> None: pass
    def warning(self, *args: Any, **kwargs: Any) -> None: pass
    def error(self, *args: Any, **kwargs: Any) -> None: pass


class _DemoTerminal:
    """The genuine SDK boundary supplied to MT5Forge, with no submit capability."""
    def __init__(self, raw: Any, settings: _DemoSettings) -> None:
        self._raw, self._settings, self._initialized_by_us = raw, settings, False

    def verify(self) -> Any:
        account = self._raw.account_info()
        expected = getattr(self._raw, "ACCOUNT_TRADE_MODE_DEMO", None)
        if expected is None or account is None or getattr(account, "trade_mode", None) != expected:
            raise _DemoSessionRejected("MT5 account is not verified Demo; real, contest and unknown accounts are rejected")
        if getattr(account, "login", None) != self._settings.login or getattr(account, "server", None) != self._settings.server:
            raise _DemoSessionRejected("MT5 Demo session differs from the explicit account selectors")
        return account

    def initialize(self, *args: Any, **kwargs: Any) -> bool:
        # Do not attach to a session that another component already opened live.
        existing = self._raw.account_info()
        demo = getattr(self._raw, "ACCOUNT_TRADE_MODE_DEMO", None)
        if existing is not None and (demo is None or getattr(existing, "trade_mode", None) != demo):
            raise ExecutionRejected("An existing non-Demo MT5 session is present; no initialization attempted")
        self._initialized_by_us = True
        settings = self._settings
        ok = bool(self._raw.initialize(settings.terminal_path, login=settings.login,
                                       password=settings.password, server=settings.server, timeout=15000))
        if ok:
            self.verify()
        return ok

    def login(self, login: int, *, password: str, server: str) -> bool:
        settings = self._settings
        if login != settings.login or password != settings.password or server != settings.server:
            raise ExecutionRejected("MT5 login differs from the explicit Demo selectors")
        self.verify()
        ok = bool(self._raw.login(settings.login, password=settings.password, server=settings.server))
        if ok:
            self.verify()
        return ok

    def account_info(self) -> Any:
        return self.verify()

    def shutdown(self) -> None:
        if self._initialized_by_us:
            self._raw.shutdown()
            self._initialized_by_us = False

    def order_send(self, *args: Any, **kwargs: Any) -> None:
        raise ExecutionRejected("Order submission is disabled; Phase 1 supports MT5 order_check only")

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._raw, name)
        if not callable(value):
            return value

        def guarded(*args: Any, **kwargs: Any) -> Any:
            self.verify()
            result = value(*args, **kwargs)
            self.verify()  # Reject an external account switch during a blocking read.
            return result
        return guarded


class MT5Broker:
    """Async Demo connection and broker order preflight, never a filled order.

    ``terminal_backend`` injects only the terminal API for contract tests or an
    explicitly provisioned terminal bridge. MT5Forge itself remains genuine.
    Production construction leaves it unset and imports the Windows SDK lazily.
    """
    mode = "mt5forge_demo_check_only"

    def __init__(self, *, terminal_backend: Any | None = None, max_risk_fraction: float = 0.02,
                 max_tick_age_seconds: float = 120.0) -> None:
        self._terminal_backend = terminal_backend
        self.max_risk_fraction = positive(max_risk_fraction, "max_risk_fraction")
        if self.max_risk_fraction > 1:
            raise ValueError("max_risk_fraction cannot exceed one")
        self.max_tick_age_seconds = positive(max_tick_age_seconds, "max_tick_age_seconds")
        self._connector: Any = None
        self._feed: Any = None
        self._forge: dict[str, Any] = {}

    async def _run(self, function: Callable[..., Any], *args: Any) -> Any:
        return await asyncio.get_running_loop().run_in_executor(_EXECUTOR, self._execute, function, args)

    def _execute(self, function: Callable[..., Any], args: tuple[Any, ...]) -> Any:
        try:
            return function(*args)
        except _DemoSessionRejected:
            self._close()
            raise ExecutionRejected("MT5 Demo verification failed; connection invalidated") from None
        except (AdapterError, ValueError):
            raise
        except Exception:
            raise AdapterError("MT5Forge terminal operation failed; provider diagnostics were redacted") from None

    async def connect(self) -> dict[str, Any]:
        return await self._run(self._connect)

    def _connect(self) -> dict[str, Any]:
        global _OWNER
        if _OWNER is not None and _OWNER is not self:
            raise ExecutionRejected("Another MT5Broker owns the process-global Demo session; close it before connecting")
        if self._connector is not None:
            self._connector.mt5.verify()
            return {"connected": True, "account_type": "DEMO", "backend": "mt5forge", "version": FORGE_VERSION, "mode": self.mode}
        if self._terminal_backend is None and platform.system() != "Windows":
            raise AdapterError("MT5Forge terminal connection requires Windows and a configured Demo terminal; current host is not Windows")
        settings = _DemoSettings.from_environment()
        try:
            if importlib.metadata.version("mt5forge") != FORGE_VERSION:
                raise AdapterError("Install the reviewed mt5forge==1.0.3 backend (Python >=3.12)")
            connection = importlib.import_module("mt5forge.connection")
            market = importlib.import_module("mt5forge.market")
            orders = importlib.import_module("mt5forge.orders.order_builder")
            constants = importlib.import_module("mt5forge.core.constants")
            terminal = self._terminal_backend if self._terminal_backend is not None else importlib.import_module("MetaTrader5")
        except (ImportError, importlib.metadata.PackageNotFoundError):
            raise AdapterError("Install mt5forge==1.0.3 and the Windows MetaTrader5 package in Python >=3.12") from None
        boundary = _DemoTerminal(terminal, settings)
        connector = connection.MT5Connector(terminal_path=settings.terminal_path, mt5=boundary, reconnect_attempts=0)
        connector.logger = _QuietForgeLogger()
        try:
            credentials = connection.MT5Credentials(account=settings.login, password=settings.password, server=settings.server)
            connector.login(credentials)
            boundary.verify()
        except Exception:
            boundary.shutdown()
            raise AdapterError("MT5Forge could not establish the explicitly configured Demo session; no market data or orders accepted") from None
        self._connector, self._feed = connector, market.MarketDataFeed(connector)
        self._forge = {"OrderRequest": orders.OrderRequest, "OrderBuilder": orders.OrderBuilder,
                       "OrderType": constants.OrderType, "Timeframe": constants.Timeframe}
        _OWNER = self
        return {"connected": True, "account_type": "DEMO", "backend": "mt5forge", "version": FORGE_VERSION, "mode": self.mode}

    def _session(self) -> Any:
        if _OWNER is not self or self._connector is None:
            raise AdapterError("MT5Broker is disconnected; await connect() first")
        try:
            return self._connector.mt5.verify()
        except Exception:
            self._close()
            raise ExecutionRejected("MT5 Demo session verification failed; connection invalidated") from None

    @staticmethod
    def _symbol(symbol: str) -> str:
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", symbol):
            raise ValueError("Invalid MT5 symbol")
        return symbol

    async def get_historical_data(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        return await self._run(self._get_historical_data, symbol, timeframe, count)

    def _get_historical_data(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        self._session()
        symbol, timeframe = self._symbol(symbol), str(timeframe).upper()
        if timeframe not in _PERIODS or isinstance(count, bool) or not 1 <= int(count) <= 20000:
            raise ValueError("Use M1/M5/M15/M30/H1/H4/D1 and count between 1 and 20000")
        try:
            if not self._connector.mt5.symbol_select(symbol, True):
                raise AdapterError("Demo instrument is unavailable; use the broker's explicit symbol suffix")
            data = self._feed.get_candles(symbol=symbol, timeframe=self._forge["Timeframe"].parse(timeframe),
                                          count=int(count), shift=1)
            self._session()
        except (AdapterError, ExecutionRejected):
            raise
        except Exception:
            raise AdapterError("MT5Forge historical-data request failed; no substitute bars produced") from None
        data = data.rename(columns={"time": "timestamp"}).set_index("timestamp")
        data["volume"] = data["tick_volume"]
        result = validate_bars(data)
        if len(result) > int(count) or (result.index + pd.Timedelta(_PERIODS[timeframe]) > pd.Timestamp.now(tz="UTC")).any():
            raise AdapterError("MT5Forge returned more than requested or unclosed bars")
        result.attrs.update(backend="mt5forge", backend_version=FORGE_VERSION, mode=self.mode,
                            volume_kind="tick_volume", symbol=symbol, timeframe=timeframe,
                            requested_count=int(count), actual_count=len(result), forming_bar_excluded=True)
        return result

    async def get_quote(self, symbol: str) -> dict[str, float]:
        return await self._run(self._quote, symbol)

    def _quote(self, symbol: str) -> dict[str, float]:
        import time
        self._session()
        symbol = self._symbol(symbol)
        terminal = self._connector.mt5
        if not terminal.symbol_select(symbol, True):
            raise AdapterError("Demo instrument is unavailable")
        tick, spec = terminal.symbol_info_tick(symbol), terminal.symbol_info(symbol)
        if tick is None or spec is None:
            raise AdapterError("Demo quote/specification unavailable")
        bid, ask = positive(tick.bid, "bid"), positive(tick.ask, "ask")
        age = time.time() - float(tick.time)
        if ask < bid or not math.isfinite(age) or age < -5 or age > self.max_tick_age_seconds:
            raise ExecutionRejected("Demo quote is stale or crossed; market may be closed")
        return {"bid": bid, "ask": ask, "point": positive(spec.point, "point"),
                "digits": int(spec.digits), "min_volume": float(spec.volume_min),
                "minimum_stop_distance": float(spec.trade_stops_level) * float(spec.point)}

    async def place_order(self, symbol: str, order_type: str, volume: float,
                          sl: float, tp: float) -> dict[str, Any]:
        return await self._run(self._place_order, symbol, order_type, volume, sl, tp)

    def _place_order(self, symbol: str, order_type: str, volume: float, sl: float, tp: float) -> dict[str, Any]:
        account = self._session()
        symbol, direction = self._symbol(symbol), str(order_type).upper()
        if direction not in {"BUY", "SELL"}:
            raise ExecutionRejected("Check-only Phase 1 supports BUY/SELL market requests")
        quote = self._quote(symbol)
        terminal, kinds = self._connector.mt5, self._forge["OrderType"]
        spec = terminal.symbol_info(symbol)
        quantity = floor_step(positive(volume, "volume"), float(spec.volume_step))
        if quantity < spec.volume_min or quantity > spec.volume_max:
            raise ExecutionRejected("Demo order volume is outside broker lot limits")
        stop, target = round(positive(sl, "sl"), int(spec.digits)), round(positive(tp, "tp"), int(spec.digits))
        entry = quote["ask"] if direction == "BUY" else quote["bid"]
        opposite = quote["bid"] if direction == "BUY" else quote["ask"]
        gap = max(quote["minimum_stop_distance"], quote["point"])
        valid = stop <= opposite - gap and target >= opposite + gap if direction == "BUY" else stop >= opposite + gap and target <= opposite - gap
        if not valid:
            raise ExecutionRejected("Demo stop/target has invalid direction or minimum distance")
        raw_type = terminal.ORDER_TYPE_BUY if direction == "BUY" else terminal.ORDER_TYPE_SELL
        risk = terminal.order_calc_profit(raw_type, symbol, quantity, entry, stop)
        margin = terminal.order_calc_margin(raw_type, symbol, quantity, entry)
        if risk is None or not math.isfinite(float(risk)) or risk >= 0 or abs(risk) > positive(account.equity, "equity") * self.max_risk_fraction:
            raise ExecutionRejected("Demo stop risk is unavailable or exceeds configured equity fraction")
        if margin is None or not math.isfinite(float(margin)) or margin < 0 or margin > account.margin_free:
            raise ExecutionRejected("Demo margin is unavailable or insufficient")
        request = self._forge["OrderRequest"](symbol=symbol, order_type=kinds(direction), lot=quantity,
                                               sl=stop, tp=target, magic=20261017, comment="AI Demo check only")
        payload = self._forge["OrderBuilder"](terminal).build_market_request(request, entry)
        if spec.filling_mode & terminal.SYMBOL_FILLING_IOC:
            payload["type_filling"] = terminal.ORDER_FILLING_IOC
        elif spec.filling_mode & terminal.SYMBOL_FILLING_FOK:
            payload["type_filling"] = terminal.ORDER_FILLING_FOK
        else:
            raise ExecutionRejected("Demo broker offers no supported immediate filling mode")
        self._session()
        checked = terminal.order_check(payload)  # Deliberately never order_send.
        self._session()
        if checked is None or getattr(checked, "retcode", None) != 0:
            raise ExecutionRejected("Demo order_check rejected the request; no order submitted")
        return {"mode": self.mode, "status": "CHECKED_ONLY", "simulated": True,
                "submitted": False, "filled": False, "broker_retcode": 0,
                "symbol": symbol, "order_type": direction, "volume": quantity,
                "reference_price": entry, "sl": stop, "tp": target}

    async def get_open_positions(self) -> list[dict[str, Any]]:
        return await self._run(self._get_open_positions)

    def _get_open_positions(self) -> list[dict[str, Any]]:
        self._session()
        try:
            positions = self._connector.mt5.positions_get()
            self._session()
        except (AdapterError, ExecutionRejected):
            raise
        except Exception:
            raise AdapterError("Demo open-position read failed; absence of a response is not flat") from None
        if positions is None:
            raise AdapterError("Demo open-position read returned no response; not treated as flat")
        return [{"ticket": int(p.ticket), "symbol": str(p.symbol), "volume": float(p.volume),
                 "order_type": "BUY" if int(p.type) == int(self._connector.mt5.POSITION_TYPE_BUY) else "SELL",
                 "entry_price": float(p.price_open), "current_price": float(p.price_current),
                 "profit": float(p.profit), "magic": int(p.magic), "source": "actual_demo_terminal"}
                for p in positions]

    async def close(self) -> None:
        await self._run(self._close)

    def _close(self) -> None:
        global _OWNER
        if self._connector is not None:
            try:
                self._connector.shutdown()
            finally:
                self._connector, self._feed = None, None
        if _OWNER is self:
            _OWNER = None
