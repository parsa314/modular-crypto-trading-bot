from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

import numpy as np
from sklearn.preprocessing import StandardScaler

from .holdout import Partition, authorize_partition_access


class AuditedScaler:
    def __init__(self) -> None:
        self.scaler = StandardScaler()
        self.fit_row_ids: tuple[str, ...] = ()

    def fit(self, values: np.ndarray, *, row_ids: Iterable[str], partitions: Iterable[Partition]) -> "AuditedScaler":
        ids, parts = tuple(row_ids), tuple(partitions)
        if len(ids) != len(values) or len(parts) != len(values):
            raise ValueError("fit metadata length mismatch")
        for part in parts:
            authorize_partition_access(part, purpose="FIT_PREPROCESSOR")
        self.scaler.fit(values)
        self.fit_row_ids = ids
        return self

    def transform(self, values: np.ndarray) -> np.ndarray:
        return self.scaler.transform(values)


class AuditedCalibrator:
    def __init__(self) -> None:
        self.fit_row_ids: tuple[str, ...] = ()

    def fit(self, probabilities: np.ndarray, targets: np.ndarray, *, row_ids: Iterable[str], partitions: Iterable[Partition]) -> "AuditedCalibrator":
        ids, parts = tuple(row_ids), tuple(partitions)
        if len(probabilities) != len(targets) or len(ids) != len(targets) or len(parts) != len(targets):
            raise ValueError("calibration metadata length mismatch")
        for part in parts:
            authorize_partition_access(part, purpose="CALIBRATE")
        self.fit_row_ids = ids
        return self


class OrderIntentRegistry:
    def __init__(self) -> None:
        self._ids: set[str] = set()

    def register(self, order_intent_id: str) -> None:
        if not order_intent_id.strip():
            raise ValueError("order_intent_id required")
        if order_intent_id in self._ids:
            raise RuntimeError("duplicate order intent")
        self._ids.add(order_intent_id)


@dataclass
class PortfolioCashLedger:
    cash: float

    def reserve(self, notional: float) -> None:
        if not math.isfinite(notional) or notional <= 0:
            raise ValueError("notional must be finite and positive")
        if notional > self.cash:
            raise RuntimeError("insufficient portfolio cash")
        self.cash -= notional


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

    def evaluate(self, *, equity: float, peak_equity: float, new_risk: bool = True) -> bool:
        if equity <= 0 or peak_equity <= 0 or equity > peak_equity:
            raise ValueError("invalid equity state")
        if 1.0 - equity / peak_equity >= self.kill_threshold:
            self.killed = True
        return not (self.killed and new_risk)
