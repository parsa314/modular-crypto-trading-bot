"""
Minimal contracts for the demo bot (compatible with research architecture).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Tuple
from uuid import uuid4


class Decision(str, Enum):
    REJECT = "REJECT"
    NO_TRADE = "NO_TRADE"
    BUY_CANDIDATE = "BUY_CANDIDATE"
    HOLD = "HOLD"
    EXIT = "EXIT"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class EvidenceStamp:
    source: str
    observed_at: datetime
    available_at: datetime

    def is_available(self, decision_time: datetime) -> bool:
        return self.available_at <= decision_time


@dataclass(frozen=True)
class HypothesisCandidate:
    hypothesis_id: str
    strategy_id: str
    asset: str
    side: Side
    timestamp: datetime
    expected_return: float
    expected_cost: float
    risk_penalty: float
    uncertainty_penalty: float
    confidence: float
    reasons: Tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def net_edge(self) -> float:
        return (
            self.expected_return
            - self.expected_cost
            - self.risk_penalty
            - self.uncertainty_penalty
        )

    @staticmethod
    def make_id(strategy_id: str, asset: str, event_key: str) -> str:
        return f"{strategy_id}:{asset}:{event_key}"


@dataclass(frozen=True)
class SignalEvidence:
    asset: str
    timestamp: datetime
    expected_return: float
    expected_cost: float
    risk_penalty: float
    uncertainty_penalty: float
    confidence: float
    decision: Decision
    reasons: Tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def net_alpha(self) -> float:
        return (
            self.expected_return
            - self.expected_cost
            - self.risk_penalty
            - self.uncertainty_penalty
        )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())[:12]
