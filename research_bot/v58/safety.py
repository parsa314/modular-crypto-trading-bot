from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

import numpy as np
from sklearn.preprocessing import StandardScaler

from .holdout import Partition, authorize_partition_access


def _validate_fit_rows(row_ids: Iterable[str], partitions: Iterable[Partition],
                       *, count: int, purpose: str) -> tuple[str, ...]:
    ids, parts = tuple(row_ids), tuple(partitions)
    if count == 0 or len(ids) != count or len(parts) != count:
        raise ValueError("fit metadata length mismatch or empty input")
    if any(not isinstance(row, str) or not row or row != row.strip() for row in ids):
        raise ValueError("row_ids must be nonempty canonical strings")
    if len(set(ids)) != len(ids):
        raise ValueError("row_ids must be unique")
    for part in parts:
        authorize_partition_access(part, purpose=purpose)
    return ids


class AuditedScaler:
    def __init__(self) -> None:
        self.scaler = StandardScaler()
        self.fit_row_ids: tuple[str, ...] = ()

    def fit(self, values: np.ndarray, *, row_ids: Iterable[str], partitions: Iterable[Partition]) -> "AuditedScaler":
        values = np.asarray(values, dtype=float)
        if values.ndim != 2 or values.shape[1] == 0 or not np.isfinite(values).all():
            raise ValueError("scaler values must be a finite two-dimensional matrix")
        ids = _validate_fit_rows(row_ids, partitions, count=len(values), purpose="FIT_PREPROCESSOR")
        self.scaler.fit(values)
        self.fit_row_ids = ids
        return self

    def transform(self, values: np.ndarray) -> np.ndarray:
        return self.scaler.transform(values)


class AuditedCalibrator:
    """Validation-partition input guard; calibration fitting is not implemented."""

    def __init__(self) -> None:
        self.fit_row_ids: tuple[str, ...] = ()

    def fit(self, probabilities: np.ndarray, targets: np.ndarray, *, row_ids: Iterable[str], partitions: Iterable[Partition]) -> "AuditedCalibrator":
        self.validate_inputs(probabilities, targets, row_ids=row_ids, partitions=partitions)
        raise NotImplementedError("calibration fitting is not implemented; no model was fitted")

    def validate_inputs(self, probabilities: np.ndarray, targets: np.ndarray, *,
                        row_ids: Iterable[str], partitions: Iterable[Partition]) -> tuple[str, ...]:
        probabilities, targets = np.asarray(probabilities, dtype=float), np.asarray(targets)
        if probabilities.ndim not in (1, 2) or targets.ndim != 1 or len(probabilities) != len(targets):
            raise ValueError("calibration shape mismatch")
        ids = _validate_fit_rows(row_ids, partitions, count=len(targets), purpose="CALIBRATE")
        if not np.isfinite(probabilities).all() or np.any((probabilities < 0) | (probabilities > 1)):
            raise ValueError("probabilities must be finite and within [0, 1]")
        classes = 2 if probabilities.ndim == 1 else probabilities.shape[1]
        if classes < 2 or (probabilities.ndim == 2 and not np.allclose(probabilities.sum(axis=1), 1)):
            raise ValueError("multiclass probabilities must sum to one")
        if not np.isin(targets, np.arange(classes)).all():
            raise ValueError("targets must be valid class indices")
        return ids


class OrderIntentRegistry:
    def __init__(self) -> None:
        self._ids: set[str] = set()

    def register(self, order_intent_id: str) -> None:
        self.ensure_available(order_intent_id)
        self._ids.add(order_intent_id)

    def ensure_available(self, order_intent_id: str) -> None:
        if (not isinstance(order_intent_id, str) or not order_intent_id
                or order_intent_id != order_intent_id.strip()):
            raise ValueError("order_intent_id must be a nonempty canonical string")
        if order_intent_id in self._ids:
            raise RuntimeError("duplicate order intent")


@dataclass
class PortfolioCashLedger:
    cash: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.cash) or self.cash < 0:
            raise ValueError("cash must be finite and nonnegative")

    def reserve(self, notional: float) -> None:
        self.validate_reservation(notional)
        self.cash -= notional

    def validate_reservation(self, notional: float) -> None:
        self.__post_init__()
        if not math.isfinite(notional) or notional <= 0:
            raise ValueError("notional must be finite and positive")
        if notional > self.cash:
            raise RuntimeError("insufficient portfolio cash")


@dataclass(frozen=True)
class AdmissionDecision:
    admitted: bool
    reason: str


def cost_aware_admission(*, expected_edge: float, round_trip_cost_bps: float,
                         abstain: bool = False, risk_veto: bool = False) -> AdmissionDecision:
    if abstain:
        return AdmissionDecision(False, "NO_TRADE")
    if risk_veto:
        return AdmissionDecision(False, "RISK_VETO")
    cost = round_trip_cost_bps / 10_000.0
    if not all(math.isfinite(x) for x in (expected_edge, cost)) or cost < 0:
        raise ValueError("invalid edge/cost")
    if expected_edge <= cost:
        return AdmissionDecision(False, "COST_GATE")
    return AdmissionDecision(True, "ADMITTED")


@dataclass
class DrawdownRiskGate:
    kill_threshold: float = 0.05
    killed: bool = False

    def __post_init__(self) -> None:
        if not math.isfinite(self.kill_threshold) or not 0 < self.kill_threshold <= 1:
            raise ValueError("kill_threshold must be finite and in (0, 1]")

    def evaluate(self, *, equity: float, peak_equity: float, new_risk: bool = True) -> bool:
        self.__post_init__()
        if (not all(math.isfinite(x) for x in (equity, peak_equity))
                or equity <= 0 or peak_equity <= 0 or equity > peak_equity):
            raise ValueError("invalid equity state")
        if equity <= peak_equity * (1.0 - self.kill_threshold):
            self.killed = True
        return not (self.killed and new_risk)


def admit_research_order(*, order_intent_id: str, notional: float,
                         expected_edge: float, round_trip_cost_bps: float,
                         equity: float, peak_equity: float,
                         registry: OrderIntentRegistry, ledger: PortfolioCashLedger,
                         risk_gate: DrawdownRiskGate, abstain: bool = False) -> AdmissionDecision:
    """Admit an abstract intent in a sequential, in-memory research simulation.

    This creates no paper/live order and implements only the stated drawdown,
    cost, cash and identity gates, not a complete portfolio risk engine.
    """
    decision = cost_aware_admission(expected_edge=expected_edge,
                                    round_trip_cost_bps=round_trip_cost_bps,
                                    abstain=abstain)
    if not decision.admitted:
        return decision
    if not risk_gate.evaluate(equity=equity, peak_equity=peak_equity, new_risk=True):
        return AdmissionDecision(False, "RISK_VETO")
    registry.ensure_available(order_intent_id)
    ledger.validate_reservation(notional)
    # Both mutations follow validation; this helper is deliberately single-threaded.
    registry.register(order_intent_id)
    ledger.reserve(notional)
    return decision
