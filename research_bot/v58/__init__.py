"""V58 research-only event/meta-trading infrastructure.

No paper or live execution is authorized from this namespace.
"""

PHASE1_IMPLEMENTED = True

from .contracts import (
    LIVE_EXECUTION,
    PAPER_EXECUTION,
    Direction,
    RegimeLabel,
    StrategyArm,
    TargetClass,
)

__all__ = [
    "PHASE1_IMPLEMENTED",
    "LIVE_EXECUTION",
    "PAPER_EXECUTION",
    "Direction",
    "RegimeLabel",
    "StrategyArm",
    "TargetClass",
]
