"""Compatibility import for the durable ledger; implementation lives in execution."""

from .execution.ledger import LiveLedger

__all__ = ["LiveLedger"]
