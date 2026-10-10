"""Execution-venue adapters used for non-production validation.

The canonical strategy/research engine remains venue-agnostic. Adapters in
this package must be fail-closed and must not silently promote PAPER/TESTNET
results to LIVE authorization.
"""

from .mt5_demo import (
    MT5DemoCloseResult,
    MT5DemoConfig,
    MT5DemoExecutionError,
    MT5DemoExecutionResult,
    MT5DemoExecutor,
    MT5DemoSafetyError,
)

__all__ = [
    "MT5DemoCloseResult",
    "MT5DemoConfig",
    "MT5DemoExecutionError",
    "MT5DemoExecutionResult",
    "MT5DemoExecutor",
    "MT5DemoSafetyError",
]
