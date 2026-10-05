from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Mapping, Sequence

from .hashing import stable_hash
from ..execution.constitution import RISK_CONSTITUTION_V1


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class GateStatus(str, Enum):
    PASS = "PASS"
    VETO = "VETO"
    ABSTAIN = "ABSTAIN"


class Regime(str, Enum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    HIGH_VOL = "HIGH_VOL"
    LOW_VOL = "LOW_VOL"
    TRANSITION = "TRANSITION"
    UNKNOWN = "UNKNOWN"


TARGET_CLASSES = ("TP", "SL", "TIMEOUT")


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be a canonical nonempty string")
    return value


def _finite(value: float, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite numeric data")
    value = float(value)
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(f"{name} must be finite" + (" and positive" if positive else ""))
    return value


def _probabilities(values: Mapping[str, float]) -> dict[str, float]:
    if tuple(values.keys()) != TARGET_CLASSES:
        raise ValueError("probabilities must preserve TP, SL, TIMEOUT class order")
    p = {name: _finite(values[name], f"probabilities.{name}") for name in TARGET_CLASSES}
    if any(value < 0 or value > 1 for value in p.values()):
        raise ValueError("probabilities must be in [0,1]")
    if not math.isclose(sum(p.values()), 1.0, abs_tol=1e-8):
        raise ValueError("probabilities must sum to one")
    return p


@dataclass(frozen=True)
class SignalCandidate:
    event_id: str
    strategy_id: str
    strategy_family: str
    symbol: str
    venue: str
    direction: Direction
    decision_at: datetime
    entry_time: datetime
    entry_price: float
    stop_price: float
    target_price: float
    horizon_bars: int
    regime: Regime
    regime_confidence: float
    feature_snapshot_id: str
    data_version: str
    strategy_version: str
    source_hash: str
    confirmations: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for name in (
            "event_id", "strategy_id", "strategy_family", "symbol", "venue",
            "feature_snapshot_id", "data_version", "strategy_version", "source_hash",
        ):
            _text(getattr(self, name), name)
        decision = _utc(self.decision_at, "decision_at")
        entry_time = _utc(self.entry_time, "entry_time")
        if entry_time <= decision:
            raise ValueError("entry_time must be strictly after decision_at")
        entry = _finite(self.entry_price, "entry_price", positive=True)
        stop = _finite(self.stop_price, "stop_price", positive=True)
        target = _finite(self.target_price, "target_price", positive=True)
        if self.direction is Direction.LONG and not stop < entry < target:
            raise ValueError("LONG requires stop < entry < target")
        if self.direction is Direction.SHORT and not target < entry < stop:
            raise ValueError("SHORT requires target < entry < stop")
        if not isinstance(self.horizon_bars, int) or self.horizon_bars <= 0:
            raise ValueError("horizon_bars must be a positive integer")
        confidence = _finite(self.regime_confidence, "regime_confidence")
        if not 0 <= confidence <= 1:
            raise ValueError("regime_confidence must be in [0,1]")
        if len(set(self.confirmations)) != len(self.confirmations):
            raise ValueError("confirmations must be unique")

    @property
    def identity_hash(self) -> str:
        return stable_hash(
            {
                "event_id": self.event_id,
                "strategy_id": self.strategy_id,
                "symbol": self.symbol,
                "venue": self.venue,
                "direction": self.direction,
                "decision_at": self.decision_at,
                "entry_time": self.entry_time,
                "feature_snapshot_id": self.feature_snapshot_id,
                "data_version": self.data_version,
                "strategy_version": self.strategy_version,
                "source_hash": self.source_hash,
            }
        )


@dataclass(frozen=True)
class ModelPrediction:
    event_id: str
    model_id: str
    model_version: str
    available_at: datetime
    probabilities: Mapping[str, float]
    calibrated: bool
    calibration_id: str
    entropy: float
    shift_score: float

    def __post_init__(self) -> None:
        _text(self.event_id, "event_id")
        _text(self.model_id, "model_id")
        _text(self.model_version, "model_version")
        _utc(self.available_at, "available_at")
        _probabilities(self.probabilities)
        _text(self.calibration_id, "calibration_id")
        entropy = _finite(self.entropy, "entropy")
        shift = _finite(self.shift_score, "shift_score")
        if not 0 <= entropy <= 1:
            raise ValueError("entropy must be in [0,1]")
        if shift < 0:
            raise ValueError("shift_score must be nonnegative")

    def normalized_probabilities(self) -> dict[str, float]:
        return _probabilities(self.probabilities)


@dataclass(frozen=True)
class UncertaintyAssessment:
    event_id: str
    prediction_set: tuple[str, ...]
    conformity_score: float
    empirical_coverage: float | None
    abstain: bool
    reason: str

    def __post_init__(self) -> None:
        _text(self.event_id, "event_id")
        if not self.prediction_set or any(label not in TARGET_CLASSES for label in self.prediction_set):
            raise ValueError("prediction_set must contain TP/SL/TIMEOUT labels")
        if len(set(self.prediction_set)) != len(self.prediction_set):
            raise ValueError("prediction_set labels must be unique")
        _finite(self.conformity_score, "conformity_score")
        if self.empirical_coverage is not None:
            coverage = _finite(self.empirical_coverage, "empirical_coverage")
            if not 0 <= coverage <= 1:
                raise ValueError("empirical_coverage must be in [0,1]")
        _text(self.reason, "reason")


@dataclass(frozen=True)
class EconomicDecision:
    event_id: str
    status: GateStatus
    expected_utility: float
    round_trip_cost_bps: float
    reward_fraction: float
    loss_fraction: float
    timeout_penalty_fraction: float
    uncertainty_penalty_fraction: float
    reason: str

    def __post_init__(self) -> None:
        _text(self.event_id, "event_id")
        for name in (
            "expected_utility", "round_trip_cost_bps", "reward_fraction",
            "loss_fraction", "timeout_penalty_fraction", "uncertainty_penalty_fraction",
        ):
            value = _finite(getattr(self, name), name)
            if name != "expected_utility" and value < 0:
                raise ValueError(f"{name} must be nonnegative")
        _text(self.reason, "reason")
        if self.status is GateStatus.PASS and self.expected_utility <= 0:
            raise ValueError("PASS economic decision requires positive expected utility")


@dataclass(frozen=True)
class PortfolioState:
    timestamp: datetime
    equity: float
    cash: float
    peak_equity: float
    gross_exposure: float
    asset_exposure: Mapping[str, float]
    recent_returns: tuple[float, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _utc(self.timestamp, "timestamp")
        equity = _finite(self.equity, "equity", positive=True)
        cash = _finite(self.cash, "cash")
        peak = _finite(self.peak_equity, "peak_equity", positive=True)
        gross = _finite(self.gross_exposure, "gross_exposure")
        if cash < 0 or peak < equity or gross < 0:
            raise ValueError("invalid portfolio state")
        for symbol, amount in self.asset_exposure.items():
            _text(symbol, "asset_exposure.symbol")
            if _finite(amount, f"asset_exposure.{symbol}") < 0:
                raise ValueError("asset exposures must be nonnegative")
        if any(not math.isfinite(float(r)) for r in self.recent_returns):
            raise ValueError("recent_returns must be finite")

    @property
    def drawdown(self) -> float:
        return 1.0 - float(self.equity) / float(self.peak_equity)


@dataclass(frozen=True)
class FinancialDecision:
    event_id: str
    status: GateStatus
    approved_notional: float
    risk_budget: float
    stop_risk_fraction: float
    projected_asset_weight: float
    projected_gross_exposure: float
    cvar95: float | None
    drawdown: float
    reason: str

    def __post_init__(self) -> None:
        _text(self.event_id, "event_id")
        for name in (
            "approved_notional", "risk_budget", "stop_risk_fraction",
            "projected_asset_weight", "projected_gross_exposure", "drawdown",
        ):
            value = _finite(getattr(self, name), name)
            if value < 0:
                raise ValueError(f"{name} must be nonnegative")
        if self.cvar95 is not None and _finite(self.cvar95, "cvar95") < 0:
            raise ValueError("cvar95 must be nonnegative")
        _text(self.reason, "reason")
        if self.status is not GateStatus.PASS and self.approved_notional != 0:
            raise ValueError("vetoed/abstained financial decisions must approve zero notional")


@dataclass(frozen=True)
class FinalResearchDecision:
    event_id: str
    status: GateStatus
    action: str
    approved_notional: float
    reason: str
    audit_hash: str
    risk_constitution_hash: str = RISK_CONSTITUTION_V1.sha256

    def __post_init__(self) -> None:
        _text(self.event_id, "event_id")
        _text(self.action, "action")
        _text(self.reason, "reason")
        _text(self.audit_hash, "audit_hash")
        _text(self.risk_constitution_hash, 'risk_constitution_hash')
        notional = _finite(self.approved_notional, "approved_notional")
        if notional < 0:
            raise ValueError("approved_notional must be nonnegative")
        if self.action not in {"NO_TRADE", "ADMITTED_RESEARCH_SIMULATION"}:
            raise ValueError("V59 phase-1 final action is research-only")
        if self.action == "NO_TRADE" and notional != 0:
            raise ValueError("NO_TRADE must approve zero notional")
