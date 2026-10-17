"""Explicit simulation, Binance Spot Testnet and Windows MT5 Demo adapters."""
from .base import AdapterError, ExecutionRejected, MarketAdapter, OrderReceipt
from .binance_adapter import BinanceAdapter
from .mt5_adapter import MT5Adapter
from .paper_adapter import PaperAdapter

__all__ = [
    "AdapterError", "ExecutionRejected", "MarketAdapter", "OrderReceipt",
    "BinanceAdapter", "MT5Adapter", "PaperAdapter",
]
