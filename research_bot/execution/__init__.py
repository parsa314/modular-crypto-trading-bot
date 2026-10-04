"""Execution foundations and lazy compatibility with the v0 paper simulator.

Importing the package or Stage 0 foundations does not load the historical shared
research contracts. Existing ``from research_bot.execution import ...`` callers
retain the simulator API; no execution method or behavior is changed here.
"""
from importlib import import_module

__all__ = ["OrderSide", "OrderType", "ExecutionPolicy", "ExecutionRequest",
           "ExecutionFill", "PaperExecutionEngine", "ExecutionMode"]


def __getattr__(name):
    if name in __all__:
        value = getattr(import_module(".legacy", __name__), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
