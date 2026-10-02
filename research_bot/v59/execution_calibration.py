from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import math
from typing import Iterable

import numpy as np
import pandas as pd

from .execution import ExecutionAssumptions
from .hashing import stable_hash


@dataclass(frozen=True)
class ExecutionCalibrationSample:
    sample_id: str
    timestamp: datetime
    side: str
    reference_mid: float
    fill_price: float
    notional: float
    depth_notional_10bps: float
    spread_bps: float
    fee_bps_per_side: float
    source_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.sample_id, str) or not self.sample_id.strip():
            raise ValueError("sample_id is required")
        if not isinstance(self.timestamp, datetime) or self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        for name in (
            "reference_mid", "fill_price", "notional", "depth_notional_10bps",
            "spread_bps", "fee_bps_per_side",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if self.reference_mid <= 0 or self.fill_price <= 0 or self.notional <= 0 or self.depth_notional_10bps <= 0:
            raise ValueError("price/notional/depth must be positive")
        if not isinstance(self.source_hash, str) or not self.source_hash.strip():
            raise ValueError("source_hash is required")

    @property
    def participation(self) -> float:
        return float(self.notional) / float(self.depth_notional_10bps)

    @property
    def adverse_fill_bps(self) -> float:
        if self.side == "BUY":
            return max(0.0, (float(self.fill_price) - float(self.reference_mid)) / float(self.reference_mid) * 10_000.0)
        return max(0.0, (float(self.reference_mid) - float(self.fill_price)) / float(self.reference_mid) * 10_000.0)

    @property
    def residual_after_half_spread_bps(self) -> float:
        return max(0.0, self.adverse_fill_bps - float(self.spread_bps) / 2.0)


@dataclass(frozen=True)
class ExecutionCalibrationConfig:
    train_fraction: float = 0.70
    min_total_samples: int = 100
    min_validation_samples: int = 20
    max_validation_mae_bps: float = 5.0
    max_p90_underprediction_bps: float = 8.0
    max_validation_underprediction_rate: float = 0.50

    def __post_init__(self) -> None:
        if not 0.5 <= float(self.train_fraction) < 1.0:
            raise ValueError("train_fraction must be in [0.5,1)")
        for name in ("min_total_samples", "min_validation_samples"):
            value = getattr(self, name)
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("max_validation_mae_bps", "max_p90_underprediction_bps"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if not 0 <= float(self.max_validation_underprediction_rate) <= 1:
            raise ValueError("max_validation_underprediction_rate must be in [0,1]")


@dataclass(frozen=True)
class ExecutionCalibrationReport:
    status: str
    total_samples: int
    train_samples: int
    validation_samples: int
    fitted_base_bps_per_side: float | None
    fitted_impact_coefficient_bps: float | None
    validation_mae_bps: float | None
    validation_p90_underprediction_bps: float | None
    validation_underprediction_rate: float | None
    train_start: str | None
    train_end: str | None
    validation_start: str | None
    validation_end: str | None
    sample_hash: str
    config_hash: str
    reason: str

    @property
    def report_hash(self) -> str:
        return stable_hash(asdict(self))


def _sample_payload(sample: ExecutionCalibrationSample) -> dict:
    payload = asdict(sample)
    payload["timestamp"] = sample.timestamp.astimezone(timezone.utc).isoformat()
    return payload


def fit_execution_calibration(
    samples: Iterable[ExecutionCalibrationSample],
    config: ExecutionCalibrationConfig | None = None,
) -> ExecutionCalibrationReport:
    cfg = ExecutionCalibrationConfig() if config is None else config
    rows = tuple(sorted(samples, key=lambda x: (x.timestamp, x.sample_id)))
    ids = [row.sample_id for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate calibration sample_id is forbidden")
    sample_hash = stable_hash([_sample_payload(row) for row in rows])
    config_hash = stable_hash(asdict(cfg))

    if len(rows) < cfg.min_total_samples:
        return ExecutionCalibrationReport(
            status="INSUFFICIENT_SAMPLE",
            total_samples=len(rows),
            train_samples=0,
            validation_samples=0,
            fitted_base_bps_per_side=None,
            fitted_impact_coefficient_bps=None,
            validation_mae_bps=None,
            validation_p90_underprediction_bps=None,
            validation_underprediction_rate=None,
            train_start=None,
            train_end=None,
            validation_start=None,
            validation_end=None,
            sample_hash=sample_hash,
            config_hash=config_hash,
            reason="MIN_TOTAL_SAMPLES_NOT_MET",
        )

    split = int(math.floor(len(rows) * cfg.train_fraction))
    split = min(max(split, 1), len(rows) - 1)
    train = rows[:split]
    validation = rows[split:]
    if len(validation) < cfg.min_validation_samples:
        return ExecutionCalibrationReport(
            status="INSUFFICIENT_SAMPLE",
            total_samples=len(rows),
            train_samples=len(train),
            validation_samples=len(validation),
            fitted_base_bps_per_side=None,
            fitted_impact_coefficient_bps=None,
            validation_mae_bps=None,
            validation_p90_underprediction_bps=None,
            validation_underprediction_rate=None,
            train_start=train[0].timestamp.astimezone(timezone.utc).isoformat(),
            train_end=train[-1].timestamp.astimezone(timezone.utc).isoformat(),
            validation_start=validation[0].timestamp.astimezone(timezone.utc).isoformat(),
            validation_end=validation[-1].timestamp.astimezone(timezone.utc).isoformat(),
            sample_hash=sample_hash,
            config_hash=config_hash,
            reason="MIN_VALIDATION_SAMPLES_NOT_MET",
        )

    x_train = np.column_stack(
        [
            np.ones(len(train), dtype=float),
            np.sqrt(np.array([max(0.0, row.participation) for row in train], dtype=float)),
        ]
    )
    y_train = np.array([row.residual_after_half_spread_bps for row in train], dtype=float)
    beta, *_ = np.linalg.lstsq(x_train, y_train, rcond=None)
    base_bps = max(0.0, float(beta[0]))
    impact_coeff = max(0.0, float(beta[1]))

    x_val = np.column_stack(
        [
            np.ones(len(validation), dtype=float),
            np.sqrt(np.array([max(0.0, row.participation) for row in validation], dtype=float)),
        ]
    )
    y_val = np.array([row.residual_after_half_spread_bps for row in validation], dtype=float)
    pred = base_bps + impact_coeff * x_val[:, 1]
    error = y_val - pred
    abs_error = np.abs(error)
    underprediction = np.maximum(error, 0.0)

    mae = float(np.mean(abs_error))
    p90_under = float(np.quantile(underprediction, 0.90))
    under_rate = float(np.mean(error > 0))
    passed = (
        mae <= cfg.max_validation_mae_bps
        and p90_under <= cfg.max_p90_underprediction_bps
        and under_rate <= cfg.max_validation_underprediction_rate
    )
    status = "CALIBRATION_CANDIDATE_PASSED" if passed else "CALIBRATION_CANDIDATE_REJECTED"
    reason = "VALIDATION_THRESHOLDS_PASSED" if passed else "VALIDATION_THRESHOLDS_FAILED"
    return ExecutionCalibrationReport(
        status=status,
        total_samples=len(rows),
        train_samples=len(train),
        validation_samples=len(validation),
        fitted_base_bps_per_side=base_bps,
        fitted_impact_coefficient_bps=impact_coeff,
        validation_mae_bps=mae,
        validation_p90_underprediction_bps=p90_under,
        validation_underprediction_rate=under_rate,
        train_start=train[0].timestamp.astimezone(timezone.utc).isoformat(),
        train_end=train[-1].timestamp.astimezone(timezone.utc).isoformat(),
        validation_start=validation[0].timestamp.astimezone(timezone.utc).isoformat(),
        validation_end=validation[-1].timestamp.astimezone(timezone.utc).isoformat(),
        sample_hash=sample_hash,
        config_hash=config_hash,
        reason=reason,
    )


def assumptions_from_calibration(
    report: ExecutionCalibrationReport,
    base: ExecutionAssumptions | None = None,
) -> ExecutionAssumptions:
    if report.status != "CALIBRATION_CANDIDATE_PASSED":
        raise ValueError("calibration report is not eligible for assumption promotion")
    if report.fitted_base_bps_per_side is None or report.fitted_impact_coefficient_bps is None:
        raise ValueError("calibration report has no fitted parameters")
    assumptions = ExecutionAssumptions() if base is None else base
    conservative_slippage = float(report.fitted_base_bps_per_side) + float(
        report.validation_p90_underprediction_bps or 0.0
    )
    return replace(
        assumptions,
        slippage_stress_bps_per_side=conservative_slippage,
        impact_coefficient_bps=float(report.fitted_impact_coefficient_bps),
    )


def samples_from_frame(frame: pd.DataFrame) -> list[ExecutionCalibrationSample]:
    required = {
        "sample_id", "timestamp", "side", "reference_mid", "fill_price",
        "notional", "depth_notional_10bps", "spread_bps",
        "fee_bps_per_side", "source_hash",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"missing calibration columns: {sorted(missing)}")
    result = []
    for row in frame.to_dict(orient="records"):
        stamp = pd.Timestamp(row["timestamp"])
        if stamp.tzinfo is None:
            raise ValueError("calibration timestamps must be timezone-aware")
        result.append(
            ExecutionCalibrationSample(
                sample_id=str(row["sample_id"]),
                timestamp=stamp.to_pydatetime(),
                side=str(row["side"]),
                reference_mid=float(row["reference_mid"]),
                fill_price=float(row["fill_price"]),
                notional=float(row["notional"]),
                depth_notional_10bps=float(row["depth_notional_10bps"]),
                spread_bps=float(row["spread_bps"]),
                fee_bps_per_side=float(row["fee_bps_per_side"]),
                source_hash=str(row["source_hash"]),
            )
        )
    return result
