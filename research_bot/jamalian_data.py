"""Validated Decimal observations shared by the Jamalian research components."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import re
from typing import Protocol

from .contracts import EvidenceStamp


def decimal_value(value: str | int | Decimal, *, positive: bool = False) -> Decimal:
    # Floats must not silently become exact financial values.
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("use a Decimal, decimal string or integer")
    try:
        result = Decimal(value)
    except Exception:
        raise ValueError("invalid decimal value") from None
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ValueError("decimal value must be finite and nonnegative/positive")
    return result


def aware(at: datetime) -> None:
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")


def currencies(symbol: str) -> tuple[str, str]:
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9]+_[A-Z0-9]+", symbol):
        raise ValueError("symbol must be BASE_QUOTE")
    base, quote = symbol.split("_")
    if base == quote:
        raise ValueError("base and quote must differ")
    return base, quote


def available(stamp: EvidenceStamp, at: datetime, max_age: timedelta | None = None) -> None:
    aware(at)
    aware(stamp.observed_at)
    aware(stamp.available_at)
    if not stamp.source.strip() or not stamp.is_available(at):
        raise ValueError("evidence is unavailable at decision time")
    if max_age is not None:
        if max_age <= timedelta(0) or at - stamp.observed_at > max_age:
            raise ValueError("stale evidence")


@dataclass(frozen=True)
class Candle:
    """One closed bar's close; observed_at is BAR CLOSE, not bar open."""
    symbol: str
    close: Decimal
    stamp: EvidenceStamp

    def __post_init__(self) -> None:
        currencies(self.symbol)
        object.__setattr__(self, "close", decimal_value(self.close, positive=True))


@dataclass(frozen=True)
class BookLevel:
    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "price", decimal_value(self.price, positive=True))
        object.__setattr__(self, "quantity", decimal_value(self.quantity, positive=True))


@dataclass(frozen=True)
class OrderBook:
    symbol: str
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    stamp: EvidenceStamp

    def __post_init__(self) -> None:
        currencies(self.symbol)
        object.__setattr__(self, "bids", tuple(self.bids))
        object.__setattr__(self, "asks", tuple(self.asks))
        if not self.bids or not self.asks:
            raise ValueError("both sides of the book are required")
        for side, reverse in ((self.bids, True), (self.asks, False)):
            prices = [level.price for level in side]
            if len(set(prices)) != len(prices) or prices != sorted(prices, reverse=reverse):
                raise ValueError("book must have unique, sorted prices")
        if self.bids[0].price >= self.asks[0].price:
            raise ValueError("crossed or locked book")


@dataclass(frozen=True)
class Ticker:
    symbol: str
    price: Decimal
    stamp: EvidenceStamp

    def __post_init__(self) -> None:
        currencies(self.symbol)
        object.__setattr__(self, "price", decimal_value(self.price, positive=True))


class MarketDataPort(Protocol):
    def get_all_prices(self) -> tuple[Ticker, ...]: ...
    def get_orderbook(self, symbol: str) -> OrderBook: ...
