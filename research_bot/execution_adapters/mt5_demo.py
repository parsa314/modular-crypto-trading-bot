from __future__ import annotations

"""Fail-closed MetaTrader 5 DEMO execution adapter.

This adapter exists for forward/execution validation of the existing research
strategy and AI stack. It deliberately refuses real-money MT5 accounts and is
disabled for submission unless explicitly opted in.

Important scientific boundary:
- canonical crypto-exchange data/research remains the source for exchange claims;
- MT5 broker/CFD observations are a separate execution venue/domain;
- MT5 demo outcomes must not be merged into exchange performance statistics
  unless a separately declared cross-venue experiment does so.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Any, Mapping

from ..execution import ExecutionRequest, OrderSide, OrderType


class MT5DemoSafetyError(RuntimeError):
    """Raised when a fail-closed demo-safety invariant is violated."""


class MT5DemoExecutionError(RuntimeError):
    """Raised when MT5 rejects or cannot validate a demo execution request."""


@dataclass(frozen=True)
class MT5DemoConfig:
    """Configuration for demo-only MT5 execution.

    allowed_symbols contains canonical research symbols such as BTC/USDT.
    symbol_map translates them to the exact broker symbol such as BTCUSD or
    BTCUSDm.

    ExecutionRequest.quantity is treated as base-asset units from the research
    engine. The requested quote notional is converted into MT5 lots using the
    broker-reported trade_contract_size and always rounded DOWN to the broker
    volume step so execution never increases requested risk.
    """

    allowed_symbols: tuple[str, ...] = ()
    symbol_map: Mapping[str, str] = field(default_factory=dict)
    max_order_notional: float = 5_000.0
    max_spread_bps: float = 35.0
    max_tick_age_seconds: float = 30.0
    max_deviation_points: int = 20
    magic: int = 314_590
    require_server_side_stop: bool = True
    submit_enabled: bool = False
    filling_mode: int | None = None

    def __post_init__(self) -> None:
        numeric = (
            self.max_order_notional,
            self.max_spread_bps,
            self.max_tick_age_seconds,
        )
        if not all(math.isfinite(float(x)) and float(x) > 0.0 for x in numeric):
            raise ValueError("MT5 demo numeric safety limits must be finite and positive")
        if int(self.max_deviation_points) < 0:
            raise ValueError("max_deviation_points must be non-negative")
        if int(self.magic) <= 0:
            raise ValueError("magic must be positive")
        if not self.allowed_symbols:
            raise ValueError("allowed_symbols must be an explicit non-empty allowlist")


@dataclass(frozen=True)
class MT5DemoExecutionResult:
    client_order_id: str
    canonical_symbol: str
    venue_symbol: str
    side: str
    status: str
    retcode: int
    order_ticket: int
    deal_ticket: int
    requested_quantity: float
    submitted_lots: float
    filled_lots: float
    filled_base_quantity: float
    decision_reference_price: float
    executable_price: float
    fill_price: float
    implementation_shortfall_bps: float | None
    account_login: int
    account_server: str
    account_trade_mode: int
    contract_size: float
    timestamp: datetime
    comment: str


@dataclass(frozen=True)
class MT5DemoCloseResult:
    ticket: int
    venue_symbol: str
    closed_side: str
    status: str
    retcode: int
    order_ticket: int
    deal_ticket: int
    requested_lots: float
    filled_lots: float
    executable_price: float
    fill_price: float
    account_login: int
    account_server: str
    timestamp: datetime
    comment: str


class MT5DemoExecutor:
    """Demo-only MT5 adapter with strict real-account refusal.

    mt5_module is injectable so CI can use a fake module without importing the
    Windows-only MetaTrader5 runtime.
    """

    def __init__(self, config: MT5DemoConfig, *, mt5_module: Any | None = None):
        self.config = config
        self._mt5 = mt5_module
        self._connected = False
        self._submission_enabled = bool(config.submit_enabled)
        self._seen_client_order_ids: set[str] = set()

    def _module(self) -> Any:
        if self._mt5 is None:
            try:
                import MetaTrader5 as mt5  # type: ignore
            except ImportError as exc:  # pragma: no cover - platform dependent
                raise MT5DemoExecutionError(
                    "MetaTrader5 package is unavailable. Install the optional MT5 "
                    "runtime on a machine with MetaTrader 5."
                ) from exc
            self._mt5 = mt5
        return self._mt5

    def _last_error(self) -> Any:
        mt5 = self._module()
        fn = getattr(mt5, "last_error", None)
        return fn() if callable(fn) else None

    def connect(
        self,
        *,
        terminal_path: str | None = None,
        login: int | None = None,
        password: str | None = None,
        server: str | None = None,
    ) -> None:
        """Attach to a local MT5 terminal and optionally log into a DEMO account.

        Credentials are supplied at runtime only; they are never persisted here.
        """

        mt5 = self._module()
        ok = mt5.initialize(terminal_path) if terminal_path else mt5.initialize()
        if not ok:
            raise MT5DemoExecutionError(f"MT5 initialize failed: {self._last_error()}")

        try:
            if login is not None:
                kwargs: dict[str, Any] = {}
                if password is not None:
                    kwargs["password"] = password
                if server is not None:
                    kwargs["server"] = server
                if not mt5.login(int(login), **kwargs):
                    raise MT5DemoExecutionError(f"MT5 login failed: {self._last_error()}")
            self._assert_demo_account()
            self._connected = True
        except Exception:
            try:
                mt5.shutdown()
            finally:
                self._connected = False
            raise

    def shutdown(self) -> None:
        if self._mt5 is not None:
            try:
                self._mt5.shutdown()
            finally:
                self._connected = False

    @property
    def connected(self) -> bool:
        return bool(self._connected)

    @property
    def submission_enabled(self) -> bool:
        return bool(self._submission_enabled)

    def set_demo_submission_enabled(self, enabled: bool) -> None:
        """Enable/disable DEMO order submission at runtime.

        Enabling is permitted only while a DEMO account is connected and is
        revalidated immediately. This never permits real-money accounts.
        """

        if enabled:
            if not self._connected:
                raise MT5DemoSafetyError("MT5_DEMO_NOT_CONNECTED")
            self._assert_demo_account()
        self._submission_enabled = bool(enabled)

    def account_summary(self) -> dict[str, Any]:
        """Return non-secret connected DEMO account metadata for a local UI."""

        if not self._connected:
            return {"connected": False, "submission_enabled": self.submission_enabled}
        info = self._assert_demo_account()
        return {
            "connected": True,
            "submission_enabled": self.submission_enabled,
            "login": int(getattr(info, "login", 0) or 0),
            "server": str(getattr(info, "server", "") or ""),
            "trade_mode": int(getattr(info, "trade_mode", -1)),
            "balance": float(getattr(info, "balance", 0.0) or 0.0),
            "equity": float(getattr(info, "equity", 0.0) or 0.0),
            "margin": float(getattr(info, "margin", 0.0) or 0.0),
            "margin_free": float(getattr(info, "margin_free", 0.0) or 0.0),
            "currency": str(getattr(info, "currency", "") or ""),
            "company": str(getattr(info, "company", "") or ""),
        }

    def search_symbols(self, query: str = "", *, limit: int = 100) -> list[str]:
        """Return broker symbol names visible to the terminal.

        This is for symbol mapping in the local UI. It performs no trading.
        """

        if not self._connected:
            raise MT5DemoSafetyError("MT5_DEMO_NOT_CONNECTED")
        mt5 = self._module()
        symbols = mt5.symbols_get()
        if symbols is None:
            raise MT5DemoExecutionError(
                f"symbols_get failed: {self._last_error()}"
            )
        needle = str(query).strip().lower()
        names = []
        for item in symbols:
            name = str(getattr(item, "name", "") or "").strip()
            if not name:
                continue
            if needle and needle not in name.lower():
                continue
            names.append(name)
            if len(names) >= max(1, int(limit)):
                break
        return names


    def copy_closed_bars(
        self,
        venue_symbol: str,
        timeframe: str,
        *,
        count: int = 500,
    ) -> list[dict[str, float | int]]:
        """Return completed MT5 OHLCV bars only.

        The current forming bar is excluded by requesting from position 1.
        Returned rows contain UTC epoch seconds and standard OHLCV fields.
        """

        if not self._connected:
            raise MT5DemoSafetyError("MT5_DEMO_NOT_CONNECTED")
        mt5 = self._module()
        tf_name = str(timeframe).strip().lower()
        mapping = {
            "1m": "TIMEFRAME_M1",
            "5m": "TIMEFRAME_M5",
            "15m": "TIMEFRAME_M15",
            "30m": "TIMEFRAME_M30",
            "1h": "TIMEFRAME_H1",
            "4h": "TIMEFRAME_H4",
            "1d": "TIMEFRAME_D1",
        }
        const_name = mapping.get(tf_name)
        if const_name is None:
            raise ValueError(f"unsupported MT5 timeframe: {timeframe}")
        tf_value = getattr(mt5, const_name, None)
        if tf_value is None:
            raise MT5DemoExecutionError(f"MT5 constant unavailable: {const_name}")

        if not mt5.symbol_select(str(venue_symbol), True):
            info = mt5.symbol_info(str(venue_symbol))
            if info is None:
                raise MT5DemoExecutionError(
                    f"MT5 symbol not found: {venue_symbol}"
                )

        rows = mt5.copy_rates_from_pos(
            str(venue_symbol),
            tf_value,
            1,
            max(2, int(count)),
        )
        if rows is None:
            raise MT5DemoExecutionError(
                f"copy_rates_from_pos failed for {venue_symbol}: {self._last_error()}"
            )

        out: list[dict[str, float | int]] = []
        for row in rows:
            getter = (
                (lambda name: row[name])
                if hasattr(row, "dtype") and getattr(row.dtype, "names", None)
                else (lambda name: getattr(row, name))
            )
            out.append(
                {
                    "time": int(getter("time")),
                    "open": float(getter("open")),
                    "high": float(getter("high")),
                    "low": float(getter("low")),
                    "close": float(getter("close")),
                    "tick_volume": float(getter("tick_volume")),
                    "spread": float(getter("spread")),
                    "real_volume": float(getter("real_volume")),
                }
            )
        return out

    def bot_positions(
        self,
        venue_symbol: str,
        *,
        magic: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return positions owned by this bot magic number for one symbol."""

        if not self._connected:
            raise MT5DemoSafetyError("MT5_DEMO_NOT_CONNECTED")
        mt5 = self._module()
        rows = mt5.positions_get(symbol=str(venue_symbol))
        if rows is None:
            raise MT5DemoExecutionError(
                f"positions_get failed for {venue_symbol}: {self._last_error()}"
            )
        expected_magic = int(self.config.magic if magic is None else magic)
        out: list[dict[str, Any]] = []
        for pos in rows:
            pos_magic = int(getattr(pos, "magic", 0) or 0)
            if pos_magic != expected_magic:
                continue
            out.append(
                {
                    "ticket": int(getattr(pos, "ticket", 0) or 0),
                    "symbol": str(getattr(pos, "symbol", "") or ""),
                    "type": int(getattr(pos, "type", -1)),
                    "volume": float(getattr(pos, "volume", 0.0) or 0.0),
                    "price_open": float(getattr(pos, "price_open", 0.0) or 0.0),
                    "sl": float(getattr(pos, "sl", 0.0) or 0.0),
                    "tp": float(getattr(pos, "tp", 0.0) or 0.0),
                    "profit": float(getattr(pos, "profit", 0.0) or 0.0),
                    "time": int(getattr(pos, "time", 0) or 0),
                    "time_msc": int(getattr(pos, "time_msc", 0) or 0),
                    "comment": str(getattr(pos, "comment", "") or ""),
                    "magic": pos_magic,
                }
            )
        return out


    def market_snapshot(self, venue_symbol: str) -> dict[str, float]:
        """Return the current executable quote and spread in basis points."""

        if not self._connected:
            raise MT5DemoSafetyError("MT5_DEMO_NOT_CONNECTED")
        mt5 = self._module()
        if not mt5.symbol_select(str(venue_symbol), True):
            info = mt5.symbol_info(str(venue_symbol))
            if info is None:
                raise MT5DemoExecutionError(
                    f"MT5 symbol not found: {venue_symbol}"
                )
        tick = mt5.symbol_info_tick(str(venue_symbol))
        if tick is None:
            raise MT5DemoExecutionError(
                f"symbol_info_tick failed for {venue_symbol}: {self._last_error()}"
            )
        bid = self._finite_positive(getattr(tick, "bid", 0.0), "bid")
        ask = self._finite_positive(getattr(tick, "ask", 0.0), "ask")
        if ask < bid:
            raise MT5DemoSafetyError("CROSSED_MT5_QUOTE")
        mid = (bid + ask) / 2.0
        return {
            "bid": float(bid),
            "ask": float(ask),
            "mid": float(mid),
            "spread_bps": float((ask - bid) / mid * 10_000.0),
        }

    def close_bot_position(
        self,
        *,
        ticket: int,
        venue_symbol: str,
        now: datetime | None = None,
    ) -> MT5DemoCloseResult:
        """Close exactly one bot-owned DEMO position by ticket.

        The MT5 position ticket is supplied explicitly so hedging accounts do
        not accidentally open an opposite position instead of closing the
        intended one.
        """

        if not self._connected:
            raise MT5DemoSafetyError("MT5_DEMO_NOT_CONNECTED")
        if not self._submission_enabled:
            raise MT5DemoSafetyError("MT5_DEMO_SUBMISSION_DISABLED")
        if int(ticket) <= 0:
            raise ValueError("position ticket must be positive")

        account = self._assert_demo_account()
        mt5 = self._module()
        rows = mt5.positions_get(ticket=int(ticket))
        if rows is None:
            raise MT5DemoExecutionError(
                f"positions_get(ticket={ticket}) failed: {self._last_error()}"
            )
        if len(rows) != 1:
            raise MT5DemoSafetyError(
                f"POSITION_TICKET_NOT_UNIQUE_OR_GONE ticket={ticket} count={len(rows)}"
            )

        pos = rows[0]
        pos_symbol = str(getattr(pos, "symbol", "") or "")
        if pos_symbol != str(venue_symbol):
            raise MT5DemoSafetyError(
                f"POSITION_SYMBOL_MISMATCH ticket={ticket} "
                f"expected={venue_symbol} actual={pos_symbol}"
            )
        pos_magic = int(getattr(pos, "magic", 0) or 0)
        if pos_magic != int(self.config.magic):
            raise MT5DemoSafetyError(
                f"POSITION_MAGIC_MISMATCH ticket={ticket} magic={pos_magic}"
            )

        lots = self._finite_positive(getattr(pos, "volume", 0.0), "position_volume")
        position_type = int(getattr(pos, "type", -1))
        buy_position = int(getattr(mt5, "POSITION_TYPE_BUY", 0))
        sell_position = int(getattr(mt5, "POSITION_TYPE_SELL", 1))
        if position_type == buy_position:
            close_side = OrderSide.SELL
        elif position_type == sell_position:
            close_side = OrderSide.BUY
        else:
            raise MT5DemoSafetyError(
                f"UNSUPPORTED_POSITION_TYPE ticket={ticket} type={position_type}"
            )

        ts = now or datetime.now(timezone.utc)
        _, executable_price = self._validated_market_state(
            venue_symbol=pos_symbol,
            side=close_side,
            now=ts,
        )
        order_type = (
            mt5.ORDER_TYPE_BUY
            if close_side is OrderSide.BUY
            else mt5.ORDER_TYPE_SELL
        )
        filling_mode = (
            self.config.filling_mode
            if self.config.filling_mode is not None
            else mt5.ORDER_FILLING_RETURN
        )
        payload = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos_symbol,
            "volume": float(lots),
            "type": order_type,
            "position": int(ticket),
            "price": float(executable_price),
            "deviation": int(self.config.max_deviation_points),
            "magic": int(self.config.magic),
            "comment": f"mbot:close:{int(ticket)}"[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_mode,
        }

        check = mt5.order_check(payload)
        if check is None:
            raise MT5DemoExecutionError(
                f"close order_check returned None: {self._last_error()}"
            )
        if int(getattr(check, "retcode", -1)) != 0:
            raise MT5DemoExecutionError(
                f"close order_check rejected retcode={getattr(check, 'retcode', None)} "
                f"comment={getattr(check, 'comment', '')}"
            )

        result = mt5.order_send(payload)
        if result is None:
            raise MT5DemoExecutionError(
                f"close order_send returned None: {self._last_error()}"
            )

        retcode = int(getattr(result, "retcode", -1))
        done = int(getattr(mt5, "TRADE_RETCODE_DONE", 10009))
        partial = int(getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010))
        placed = int(getattr(mt5, "TRADE_RETCODE_PLACED", 10008))
        if retcode not in {done, partial, placed}:
            raise MT5DemoExecutionError(
                f"close order_send rejected retcode={retcode} "
                f"comment={getattr(result, 'comment', '')}"
            )

        if retcode == done:
            status = "CLOSED_DEMO"
        elif retcode == partial:
            status = "PARTIALLY_CLOSED_DEMO"
        else:
            status = "CLOSE_ACKNOWLEDGED_DEMO"

        filled_lots = float(getattr(result, "volume", 0.0) or 0.0)
        fill_price = float(getattr(result, "price", 0.0) or 0.0)
        if fill_price <= 0.0:
            fill_price = float(executable_price)

        return MT5DemoCloseResult(
            ticket=int(ticket),
            venue_symbol=pos_symbol,
            closed_side=close_side.value,
            status=status,
            retcode=retcode,
            order_ticket=int(getattr(result, "order", 0) or 0),
            deal_ticket=int(getattr(result, "deal", 0) or 0),
            requested_lots=float(lots),
            filled_lots=float(filled_lots),
            executable_price=float(executable_price),
            fill_price=float(fill_price),
            account_login=int(getattr(account, "login", 0) or 0),
            account_server=str(getattr(account, "server", "") or ""),
            timestamp=ts.astimezone(timezone.utc),
            comment=str(getattr(result, "comment", "") or ""),
        )

    def _assert_demo_account(self) -> Any:
        mt5 = self._module()
        info = mt5.account_info()
        if info is None:
            raise MT5DemoSafetyError(f"MT5 account_info unavailable: {self._last_error()}")

        demo_mode = int(getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0))
        trade_mode = int(getattr(info, "trade_mode", -1))
        if trade_mode != demo_mode:
            raise MT5DemoSafetyError(
                f"REAL_OR_NON_DEMO_ACCOUNT_REFUSED trade_mode={trade_mode}"
            )
        if not bool(getattr(info, "trade_allowed", False)):
            raise MT5DemoSafetyError("ACCOUNT_TRADING_NOT_ALLOWED")
        if not bool(getattr(info, "trade_expert", False)):
            raise MT5DemoSafetyError("EXPERT_TRADING_NOT_ALLOWED")
        return info

    def _resolve_symbol(self, canonical_symbol: str) -> str:
        canonical = str(canonical_symbol).strip()
        if canonical not in self.config.allowed_symbols:
            raise MT5DemoSafetyError(f"SYMBOL_NOT_ALLOWLISTED: {canonical}")
        venue_symbol = str(self.config.symbol_map.get(canonical, canonical)).strip()
        if not venue_symbol:
            raise MT5DemoSafetyError(f"EMPTY_VENUE_SYMBOL: {canonical}")
        return venue_symbol

    @staticmethod
    def _finite_positive(value: Any, name: str) -> float:
        x = float(value)
        if not math.isfinite(x) or x <= 0.0:
            raise MT5DemoExecutionError(f"invalid {name}")
        return x

    @staticmethod
    def _volume_decimals(step: float) -> int:
        text = f"{step:.12f}".rstrip("0")
        return len(text.split(".", 1)[1]) if "." in text else 0

    def _lots_from_requested_notional(
        self,
        request: ExecutionRequest,
        price: float,
        info: Any,
    ) -> tuple[float, float]:
        contract_size = self._finite_positive(
            getattr(info, "trade_contract_size", 0.0), "trade_contract_size"
        )
        volume_min = self._finite_positive(
            getattr(info, "volume_min", 0.0), "volume_min"
        )
        volume_max = self._finite_positive(
            getattr(info, "volume_max", 0.0), "volume_max"
        )
        volume_step = self._finite_positive(
            getattr(info, "volume_step", 0.0), "volume_step"
        )
        if volume_max < volume_min:
            raise MT5DemoExecutionError("invalid broker volume bounds")

        raw_lots = float(request.notional) / (price * contract_size)
        lots = math.floor((raw_lots + 1e-14) / volume_step) * volume_step
        lots = round(lots, self._volume_decimals(volume_step))
        if lots < volume_min - 1e-12:
            raise MT5DemoSafetyError(
                f"REQUEST_BELOW_BROKER_MIN_VOLUME raw_lots={raw_lots:.12g} min={volume_min}"
            )
        if lots > volume_max + 1e-12:
            raise MT5DemoSafetyError(
                f"REQUEST_ABOVE_BROKER_MAX_VOLUME lots={lots} max={volume_max}"
            )
        return float(lots), contract_size

    def _validated_market_state(
        self,
        *,
        venue_symbol: str,
        side: OrderSide,
        now: datetime,
    ) -> tuple[Any, float]:
        mt5 = self._module()
        info = mt5.symbol_info(venue_symbol)
        if info is None:
            raise MT5DemoExecutionError(f"MT5 symbol not found: {venue_symbol}")
        if not bool(getattr(info, "visible", False)):
            if not mt5.symbol_select(venue_symbol, True):
                raise MT5DemoExecutionError(
                    f"symbol_select failed for {venue_symbol}: {self._last_error()}"
                )
            info = mt5.symbol_info(venue_symbol)
            if info is None:
                raise MT5DemoExecutionError(
                    f"symbol disappeared after select: {venue_symbol}"
                )

        tick = mt5.symbol_info_tick(venue_symbol)
        if tick is None:
            raise MT5DemoExecutionError(f"no executable tick for {venue_symbol}")

        bid = self._finite_positive(getattr(tick, "bid", 0.0), "bid")
        ask = self._finite_positive(getattr(tick, "ask", 0.0), "ask")
        if ask < bid:
            raise MT5DemoSafetyError("CROSSED_MT5_QUOTE")
        mid = (bid + ask) / 2.0
        spread_bps = (ask - bid) / mid * 10_000.0
        if spread_bps > self.config.max_spread_bps:
            raise MT5DemoSafetyError(
                f"SPREAD_TOO_WIDE spread_bps={spread_bps:.4f}"
            )

        tick_ms = getattr(tick, "time_msc", None)
        tick_s = getattr(tick, "time", None)
        if tick_ms not in (None, 0):
            tick_time = datetime.fromtimestamp(
                float(tick_ms) / 1000.0, tz=timezone.utc
            )
        elif tick_s not in (None, 0):
            tick_time = datetime.fromtimestamp(float(tick_s), tz=timezone.utc)
        else:
            raise MT5DemoSafetyError("MT5_TICK_TIMESTAMP_UNAVAILABLE")

        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        age = (now.astimezone(timezone.utc) - tick_time).total_seconds()
        if age < -5.0:
            raise MT5DemoSafetyError("MT5_TICK_TIMESTAMP_IN_FUTURE")
        if age > self.config.max_tick_age_seconds:
            raise MT5DemoSafetyError(f"STALE_MT5_TICK age_seconds={age:.3f}")

        price = ask if side is OrderSide.BUY else bid
        return info, float(price)

    def _validate_stops(
        self,
        *,
        side: OrderSide,
        price: float,
        stop_loss: float | None,
        take_profit: float | None,
        info: Any,
    ) -> tuple[float, float]:
        if self.config.require_server_side_stop and stop_loss is None:
            raise MT5DemoSafetyError("SERVER_SIDE_STOP_REQUIRED")

        sl = (
            0.0
            if stop_loss is None
            else self._finite_positive(stop_loss, "stop_loss")
        )
        tp = (
            0.0
            if take_profit is None
            else self._finite_positive(take_profit, "take_profit")
        )

        if side is OrderSide.BUY:
            if sl and not sl < price:
                raise MT5DemoSafetyError(
                    "BUY_STOP_MUST_BE_BELOW_EXECUTABLE_PRICE"
                )
            if tp and not tp > price:
                raise MT5DemoSafetyError(
                    "BUY_TARGET_MUST_BE_ABOVE_EXECUTABLE_PRICE"
                )
        else:
            if sl and not sl > price:
                raise MT5DemoSafetyError(
                    "SELL_STOP_MUST_BE_ABOVE_EXECUTABLE_PRICE"
                )
            if tp and not tp < price:
                raise MT5DemoSafetyError(
                    "SELL_TARGET_MUST_BE_BELOW_EXECUTABLE_PRICE"
                )

        point = float(getattr(info, "point", 0.0) or 0.0)
        stops_level = float(getattr(info, "trade_stops_level", 0.0) or 0.0)
        min_distance = point * stops_level
        if min_distance > 0.0:
            if sl and abs(price - sl) + 1e-12 < min_distance:
                raise MT5DemoSafetyError("STOP_INSIDE_BROKER_STOPS_LEVEL")
            if tp and abs(price - tp) + 1e-12 < min_distance:
                raise MT5DemoSafetyError("TARGET_INSIDE_BROKER_STOPS_LEVEL")
        return sl, tp

    def execute(
        self,
        request: ExecutionRequest,
        *,
        stop_loss: float | None,
        take_profit: float | None = None,
        now: datetime | None = None,
    ) -> MT5DemoExecutionResult:
        """Validate and submit one market order to an MT5 DEMO account."""

        if not self._connected:
            raise MT5DemoSafetyError("MT5_DEMO_NOT_CONNECTED")
        if not self._submission_enabled:
            raise MT5DemoSafetyError("MT5_DEMO_SUBMISSION_DISABLED")
        if request.order_type is not OrderType.MARKET:
            raise MT5DemoSafetyError(
                "ONLY_MARKET_ORDERS_SUPPORTED_IN_MT5_DEMO_ADAPTER"
            )
        if request.client_order_id in self._seen_client_order_ids:
            raise MT5DemoSafetyError(
                f"DUPLICATE_CLIENT_ORDER_ID: {request.client_order_id}"
            )
        if not request.client_order_id.strip():
            raise ValueError("client_order_id is required")
        if not math.isfinite(float(request.quantity)) or request.quantity <= 0.0:
            raise ValueError("request quantity must be finite and positive")
        if (
            not math.isfinite(float(request.reference_price))
            or request.reference_price <= 0.0
        ):
            raise ValueError(
                "request reference_price must be finite and positive"
            )
        if request.notional > self.config.max_order_notional:
            raise MT5DemoSafetyError(
                f"MAX_ORDER_NOTIONAL_BREACH notional={request.notional:.8f}"
            )

        account = self._assert_demo_account()
        venue_symbol = self._resolve_symbol(request.symbol)
        ts = now or datetime.now(timezone.utc)
        info, executable_price = self._validated_market_state(
            venue_symbol=venue_symbol,
            side=request.side,
            now=ts,
        )
        lots, contract_size = self._lots_from_requested_notional(
            request, executable_price, info
        )
        sl, tp = self._validate_stops(
            side=request.side,
            price=executable_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            info=info,
        )

        mt5 = self._module()
        order_type = (
            mt5.ORDER_TYPE_BUY
            if request.side is OrderSide.BUY
            else mt5.ORDER_TYPE_SELL
        )
        filling_mode = (
            self.config.filling_mode
            if self.config.filling_mode is not None
            else mt5.ORDER_FILLING_RETURN
        )
        comment = f"mbot:{request.client_order_id}"[:31]
        payload = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": venue_symbol,
            "volume": lots,
            "type": order_type,
            "price": executable_price,
            "sl": sl,
            "tp": tp,
            "deviation": int(self.config.max_deviation_points),
            "magic": int(self.config.magic),
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_mode,
        }

        check = mt5.order_check(payload)
        if check is None:
            raise MT5DemoExecutionError(
                f"order_check returned None: {self._last_error()}"
            )
        if int(getattr(check, "retcode", -1)) != 0:
            raise MT5DemoExecutionError(
                f"order_check rejected retcode={getattr(check, 'retcode', None)} "
                f"comment={getattr(check, 'comment', '')}"
            )

        result = mt5.order_send(payload)
        if result is None:
            raise MT5DemoExecutionError(
                f"order_send returned None: {self._last_error()}"
            )

        retcode = int(getattr(result, "retcode", -1))
        done = int(getattr(mt5, "TRADE_RETCODE_DONE", 10009))
        partial = int(getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010))
        placed = int(getattr(mt5, "TRADE_RETCODE_PLACED", 10008))
        if retcode not in {done, partial, placed}:
            raise MT5DemoExecutionError(
                f"order_send rejected retcode={retcode} "
                f"comment={getattr(result, 'comment', '')}"
            )

        self._seen_client_order_ids.add(request.client_order_id)
        filled_lots = float(getattr(result, "volume", 0.0) or 0.0)
        fill_price = float(getattr(result, "price", 0.0) or 0.0)
        if fill_price <= 0.0:
            fill_price = executable_price

        if retcode == done:
            status = "FILLED_DEMO"
        elif retcode == partial:
            status = "PARTIALLY_FILLED_DEMO"
        else:
            status = "ACKNOWLEDGED_DEMO"

        shortfall_bps: float | None = None
        if fill_price > 0.0 and executable_price > 0.0:
            direction = 1.0 if request.side is OrderSide.BUY else -1.0
            shortfall_bps = (
                direction
                * (fill_price - executable_price)
                / executable_price
                * 10_000.0
            )

        return MT5DemoExecutionResult(
            client_order_id=request.client_order_id,
            canonical_symbol=request.symbol,
            venue_symbol=venue_symbol,
            side=request.side.value,
            status=status,
            retcode=retcode,
            order_ticket=int(getattr(result, "order", 0) or 0),
            deal_ticket=int(getattr(result, "deal", 0) or 0),
            requested_quantity=float(request.quantity),
            submitted_lots=float(lots),
            filled_lots=float(filled_lots),
            filled_base_quantity=float(filled_lots * contract_size),
            decision_reference_price=float(request.reference_price),
            executable_price=float(executable_price),
            fill_price=float(fill_price),
            implementation_shortfall_bps=shortfall_bps,
            account_login=int(getattr(account, "login", 0) or 0),
            account_server=str(getattr(account, "server", "") or ""),
            account_trade_mode=int(getattr(account, "trade_mode", -1)),
            contract_size=float(contract_size),
            timestamp=ts.astimezone(timezone.utc),
            comment=str(getattr(result, "comment", "") or ""),
        )
