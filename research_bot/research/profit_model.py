"""Frozen logistic baseline with calendar walk-forward and held-out Platt fit.

There is no threshold search. Train/validation predictions are fit diagnostics;
only test predictions are out-of-sample. Fold failures remain explicit records.
"""

from __future__ import annotations

from dataclasses import dataclass
import warnings

import numpy as np
import pandas as pd
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .profit_features import FEATURE_COLUMNS, add_profit_labels, build_profit_features


MIN_TRAIN_ROWS = 200
MIN_CALIBRATION_ROWS = 100
MIN_CLASS_ROWS = 5


class FoldRejected(ValueError):
    """Insufficient or unusable fit data; never substitute fake probabilities."""


def _utc(value: str | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")


@dataclass(frozen=True)
class ProfitFold:
    fold_id: int
    train_start: pd.Timestamp
    validation_start: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    embargo_hours: int = 24

    def __post_init__(self) -> None:
        if isinstance(self.fold_id, bool) or not isinstance(self.fold_id, int) or self.fold_id < 0:
            raise ValueError("fold_id must be a non-negative integer")
        if isinstance(self.embargo_hours, bool) or self.embargo_hours != 24:
            raise ValueError("the frozen protocol requires a 24h embargo")
        for name in ("train_start", "validation_start", "test_start", "test_end"):
            timestamp = _utc(getattr(self, name))
            if pd.isna(timestamp) or timestamp != timestamp.normalize().replace(day=1):
                raise ValueError("fold bounds must be UTC calendar-month boundaries")
            object.__setattr__(self, name, timestamp)
        if (self.validation_start != self.train_start + pd.DateOffset(months=6)
                or self.test_start != self.validation_start + pd.DateOffset(months=1)
                or self.test_end != self.test_start + pd.DateOffset(months=1)):
            raise ValueError("the frozen fold protocol requires calendar months 6/1/1")

    def bounds(self, phase: str) -> tuple[pd.Timestamp, pd.Timestamp]:
        return {"train": (self.train_start, self.validation_start),
                "validation": (self.validation_start, self.test_start),
                "test": (self.test_start, self.test_end)}[phase]


def calendar_folds(start: str | pd.Timestamp, end: str | pd.Timestamp) -> list[ProfitFold]:
    """Six train months, one calibration month, one test month; end is exclusive."""
    first, last = _utc(start), _utc(end)
    if pd.isna(first) or pd.isna(last) or first >= last or any(t != t.normalize().replace(day=1) for t in (first, last)):
        raise ValueError("study bounds must be increasing UTC calendar-month boundaries")
    folds = []
    train_start = first
    while train_start + pd.DateOffset(months=8) <= last:
        folds.append(ProfitFold(len(folds), train_start, train_start + pd.DateOffset(months=6),
                                train_start + pd.DateOffset(months=7), train_start + pd.DateOffset(months=8)))
        train_start += pd.DateOffset(months=1)
    return folds


def _phase_rows(frame: pd.DataFrame, fold: ProfitFold, phase: str, *, fitting: bool) -> pd.DataFrame:
    start, end = fold.bounds(phase)
    eligible = frame["timestamp"].ge(start) & frame["timestamp"].lt(end) & frame["feature_ready"]
    if fitting:
        # Purge outcomes reaching the boundary and leave a further 24h embargo.
        eligible &= frame["target"].notna() & frame["label_end"].lt(end - pd.Timedelta(hours=fold.embargo_hours))
    return frame.loc[eligible].copy()


def probability_diagnostics(target: np.ndarray, probability: np.ndarray) -> dict:
    """Fixed ten equal-width bins, with probability 1 assigned to the last bin."""
    y, p = np.asarray(target, dtype=float), np.asarray(probability, dtype=float)
    if y.ndim != 1 or y.shape != p.shape or not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
        raise ValueError("probabilities must be aligned, finite and in [0, 1]")
    valid = np.isfinite(y)
    if not np.isin(y[valid], [0, 1]).all():
        raise ValueError("targets must be binary or missing")
    y, p = y[valid], p[valid]
    if not len(y):
        return {"n": 0, "brier": None, "ece_10": None}
    bins = np.minimum((p * 10).astype(int), 9)
    ece = sum(float(np.mean(bins == b)) * abs(float(p[bins == b].mean() - y[bins == b].mean()))
              for b in range(10) if (bins == b).any())
    return {"n": len(y), "brier": float(brier_score_loss(y, p)), "ece_10": ece}


@dataclass
class FittedProfitModel:
    fold: ProfitFold
    pipeline: Pipeline
    calibrator: LogisticRegression
    metadata: dict

    def predict(self, frame: pd.DataFrame, phase: str) -> pd.DataFrame:
        rows = _phase_rows(frame, self.fold, phase, fitting=phase != "test")
        output = rows[["timestamp", "feature_available_at", "target", "label_end"]].copy()
        if len(rows):
            matrix = rows[list(FEATURE_COLUMNS)]
            scores = self.pipeline.decision_function(matrix)
            output["raw_probability"] = self.pipeline.predict_proba(matrix)[:, 1]
            output["probability"] = self.calibrator.predict_proba(scores.reshape(-1, 1))[:, 1]
        else:
            output["raw_probability"] = pd.Series(index=rows.index, dtype=float)
            output["probability"] = pd.Series(index=rows.index, dtype=float)
        output["fold_id"] = self.fold.fold_id
        output["phase"] = phase
        output["is_out_of_sample"] = phase == "test"
        return output


def fit_profit_fold(frame: pd.DataFrame, fold: ProfitFold) -> FittedProfitModel:
    train = _phase_rows(frame, fold, "train", fitting=True)
    calibration = _phase_rows(frame, fold, "validation", fitting=True)
    for phase, rows, minimum in (("train", train, MIN_TRAIN_ROWS), ("validation", calibration, MIN_CALIBRATION_ROWS)):
        counts = rows["target"].value_counts()
        if len(rows) < minimum or len(counts) != 2 or counts.min() < MIN_CLASS_ROWS:
            raise FoldRejected(f"{phase}: need >= {minimum} finite labeled rows and >= {MIN_CLASS_ROWS} per class")
    pipeline = Pipeline([("scaler", StandardScaler()),
                         # Default L2 regularization works across supported sklearn versions.
                         ("classifier", LogisticRegression(C=1.0, solver="lbfgs", random_state=42,
                                                           max_iter=1000, class_weight=None))])
    calibrator = LogisticRegression(C=1.0, solver="lbfgs", random_state=42, max_iter=1000, class_weight=None)
    with warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        try:
            pipeline.fit(train[list(FEATURE_COLUMNS)], train["target"].astype(int))
            calibration_scores = pipeline.decision_function(calibration[list(FEATURE_COLUMNS)])
            calibrator.fit(calibration_scores.reshape(-1, 1), calibration["target"].astype(int))
        except ConvergenceWarning as exc:
            raise FoldRejected("logistic or Platt fit failed to converge") from exc
    metadata = {
        "fold_id": fold.fold_id, "status": "FITTED", "train_start": fold.train_start.isoformat(),
        "validation_start": fold.validation_start.isoformat(), "test_start": fold.test_start.isoformat(),
        "test_end": fold.test_end.isoformat(), "embargo_hours": fold.embargo_hours,
        "feature_columns": list(FEATURE_COLUMNS), "scaler_fit_phase": "train",
        "classifier": {"C": 1.0, "penalty": "l2", "solver": "lbfgs", "random_state": 42,
                       "max_iter": 1000, "class_weight": None},
        "scaler_mean": pipeline.named_steps["scaler"].mean_.tolist(),
        "calibration_fit_phase": "validation", "fit_diagnostics_are_causal_returns": False,
    }
    for phase, rows in (("train", train), ("validation", calibration)):
        metadata[phase] = {"rows": len(rows), "first_timestamp": rows["timestamp"].min().isoformat(),
                           "last_timestamp": rows["timestamp"].max().isoformat(),
                           "max_label_end": rows["label_end"].max().isoformat(),
                           "class_counts": {str(int(k)): int(v) for k, v in rows["target"].value_counts().items()}}
    return FittedProfitModel(fold, pipeline, calibrator, metadata)


@dataclass
class WalkForwardResult:
    predictions: pd.DataFrame
    folds: list[dict]
    featured: pd.DataFrame


def walk_forward_predictions(
    ohlcv: pd.DataFrame, *, start: str = "2022-01-01", end: str = "2025-01-01",
    horizon_bars: int = 24, fee_bps: float = 10.0, slippage_bps: float = 5.0,
) -> WalkForwardResult:
    frame = add_profit_labels(build_profit_features(ohlcv), horizon_bars=horizon_bars,
                              fee_bps=fee_bps, slippage_bps=slippage_bps)
    folds = calendar_folds(start, end)
    if not folds:
        raise ValueError("study period must provide at least one complete 6/1/1 fold")
    if (frame["timestamp"].min() > _utc(start)
            or frame["timestamp"].max() < _utc(end) - pd.Timedelta(hours=1)):
        raise ValueError("OHLCV must cover the complete configured study period")
    predictions, records = [], []
    for fold in folds:
        try:
            fitted = fit_profit_fold(frame, fold)
        except FoldRejected as exc:
            records.append({"fold_id": fold.fold_id, "status": "REJECTED", "reason": str(exc),
                            "test_start": fold.test_start.isoformat(), "test_end": fold.test_end.isoformat()})
            continue
        record = fitted.metadata.copy()
        record["diagnostics"] = {}
        for phase in ("train", "validation", "test"):
            prediction = fitted.predict(frame, phase)
            predictions.append(prediction)
            record["diagnostics"][phase] = {
                kind: probability_diagnostics(prediction["target"].to_numpy(), prediction[column].to_numpy())
                for kind, column in (("raw", "raw_probability"), ("calibrated", "probability"))}
        records.append(record)
    columns = ["timestamp", "feature_available_at", "target", "label_end", "raw_probability", "probability", "fold_id", "phase", "is_out_of_sample"]
    output = pd.concat(predictions, ignore_index=True) if predictions else pd.DataFrame(columns=columns)
    return WalkForwardResult(output, records, frame)
