"""Common closed-bar and execution contract for explicitly named market modes."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_DOWN
import math
from typing import Any

import pandas as pd


class AdapterError(RuntimeError):
    """A provider/runtime failure without credential-bearing diagnostics."""


class ExecutionRejected(AdapterError):
    """An order failed local execution/risk checks before submission."""


@dataclass(frozen=True)
class OrderReceipt:
    order_id: str
    symbol: str
    direction: str
    size: float
    price: float
    status: str
    mode: str
    fee: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def positive(value: float, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ExecutionRejected(f"{name} must be finite and positive")
    return number


def floor_step(value: float, step: float) -> float:
    amount, increment = Decimal(str(positive(value, "size"))), Decimal(str(positive(step, "step")))
    return float((amount / increment).to_integral_value(rounding=ROUND_DOWN) * increment)


def side(direction: str) -> str:
    aliases = {"buy": "buy", "long": "buy", "sell": "sell", "short": "sell"}
    try:
        return aliases[str(direction).lower()]
    except KeyError:
        raise ExecutionRejected("direction must be buy or sell") from None


def validate_bars(frame: pd.DataFrame) -> pd.DataFrame:
    columns = ["open", "high", "low", "close", "volume"]
    if frame.empty:
        raise AdapterError("No closed bars returned by provider")
    data = frame.loc[:, columns].astype(float).copy()
    data.index = pd.to_datetime(frame.index, utc=True)
    data.index.name = "timestamp"
    if data.index.has_duplicates or not data.index.is_monotonic_increasing:
        raise AdapterError("Provider returned duplicate or unordered timestamps")
    values = data.to_numpy()
    if not all(math.isfinite(float(x)) for x in values.flat):
        raise AdapterError("Provider returned non-finite OHLCV")
    if (data[["open", "high", "low", "close"]] <= 0).any().any() or (data.volume < 0).any():
        raise AdapterError("Provider returned invalid prices or volume")
    if ((data.high < data[["open", "low", "close"]].max(axis=1)) |
            (data.low > data[["open", "high", "close"]].min(axis=1))).any():
        raise AdapterError("Provider returned inconsistent OHLC")
    return data


class MarketAdapter(ABC):
    """Size units are base asset units on spot/simulation and lots on MT5.

    ``get_data`` returns *closed* bars only, with UTC DatetimeIndex and OHLCV.
    Provider adapters never fall back to simulation on connection failure.
    """

    mode: str

    @abstractmethod
    def get_data(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame: ...

    @abstractmethod
    def get_price(self, symbol: str) -> float: ...

    @abstractmethod
    def place_order(self, symbol: str, direction: str, size: float, **kwargs: Any) -> OrderReceipt: ...

    def size_for_fraction(
        self, symbol: str, fraction: float, equity: float, *, stop_loss: float | None = None,
    ) -> float:
        """Legacy spot allocation helper; MT5 overrides this for stop-risk sizing."""
        return self.size_for_allocation(symbol, fraction, equity, stop_loss=stop_loss)

    def size_for_allocation(
        self, symbol: str, fraction: float, equity: float, *, stop_loss: float | None = None,
    ) -> float:
        """Convert an account-equity notional allocation into provider size units.

        This is the uniform RL allocation interface. Stop-loss risk is checked
        independently by the execution adapter and never reinterprets fraction.
        """
        fraction = positive(fraction, "fraction")
        if fraction > 1:
            raise ExecutionRejected("fraction cannot exceed one")
        return fraction * positive(equity, "equity") / self.get_price(symbol)
