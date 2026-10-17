"""Dependency-injected market adapters. Terminal credentials never belong here."""

from .mt5_broker import MT5Broker, MT5DemoError

__all__ = ["MT5Broker", "MT5DemoError"]
