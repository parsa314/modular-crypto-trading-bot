from __future__ import annotations

from typing import Protocol
from ...jamalian_data import OrderBook, Ticker
from ...exchanges.bitpin_adapter import BitpinAdapter, WalletBalance


class PublicMarketDataPort(Protocol):
    def get_all_prices(self) -> tuple[Ticker, ...]: ...
    def get_orderbook(self, symbol: str) -> OrderBook: ...


class AuthenticatedReadPort(Protocol):
    def get_my_balance(self) -> tuple[WalletBalance, ...]: ...


class BitpinReadOnly:
    """Composition facade over the tested GET/auth-read allowlist.

No place_order or cancel_order API; URLs/credentials remain in the existing
adapter. Current exchange schema is unverified until a read contract check.
"""
    def __init__(self, adapter: BitpinAdapter) -> None:
        self._adapter = adapter

    def get_all_prices(self) -> tuple[Ticker, ...]:
        return self._adapter.get_all_prices()

    def get_orderbook(self, symbol: str) -> OrderBook:
        return self._adapter.get_orderbook(symbol)

    def get_my_balance(self) -> tuple[WalletBalance, ...]:
        return self._adapter.get_my_balance()
