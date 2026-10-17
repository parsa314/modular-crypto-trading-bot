"""Local, explicitly simulated spot broker for offline AI integration.

This is never labeled Binance Testnet or MT5 Demo. It executes on observed marks
only; replay orchestration must call update_bar in chronological order.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from .base import AdapterError, ExecutionRejected, MarketAdapter, OrderReceipt, positive, side, validate_bars


class PaperAdapter(MarketAdapter):
    mode = "local_paper_simulation"

    def __init__(self, *, initial_cash: float = 10000.0, fee_bps: float = 15.0,
                 max_position_fraction: float = 0.10) -> None:
        self.cash = positive(initial_cash, "initial_cash")
        self.fee_bps = float(fee_bps)
        if not math.isfinite(self.fee_bps) or not 0 <= self.fee_bps < 10000:
            raise ValueError("fee_bps must be finite and between 0 and 10000")
        self.max_position_fraction = positive(max_position_fraction, "max_position_fraction")
        if self.max_position_fraction > 1:
            raise ValueError("max_position_fraction cannot exceed one")
        self.positions: dict[str, float] = {}
        self.prices: dict[str, float] = {}
        self._history: dict[tuple[str, str], pd.DataFrame] = {}
        self.receipts: list[OrderReceipt] = []

    def update_bar(self, symbol: str, bar: dict[str, Any], *, timeframe: str = "M15") -> None:
        """Observe exactly one closed OHLCV bar; equal/older bars are rejected."""
        if "timestamp" not in bar:
            raise ValueError("Closed bar needs a timestamp")
        timestamp = pd.Timestamp(bar["timestamp"])
        if timestamp.tzinfo is None:
            raise ValueError("Bar timestamp must be timezone-aware")
        one = pd.DataFrame([{name: bar[name] for name in ("open", "high", "low", "close", "volume")}],
                           index=pd.DatetimeIndex([timestamp]))
        one = validate_bars(one)
        key = (symbol, timeframe)
        previous = self._history.get(key)
        if previous is not None and one.index[0] <= previous.index[-1]:
            raise ValueError("Simulation bars must arrive in strictly increasing order")
        self._history[key] = one if previous is None else pd.concat([previous, one]).iloc[-2000:]
        self.prices[symbol] = float(one.close.iloc[0])

    def get_data(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        if int(count) < 1:
            raise ValueError("count must be positive")
        data = self._history.get((symbol, timeframe))
        if data is None:
            raise AdapterError("No observed simulation bars for this symbol/timeframe")
        return data.iloc[-int(count):].copy()

    def get_price(self, symbol: str) -> float:
        if symbol not in self.prices:
            raise AdapterError("No observed simulation price")
        return positive(self.prices[symbol], "price")

    def get_balance(self) -> dict[str, float | str]:
        equity = self.cash + sum(size * self.get_price(symbol) for symbol, size in self.positions.items())
        return {"cash": self.cash, "equity": equity, "currency": "USDT"}

    def get_positions(self) -> list[dict[str, Any]]:
        return [{"symbol": symbol, "size": size, "direction": "buy", "price": self.get_price(symbol),
                 "notional_account_currency": size * self.get_price(symbol)}
                for symbol, size in self.positions.items() if size > 0]

    def place_order(self, symbol: str, direction: str, size: float, *,
                    stop_loss: float | None = None, take_profit: float | None = None,
                    **kwargs: Any) -> OrderReceipt:
        if stop_loss is not None or take_profit is not None or kwargs:
            raise ExecutionRejected("Local simulation supports market spot orders only; bracket orders are not implemented")
        direction, quantity, price = side(direction), positive(size, "size"), self.get_price(symbol)
        notional, held = quantity * price, self.positions.get(symbol, 0.0)
        fee = notional * self.fee_bps / 10000
        if direction == "buy":
            equity = float(self.get_balance()["equity"])
            if notional + fee > self.cash + 1e-10:
                raise ExecutionRejected("Insufficient simulated cash")
            # Evaluate against equity after fees, not the inflated pre-fill NAV.
            if (held + quantity) * price > (equity - fee) * self.max_position_fraction + 1e-10:
                raise ExecutionRejected("Simulated position would exceed configured fraction")
            self.cash -= notional + fee
            self.positions[symbol] = held + quantity
        else:
            if quantity > held + 1e-12:
                raise ExecutionRejected("Simulation is spot long-only; cannot sell more than held")
            self.cash += notional - fee
            self.positions[symbol] = max(0.0, held - quantity)
        receipt = OrderReceipt(str(len(self.receipts) + 1), symbol, direction, quantity, price,
                               "FILLED", self.mode, fee)
        self.receipts.append(receipt)
        return receipt
