"""Windows MetaTrader5 bridge that verifies the connected account is Demo.

Importing this module on Linux is safe. The proprietary MT5 package and terminal
are required only when connecting; a failure is never replaced with fake data.
"""
from __future__ import annotations

import importlib
import math
import os
import platform
from typing import Any

import pandas as pd

from .base import AdapterError, ExecutionRejected, MarketAdapter, OrderReceipt, floor_step, positive, side, validate_bars


class MT5Adapter(MarketAdapter):
    mode = "mt5_demo"
    SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD")
    _TIMEFRAMES = {"H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4", "D1": "TIMEFRAME_D1",
                   "1h": "TIMEFRAME_H1", "4h": "TIMEFRAME_H4", "1d": "TIMEFRAME_D1"}

    def __init__(self, *, enable_orders: bool = False, max_risk_fraction: float = 0.02,
                 symbol_map: dict[str, str] | None = None, max_tick_age_seconds: float = 120.0) -> None:
        self.enable_orders = bool(enable_orders)
        self.max_risk_fraction = positive(max_risk_fraction, "max_risk_fraction")
        if self.max_risk_fraction > 1:
            raise ExecutionRejected("max_risk_fraction cannot exceed one")
        self.symbol_map = dict(symbol_map or {})
        if not set(self.symbol_map).issubset(self.SYMBOLS):
            raise ValueError("symbol_map keys must be EURUSD, GBPUSD, USDJPY or XAUUSD")
        self.max_tick_age_seconds = positive(max_tick_age_seconds, "max_tick_age_seconds")
        self._mt5: Any = None

    def connect(self) -> None:
        if self._mt5 is None:
            if platform.system() != "Windows":
                raise AdapterError("MT5 requires Windows and a running MetaTrader 5 Demo terminal; run README.mt5.md on Windows")
            try:
                mt5 = importlib.import_module("MetaTrader5")
            except ImportError:
                raise AdapterError("Install MetaTrader5 on Windows: python -m pip install MetaTrader5") from None
            settings: dict[str, Any] = {"timeout": 15000}
            login = os.environ.get("MT5_LOGIN")
            password = os.environ.get("MT5_PASSWORD")
            server = os.environ.get("MT5_SERVER")
            if login:
                try:
                    settings["login"] = int(login)
                except ValueError:
                    raise AdapterError("MT5_LOGIN must be an integer Demo account selector") from None
            if password:
                settings["password"] = password
            if server:
                settings["server"] = server
            terminal_path = os.environ.get("MT5_TERMINAL_PATH")
            initialized = mt5.initialize(terminal_path, **settings) if terminal_path else mt5.initialize(**settings)
            if not initialized:
                # last_error() can include paths/account/provider details.
                raise AdapterError("MT5 initialization failed; open the Windows terminal, select Demo and verify environment selectors")
            self._mt5 = mt5
        self._verify_demo()

    def _verify_demo(self) -> Any:
        info = self._mt5.account_info()
        if info is None or getattr(info, "trade_mode", None) != self._mt5.ACCOUNT_TRADE_MODE_DEMO:
            self.close()
            raise ExecutionRejected("MT5 account is not verified Demo; real/contest/unknown accounts are rejected")
        server = os.environ.get("MT5_SERVER")
        login = os.environ.get("MT5_LOGIN")
        if (server and getattr(info, "server", None) != server) or (login and str(getattr(info, "login", "")) != login):
            self.close()
            raise ExecutionRejected("Connected Demo account does not match environment selectors")
        return info

    def close(self) -> None:
        if self._mt5 is not None:
            self._mt5.shutdown()
            self._mt5 = None

    def _symbol(self, symbol: str) -> str:
        normalized = symbol.upper().replace("/", "")
        if normalized not in self.SYMBOLS:
            raise ExecutionRejected("Supported instruments: EURUSD, GBPUSD, USDJPY, XAUUSD; futures are not configured")
        return self.symbol_map.get(normalized, normalized)

    def _info(self, symbol: str) -> Any:
        self.connect()
        if not self._mt5.symbol_select(symbol, True):
            raise AdapterError("Requested instrument is unavailable in the Demo terminal; configure broker suffix using symbol_map")
        info = self._mt5.symbol_info(symbol)
        if info is None:
            raise AdapterError("MT5 symbol specification unavailable")
        return info

    def get_data(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        if timeframe not in self._TIMEFRAMES:
            raise ValueError("Supported MT5 timeframes: H1, H4, D1")
        if not 1 <= int(count) <= 100000:
            raise ValueError("count must be between 1 and 100000")
        mapped = self._symbol(symbol)
        self._info(mapped)
        # Position 0 is an unfinished bar. Start at 1 for closed-bar semantics.
        rates = self._mt5.copy_rates_from_pos(mapped, getattr(self._mt5, self._TIMEFRAMES[timeframe]), 1, int(count))
        if rates is None or len(rates) == 0:
            raise AdapterError("MT5 returned no closed bars; load instrument history in Demo terminal")
        frame = pd.DataFrame(rates)
        frame.index = pd.to_datetime(frame["time"], unit="s", utc=True)
        # FX/CFD tick_volume is an activity proxy, never USDT/centralized volume.
        frame["volume"] = frame["tick_volume"]
        result = validate_bars(frame)
        result.attrs.update(venue="mt5_demo", volume_kind="tick_volume", instrument=mapped)
        return result

    def get_history(self, symbol: str, timeframe: str, start: Any, end: Any, *, max_bars: int = 100000) -> pd.DataFrame:
        """Actual Demo terminal history in the half-open UTC interval [start,end).

        ``copy_rates_range`` can return fewer rows than requested when terminal
        history is limited. Preserve that actual coverage; never synthesize a
        calendar or forward-fill market OHLCV across closed sessions.
        """
        if timeframe not in self._TIMEFRAMES:
            raise ValueError("Supported MT5 timeframes: H1, H4, D1")
        if not 1 <= int(max_bars) <= 100000:
            raise ValueError("max_bars must be between 1 and 100000")
        begin, finish = pd.Timestamp(start), pd.Timestamp(end)
        if begin.tzinfo is None or finish.tzinfo is None or begin >= finish:
            raise ValueError("History start/end must be timezone-aware and increasing")
        begin, finish = begin.tz_convert("UTC"), finish.tz_convert("UTC")
        mapped = self._symbol(symbol)
        self._info(mapped)
        rates = self._mt5.copy_rates_range(mapped, getattr(self._mt5, self._TIMEFRAMES[timeframe]),
                                          begin.to_pydatetime(), finish.to_pydatetime())
        if rates is None or len(rates) == 0:
            raise AdapterError("MT5 returned no historical bars; load Demo terminal history and raise its Max bars setting")
        frame = pd.DataFrame(rates)
        frame.index = pd.to_datetime(frame["time"], unit="s", utc=True)
        delta = {"H1": "1h", "1h": "1h", "H4": "4h", "4h": "4h", "D1": "1d", "1d": "1d"}[timeframe]
        close_times = frame.index + pd.Timedelta(delta)
        cutoff = min(finish, pd.Timestamp.now(tz="UTC"))
        frame = frame[(frame.index >= begin) & (frame.index < finish) & (close_times <= cutoff)].copy()
        if len(frame) > int(max_bars):
            raise AdapterError("MT5 historical response exceeds the requested safety limit; use a shorter interval")
        frame["volume"] = frame["tick_volume"]
        result = validate_bars(frame)
        result.attrs.update(venue="mt5_demo", volume_kind="tick_volume", instrument=mapped,
                            requested_start=begin.isoformat(), requested_end_exclusive=finish.isoformat(),
                            returned_rows=len(result), history_coverage="actual returned bars; no fill across sessions")
        return result

    def _tick(self, symbol: str) -> Any:
        import time
        tick = self._mt5.symbol_info_tick(symbol)
        if tick is None or not math.isfinite(float(tick.bid)) or not math.isfinite(float(tick.ask)):
            raise AdapterError("MT5 quote unavailable")
        if tick.bid <= 0 or tick.ask < tick.bid:
            raise AdapterError("MT5 quote is invalid")
        age = time.time() - float(tick.time)
        if age < -5 or age > self.max_tick_age_seconds:
            raise ExecutionRejected("MT5 quote is stale; market may be closed")
        return tick

    def get_price(self, symbol: str) -> float:
        mapped = self._symbol(symbol)
        self._info(mapped)
        tick = self._tick(mapped)
        return (float(tick.bid) + float(tick.ask)) / 2

    def get_balance(self) -> dict[str, float | str]:
        self.connect()
        account = self._verify_demo()
        return {"equity": float(account.equity), "cash": float(account.balance),
                "free_margin": float(account.margin_free), "currency": str(account.currency)}

    def get_positions(self) -> list[dict[str, Any]]:
        self.connect()
        positions = self._mt5.positions_get()
        if positions is None:
            raise AdapterError("MT5 positions unavailable")
        notionals = {}
        for position in positions:
            if position.symbol not in notionals:
                self._info(position.symbol)
                notionals[position.symbol] = self._notional_per_lot(position.symbol)
        return [{"ticket": int(p.ticket), "symbol": p.symbol, "size": float(p.volume),
                 "direction": "buy" if p.type == self._mt5.POSITION_TYPE_BUY else "sell",
                 "price": float(p.price_open), "unrealized_pnl": float(p.profit),
                 "magic": int(getattr(p, "magic", 0)),
                 "notional_account_currency": float(p.volume) * notionals[p.symbol]} for p in positions]

    def _notional_per_lot(self, mapped_symbol: str) -> float:
        entry = positive(self._tick(mapped_symbol).ask, "entry")
        change = self._mt5.order_calc_profit(self._mt5.ORDER_TYPE_BUY, mapped_symbol, 1.0, entry, entry * 1.0001)
        if change is None or not math.isfinite(float(change)) or change <= 0:
            raise ExecutionRejected("MT5 cannot determine account-currency notional per lot")
        return positive(float(change) / 0.0001, "notional_per_lot")

    def size_for_fraction(self, symbol: str, fraction: float, equity: float, *,
                          stop_loss: float | None = None) -> float:
        """Fraction denotes account-equity *risk* for leveraged MT5 lots.

        MT5 uses broker order_calc_profit to convert price distance into account
        currency; spot base-unit conversion is invalid for FX/CFDs.
        """
        fraction = positive(fraction, "fraction")
        if fraction > self.max_risk_fraction:
            raise ExecutionRejected("Requested MT5 risk fraction exceeds configured limit")
        if stop_loss is None:
            raise ExecutionRejected("MT5 sizing requires a stop_loss")
        mapped = self._symbol(symbol)
        spec = self._info(mapped)
        tick = self._tick(mapped)
        entry = float(tick.ask)
        stop = positive(stop_loss, "stop_loss")
        # Long when stop is below market, short when above market.
        order_type = self._mt5.ORDER_TYPE_BUY if stop < entry else self._mt5.ORDER_TYPE_SELL
        if order_type == self._mt5.ORDER_TYPE_SELL:
            entry = float(tick.bid)
        risk_one_lot = self._mt5.order_calc_profit(order_type, mapped, 1.0, entry, stop)
        if risk_one_lot is None or not math.isfinite(float(risk_one_lot)) or risk_one_lot >= 0:
            raise ExecutionRejected("MT5 cannot calculate a losing-stop risk in account currency")
        quantity = floor_step(positive(equity, "equity") * fraction / abs(risk_one_lot), spec.volume_step)
        if quantity < spec.volume_min:
            raise ExecutionRejected("Risk budget is below broker minimum lot size")
        return min(quantity, float(spec.volume_max))

    def size_for_allocation(self, symbol: str, fraction: float, equity: float, *,
                            stop_loss: float | None = None) -> float:
        """Convert notional capital allocation to lots in account currency.

        A one-basis-point favorable move reveals account-currency notional per
        lot through the broker's own contract/currency conversion. Unlike
        ``size_for_fraction`` this quantity is independent of stop distance.
        Stops, account risk and margin are still verified by ``place_order``.
        """
        fraction = positive(fraction, "fraction")
        if fraction > 1:
            raise ExecutionRejected("fraction cannot exceed one")
        mapped = self._symbol(symbol)
        spec = self._info(mapped)
        notional_per_lot = self._notional_per_lot(mapped)
        quantity = floor_step(positive(equity, "equity") * fraction / notional_per_lot, spec.volume_step)
        if quantity < spec.volume_min or quantity > spec.volume_max:
            raise ExecutionRejected("Notional allocation is outside broker lot limits")
        return quantity

    def place_order(self, symbol: str, direction: str, size: float, *,
                    stop_loss: float | None = None, take_profit: float | None = None,
                    position_ticket: int | None = None, reduce_only: bool = False,
                    **kwargs: Any) -> OrderReceipt:
        if not self.enable_orders:
            raise ExecutionRejected("MT5 Demo orders are disabled")
        if kwargs:
            raise ExecutionRejected("Unsupported MT5 order options")
        mapped, direction = self._symbol(symbol), side(direction)
        spec = self._info(mapped)
        account = self._verify_demo()  # Recheck at every order, including account switches.
        if not getattr(account, "trade_allowed", False) or not getattr(account, "trade_expert", False):
            raise ExecutionRejected("Demo account algorithmic trading permission is disabled")
        quantity = floor_step(positive(size, "size"), spec.volume_step)
        if quantity < spec.volume_min or quantity > spec.volume_max:
            raise ExecutionRejected("Order size is outside MT5 lot limits")
        tick = self._tick(mapped)
        order_type = self._mt5.ORDER_TYPE_BUY if direction == "buy" else self._mt5.ORDER_TYPE_SELL
        entry = round(float(tick.ask if direction == "buy" else tick.bid), int(spec.digits))
        request = {"action": self._mt5.TRADE_ACTION_DEAL, "symbol": mapped, "volume": quantity,
                   "type": order_type, "price": entry, "deviation": 10,
                   "magic": 20261016, "comment": "AI Demo", "type_time": self._mt5.ORDER_TIME_GTC}
        # SYMBOL_FILLING_* is a bitmask; ORDER_FILLING_* is a different enum.
        if spec.filling_mode & self._mt5.SYMBOL_FILLING_IOC:
            request["type_filling"] = self._mt5.ORDER_FILLING_IOC
        elif spec.filling_mode & self._mt5.SYMBOL_FILLING_FOK:
            request["type_filling"] = self._mt5.ORDER_FILLING_FOK
        else:
            raise ExecutionRejected("Broker offers no supported immediate filling mode")
        if reduce_only:
            if position_ticket is None:
                raise ExecutionRejected("Closing a Demo position requires its position_ticket")
            positions = self._mt5.positions_get(ticket=int(position_ticket))
            if positions is None or len(positions) != 1:
                raise ExecutionRejected("Demo position not found")
            position = positions[0]
            opposite = (direction == "sell" and position.type == self._mt5.POSITION_TYPE_BUY) or (
                direction == "buy" and position.type != self._mt5.POSITION_TYPE_BUY)
            if position.symbol != mapped or quantity > position.volume or not opposite:
                raise ExecutionRejected("Close order would increase or reverse exposure")
            request["position"] = int(position_ticket)
        else:
            if position_ticket is not None:
                raise ExecutionRejected("position_ticket is valid only with reduce_only=True")
            if stop_loss is None:
                raise ExecutionRejected("Every new Demo position requires a broker-side stop_loss")
            stop = round(positive(stop_loss, "stop_loss"), int(spec.digits))
            minimum_gap = max(float(spec.trade_stops_level) * float(spec.point), float(spec.point))
            # Broker evaluates stops relative to opposite quote (bid for buy).
            quote = float(tick.bid if direction == "buy" else tick.ask)
            valid_stop = stop <= quote - minimum_gap if direction == "buy" else stop >= quote + minimum_gap
            if not valid_stop:
                raise ExecutionRejected("Stop is on the wrong side or inside broker minimum distance")
            risk = self._mt5.order_calc_profit(order_type, mapped, quantity, entry, stop)
            if risk is None or not math.isfinite(float(risk)) or risk >= 0:
                raise ExecutionRejected("Cannot determine MT5 stop-loss risk")
            equity = positive(account.equity, "account equity")
            if abs(risk) > equity * self.max_risk_fraction:
                raise ExecutionRejected("MT5 stop-loss risk exceeds configured equity fraction")
            request["sl"] = stop
            if take_profit is not None:
                target = round(positive(take_profit, "take_profit"), int(spec.digits))
                valid_target = target >= quote + minimum_gap if direction == "buy" else target <= quote - minimum_gap
                if not valid_target:
                    raise ExecutionRejected("Target is on the wrong side or inside broker minimum distance")
                request["tp"] = target
            margin = self._mt5.order_calc_margin(order_type, mapped, quantity, entry)
            if margin is None or not math.isfinite(float(margin)) or margin < 0 or margin > account.margin_free:
                raise ExecutionRejected("Insufficient Demo margin or margin calculation unavailable")
        checked = self._mt5.order_check(request)
        if checked is None or checked.retcode != 0:
            raise ExecutionRejected("MT5 broker preflight rejected Demo order")
        result = self._mt5.order_send(request)
        if result is None:
            raise AdapterError("MT5 order result unknown; reconcile Demo positions before retrying")
        if result.retcode not in {self._mt5.TRADE_RETCODE_DONE, self._mt5.TRADE_RETCODE_DONE_PARTIAL}:
            raise ExecutionRejected(f"MT5 Demo order rejected (retcode {int(result.retcode)})")
        status = "FILLED" if result.retcode == self._mt5.TRADE_RETCODE_DONE else "PARTIALLY_FILLED"
        return OrderReceipt(str(result.order), symbol, direction, float(result.volume), float(result.price), status, self.mode)
