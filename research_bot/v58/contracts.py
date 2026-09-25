from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import math


PAPER_EXECUTION = False
LIVE_EXECUTION = False
INSTITUTIONAL_RESEARCH = True


class StrategyArm(str, Enum):
    ARM_A = "ARM_A"
    ARM_B = "ARM_B"
    ARM_C = "ARM_C"
    ARM_D = "ARM_D"
    ARM_E = "ARM_E"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class TargetClass(str, Enum):
    TP = "TP"
    SL = "SL"
    TIMEOUT = "TIMEOUT"


class RegimeLabel(str, Enum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    HIGH_VOL = "HIGH_VOL"
    LOW_VOL = "LOW_VOL"
    TRANSITION = "TRANSITION"
    UNKNOWN = "UNKNOWN"


def require_aware_utc(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def require_finite_positive(value: float, *, name: str) -> float:
    value = float(value)
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{name} must be finite and > 0")
    return value


@dataclass(frozen=True)
class PITValue:
    effective_at: datetime
    available_at: datetime
    observed_at: datetime
    source: str
    source_hash: str
    value: float
    confidence: float = 1.0

    def validate_for(self, decision_at: datetime) -> None:
        decision = require_aware_utc(decision_at, name="decision_at")
        available = require_aware_utc(self.available_at, name="available_at")
        require_aware_utc(self.effective_at, name="effective_at")
        require_aware_utc(self.observed_at, name="observed_at")
        if available > decision:
            raise ValueError("PIT availability violation: available_at is after decision_at")
        if not self.source.strip() or not self.source_hash.strip():
            raise ValueError("source and source_hash are required")
        if not math.isfinite(float(self.value)):
            raise ValueError("PIT value must be finite")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be in [0, 1]")


def assert_research_only() -> None:
    if PAPER_EXECUTION or LIVE_EXECUTION:
        raise RuntimeError("V58 execution firewall violation")
