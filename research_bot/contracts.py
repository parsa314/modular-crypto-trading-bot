from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from enum import Enum
from typing import Any, Mapping


class ResearchStatus(str, Enum):
    NOT_TESTED = "NOT_TESTED"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    UNVERIFIED = "UNVERIFIED"
    HYPOTHESIS = "HYPOTHESIS"
    DISCOVERY_CANDIDATE = "DISCOVERY_CANDIDATE"
    VALIDATED_OOS = "VALIDATED_OOS"


class Decision(str, Enum):
    REJECT = "REJECT"
    NO_TRADE = "NO_TRADE"
    BUY_CANDIDATE = "BUY_CANDIDATE"
    CONFIRMED_ENTRY = "CONFIRMED_ENTRY"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    EXIT = "EXIT"


class ExecutionMode(str, Enum):
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    TESTNET = "TESTNET"
    LIVE = "LIVE"


@dataclass(frozen=True)
class EvidenceStamp:
    """Point-in-time provenance for a datum or derived feature.

    ``observed_at`` is when the source event occurred. ``available_at`` is the
    earliest timestamp at which the trading system could legitimately have
    consumed it.  This distinction is mandatory for news/on-chain/fundamental
    data where publication lag can otherwise create look-ahead leakage.
    """

    source: str
    observed_at: datetime
    available_at: datetime
    lineage: str = ""
    reliability_tier: str = "UNRATED"
    status: ResearchStatus = ResearchStatus.UNVERIFIED

    def __post_init__(self) -> None:
        if self.available_at < self.observed_at:
            raise ValueError("available_at cannot precede observed_at")
        for name in ("observed_at", "available_at"):
            value = getattr(self, name)
            if value.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")

    def is_available(self, decision_time: datetime) -> bool:
        if decision_time.tzinfo is None:
            raise ValueError("decision_time must be timezone-aware")
        return self.available_at <= decision_time


@dataclass(frozen=True)
class SignalEvidence:
    asset: str
    timestamp: datetime
    expected_return: float | Decimal
    expected_cost: float | Decimal
    risk_penalty: float | Decimal
    uncertainty_penalty: float | Decimal
    confidence: float
    decision: Decision
    reasons: tuple[str, ...] = ()
    evidence: tuple[EvidenceStamp, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def net_alpha(self) -> float | Decimal:
        values = (self.expected_return,self.expected_cost,self.risk_penalty,self.uncertainty_penalty)
        if any(isinstance(value, Decimal) for value in values):
            with localcontext() as ctx:
                ctx.prec = 80
                values = tuple(value if isinstance(value, Decimal) else Decimal(str(value)) for value in values)
                return values[0] - values[1] - values[2] - values[3]
        return (
            float(self.expected_return)
            - float(self.expected_cost)
            - float(self.risk_penalty)
            - float(self.uncertainty_penalty)
        )


@dataclass(frozen=True)
class ExperimentManifest:
    """Minimum reproducibility metadata attached to every research run."""

    experiment_id: str
    created_at: datetime
    git_commit: str
    dataset_id: str
    feature_set: str
    model_name: str
    random_seed: int
    execution_mode: ExecutionMode
    fee_bps: float
    slippage_bps: float
    notes: str = ""

    @classmethod
    def now(
        cls,
        *,
        experiment_id: str,
        git_commit: str,
        dataset_id: str,
        feature_set: str,
        model_name: str,
        random_seed: int,
        execution_mode: ExecutionMode,
        fee_bps: float,
        slippage_bps: float,
        notes: str = "",
    ) -> "ExperimentManifest":
        return cls(
            experiment_id=experiment_id,
            created_at=datetime.now(timezone.utc),
            git_commit=git_commit,
            dataset_id=dataset_id,
            feature_set=feature_set,
            model_name=model_name,
            random_seed=random_seed,
            execution_mode=execution_mode,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            notes=notes,
        )

