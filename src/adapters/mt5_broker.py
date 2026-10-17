"""MetaTrader5 adapter. Strictly DEMO-only; defaults to read-only.

Requires an installed, already-logged-in local desktop MT5 terminal. No
passwords/API secrets. Demo orders require explicit opt-in, a pinned demo
login, protective stop-loss and capped loss/volume. Zero live-account mode.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import importlib
import math
import pandas as pd


class MT5DemoError(RuntimeError):
    """Fail closed on any missing, stale or inconsistent broker information."""


@dataclass(frozen=True)
class DemoOrderResult:
    status: str
    symbol: str
    side: str
    volume: float
    order_ticket: int
    deal_ticket: int
    retcode: int
    demo_only: bool = True


class MT5Broker:
    TIMEFRAMES = {"M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15",
                  "H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4",
                  "D1": "TIMEFRAME_D1"}

    def __init__(self, *, client: Any | None = None,
                 terminal_path: str | Path | None = None,
                 demo_order_enabled: bool = False,
                 expected_demo_login: int | None = None,
                 risk_fraction_max: float = 0.0025,
                 max_volume_lots: float = 0.10) -> None:
        if type(demo_order_enabled) is not bool:
            raise ValueError("demo_order_enabled must be bool")
        if demo_order_enabled and (type(expected_demo_login) is not int or expected_demo_login <= 0):
            raise ValueError("DEMO_ORDER_REQUIRES_PINNED_LOGIN")
        if expected_demo_login is not None and (
            type(expected_demo_login) is not int or expected_demo_login <= 0
        ):
            raise ValueError("INVALID_DEMO_LOGIN")
        if not 0 < risk_fraction_max <= 0.0025:
            raise ValueError("RISK_FRACTION_CAPPED_AT_0_25_PERCENT")
        if not 0 < max_volume_lots <= 0.10:
            raise ValueError("LOTS_CAPPED_AT_0_10")
        self._client = client
        self._terminal_path = str(terminal_path) if terminal_path is not None else None
        self._allow_demo_order = demo_order_enabled
        self._expected_login = expected_demo_login
        self._risk_fraction = risk_fraction_max
        self._max_lots = max_volume_lots
        self._connected = False

    def _mt5(self):
        if self._client is None:
            try:
                self._client = importlib.import_module("MetaTrader5")
            except ImportError as exc:
                raise MT5DemoError("MT5_NATIVE_PACKAGE_MISSING_ON_TERMINAL_HOST") from exc
        return self._client

    def _error(self, prefix: str) -> MT5DemoError:
        client = self._mt5()
        try:
            code = client.last_error()
        except Exception:
            code = "unavailable"
        return MT5DemoError(f"{prefix}: {code}")

    def _verify_demo(self):
        if not self._connected:
            raise MT5DemoError("NOT_CONNECTED")
        client = self._mt5()
        terminal = client.terminal_info()
        if terminal is None or not bool(getattr(terminal, "connected", False)):
            raise self._error("TERMINAL_DISCONNECTED")
        account = client.account_info()
        if account is None:
            raise self._error("ACCOUNT_INFO_MISSING")
        trade_mode = getattr(account, "trade_mode", None)
        demo_mode = getattr(client, "ACCOUNT_TRADE_MODE_DEMO", None)
        if demo_mode is None or trade_mode != demo_mode:
            raise MT5DemoError("REAL_OR_CONTEST_ACCOUNT_REFUSED_DEMO_ONLY")
        login = getattr(account, "login", None)
        if self._expected_login is not None and login != self._expected_login:
            raise MT5DemoError("DEMO_ACCOUNT_CHANGED_OR_WRONG_LOGIN")
        return account

    def connect(self) -> bool:
        mt5 = self._mt5()
        if self._connected:
            self._verify_demo()
            return True
        kwargs = {"path": self._terminal_path} if self._terminal_path else {}
        if not mt5.initialize(**kwargs):
            raise self._error("MT5_INITIALIZE_FAILED")
        self._connected = True
        try:
            self._verify_demo()
        except Exception:
            self.close()
            raise
        return True

    def close(self) -> None:
        if self._connected:
            try:
                self._mt5().shutdown()
            finally:
                self._connected = False

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, _type, _value, _tb):
        self.close()

    def get_historical_data(self, symbol: str = "EURUSD",
                            timeframe: str = "H1", count: int = 100) -> pd.DataFrame:
        """Exactly count completed bars; current forming bar (index zero) excluded."""
        self._verify_demo()
        if not isinstance(symbol, str) or not symbol or not symbol.replace(".", "").replace("_", "").isalnum():
            raise ValueError("INVALID_SYMBOL")
        if timeframe not in self.TIMEFRAMES:
            raise ValueError("UNSUPPORTED_TIMEFRAME")
        if type(count) is not int or not 1 <= count <= 5000:
            raise ValueError("INVALID_CANDLE_COUNT")
        client = self._mt5()
        tf = getattr(client, self.TIMEFRAMES[timeframe], None)
        if tf is None:
            raise MT5DemoError("TIMEFRAME_NOT_SUPPORTED_BY_MT5")
        info = client.symbol_info(symbol)
        if info is None:
            raise self._error("SYMBOL_NOT_FOUND")
        if not bool(getattr(info, "visible", False)):
            if not client.symbol_select(symbol, True):
                raise self._error("SYMBOL_SELECT_FAILED")
        rates = client.copy_rates_from_pos(symbol, tf, 1, count)
        if rates is None:
            raise self._error("MT5_RATES_FAILED")
        data = pd.DataFrame(rates)
        required = {"time", "open", "high", "low", "close", "tick_volume"}
        if len(data) != count or not required.issubset(data.columns):
            raise MT5DemoError("INSUFFICIENT_OR_INVALID_COMPLETED_RATES")
        data["timestamp"] = pd.to_datetime(data["time"], unit="s", utc=True)
        data = data.sort_values("timestamp").reset_index(drop=True)
        if data.timestamp.isna().any() or data.timestamp.duplicated().any():
            raise MT5DemoError("DUPLICATE_OR_INVALID_CANDLE_TIME")
        if not isinstance(data.timestamp.dtype, pd.DatetimeTZDtype):
            raise MT5DemoError("NON_UTC_CANDLES")
        for col in ("open", "high", "low", "close", "tick_volume"):
            data[col] = pd.to_numeric(data[col], errors="coerce")
        if not data[["open", "high", "low", "close", "tick_volume"]].map(math.isfinite).all().all():
            raise MT5DemoError("NONFINITE_CANDLES")
        if (data[["open", "high", "low", "close"]] <= 0).any().any() or (data.tick_volume < 0).any():
            raise MT5DemoError("INVALID_PRICE_OR_VOLUME")
        if not (data.high >= data[["open", "close", "low"]].max(axis=1)).all() or not (
            data.low <= data[["open", "close", "high"]].min(axis=1)
        ).all():
            raise MT5DemoError("INCONSISTENT_OHLC")
        return data[["timestamp", "open", "high", "low", "close", "tick_volume"]].copy()

    def get_open_positions(self, symbol: str | None = None) -> list[dict]:
        self._verify_demo()
        client = self._mt5()
        result = client.positions_get(symbol=symbol) if symbol else client.positions_get()
        if result is None:
            raise self._error("POSITIONS_READ_FAILED")
        return [{"ticket": int(p.ticket), "symbol": str(p.symbol),
                 "volume": float(p.volume), "type": int(p.type),
                 "price_open": float(p.price_open), "sl": float(p.sl),
                 "tp": float(p.tp), "profit": float(p.profit)} for p in result]

    def place_order(self, *, symbol: str, side: str, volume: float,
                    stop_loss: float, take_profit: float | None = None,
                    deviation_points: int = 20) -> DemoOrderResult:
        """Never trade unless explicit DEMO opt-in and pinned account validation."""
        if not self._allow_demo_order:
            raise MT5DemoError("DEMO_ORDER_EXECUTION_DISABLED_BY_DEFAULT")
        account = self._verify_demo()
        client = self._mt5()
        if side not in {"BUY", "SELL"}:
            raise ValueError("SIDE_MUST_BE_BUY_OR_SELL")
        if not isinstance(symbol,str) or not symbol or not symbol.replace(".","").replace("_","").isalnum():
            raise ValueError("INVALID_SYMBOL")
        if not math.isfinite(volume) or volume <= 0 or volume > self._max_lots:
            raise MT5DemoError("ORDER_VOLUME_LIMIT")
        if not math.isfinite(stop_loss) or stop_loss <= 0:
            raise ValueError("STOP_LOSS_REQUIRED")
        if take_profit is not None and (not math.isfinite(take_profit) or take_profit <= 0):
            raise ValueError("INVALID_TARGET")
        if type(deviation_points) is not int or not 0 <= deviation_points <= 20:
            raise ValueError("MAX_DEVIATION_20_POINTS")
        info = client.symbol_info(symbol)
        if info is None:
            raise self._error("ORDER_SYMBOL_NOT_FOUND")
        if not bool(getattr(info,"visible",False)) and not client.symbol_select(symbol,True):
            raise self._error("ORDER_SYMBOL_UNAVAILABLE")
        step = float(getattr(info,"volume_step",0))
        minimum = float(getattr(info,"volume_min",0))
        maximum = float(getattr(info,"volume_max",0))
        if step<=0 or volume<minimum or volume>maximum or abs(volume/step-round(volume/step))>1e-8:
            raise MT5DemoError("INVALID_BROKER_VOLUME_STEP")
        tick = client.symbol_info_tick(symbol)
        if tick is None:
            raise self._error("NO_LIVE_QUOTE")
        now = datetime.now(timezone.utc).timestamp()
        quote_time = float(getattr(tick,"time",0))
        if not 0 <= now - quote_time <= 60:
            raise MT5DemoError("STALE_TICK_REFUSE_ORDER")
        price = float(tick.ask if side=="BUY" else tick.bid)
        if not math.isfinite(price) or price<=0:
            raise MT5DemoError("INVALID_DEMO_QUOTE")
        if (side=="BUY" and stop_loss>=price) or (side=="SELL" and stop_loss<=price):
            raise MT5DemoError("STOP_LOSS_WRONG_SIDE")
        typ = client.ORDER_TYPE_BUY if side=="BUY" else client.ORDER_TYPE_SELL
        loss = client.order_calc_profit(typ,symbol,volume,price,stop_loss)
        if loss is None or not math.isfinite(float(loss)) or float(loss)>=0:
            raise self._error("CANNOT_MEASURE_STOP_RISK")
        equity=float(getattr(account,"equity",0))
        if equity<=0 or -float(loss)>equity*self._risk_fraction:
            raise MT5DemoError("PER_ORDER_STOP_RISK_EXCEEDS_0_25_PERCENT")
        terminal=client.terminal_info()
        if not (bool(getattr(account,"trade_allowed",False)) and
                bool(getattr(account,"trade_expert",False)) and terminal is not None and
                bool(getattr(terminal,"trade_allowed",False))):
            raise MT5DemoError("DEMO_TERMINAL_TRADING_DISABLED")
        filling=int(getattr(info,"filling_mode",0))
        if filling & int(getattr(client,"SYMBOL_FILLING_IOC",2)):
            fill=client.ORDER_FILLING_IOC
        elif filling & int(getattr(client,"SYMBOL_FILLING_FOK",1)):
            fill=client.ORDER_FILLING_FOK
        else:
            raise MT5DemoError("UNSUPPORTED_SYMBOL_FILL_MODE")
        request={"action":client.TRADE_ACTION_DEAL,"symbol":symbol,
                 "type":typ,"volume":float(volume),"price":price,
                 "sl":float(stop_loss),"deviation":deviation_points,
                 "type_filling":fill,"magic":261017,"comment":"DEMO_PINNED_ONLY"}
        if take_profit is not None:request["tp"]=float(take_profit)
        checked=client.order_check(request)
        if checked is None or int(getattr(checked,"retcode",-1))!=0:
            raise self._error("DEMO_ORDER_CHECK_FAILED")
        self._verify_demo()
        outcome=client.order_send(request)
        if outcome is None:
            raise self._error("DEMO_ORDER_SEND_NO_RESULT_RECONCILE_FIRST")
        rc=int(getattr(outcome,"retcode",-1))
        done=int(getattr(client,"TRADE_RETCODE_DONE",10009))
        partial=int(getattr(client,"TRADE_RETCODE_DONE_PARTIAL",10010))
        if rc not in (done,partial):
            raise MT5DemoError(f"DEMO_ORDER_NOT_FILLED_RECONCILE retcode={rc}")
        return DemoOrderResult(status="FILLED_DEMO" if rc==done else "PARTIAL_DEMO_RECONCILE",
                               symbol=symbol,side=side,volume=volume,
                               order_ticket=int(getattr(outcome,"order",0)),
                               deal_ticket=int(getattr(outcome,"deal",0)),retcode=rc)
