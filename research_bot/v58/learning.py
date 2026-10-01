"""A synthetic-only V58 multiclass learning integration.

This engineering seam cannot train on market venues or unlock a holdout.
Explicit feature names keep labels and realized outcomes outside the model;
callers remain responsible for causal feature construction. Entropy and the
standardized shift score are diagnostics, not certified uncertainty bounds.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import re

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from sklearn import __version__ as sklearn_version
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from .contracts import assert_research_only
from .events import stable_hash


CLASS_ORDER = ("TP", "SL", "TIMEOUT")
WALK_FORWARD_CALIBRATION_FRACTION = 0.20
WALK_FORWARD_TEST_FRACTION = 0.30
_REQUIRED = ("event_id", "venue", "decision_at", "label_available_at", "label")
_FORBIDDEN_FEATURE_PARTS = {
    "label", "labels", "outcome", "outcomes", "target", "future", "exit",
    "realized", "resolved", "pnl", "profit", "net", "gross", "horizon",
}
_FORBIDDEN_FEATURE_MARKERS = ("label", "outcome", "target", "future", "exit", "realized", "resolved")


@dataclass(frozen=True)
class LearningConfig:
    train_fraction: float = 0.6
    calibration_fraction: float = 0.2
    embargo_seconds: int = 14400
    min_train_events: int = 30
    min_calibration_events: int = 10
    min_test_events: int = 10
    random_seed: int = 58

    def __post_init__(self) -> None:
        fractions = (self.train_fraction, self.calibration_fraction)
        if (any(isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not 0 < value < 1 for value in fractions)
                or sum(fractions) >= 1):
            raise ValueError("positive finite split fractions must sum to less than one")
        for name in ("embargo_seconds", "min_train_events", "min_calibration_events",
                     "min_test_events", "random_seed"):
            value = getattr(self, name)
            minimum = 1 if name.startswith("min_") else 0
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        if self.random_seed > 2**32 - 1:
            raise ValueError("random_seed exceeds the supported integer range")


def _validate_feature_rows(events: pd.DataFrame, feature_columns: tuple[str, ...],
                           *, require_labels: bool) -> pd.DataFrame:
    if not isinstance(events, pd.DataFrame) or events.empty:
        raise ValueError("events must be a nonempty DataFrame")
    if events.columns.duplicated().any():
        raise ValueError("duplicate input column names")
    if (not isinstance(feature_columns, tuple) or not feature_columns
            or any(not isinstance(name, str) for name in feature_columns)
            or len(set(feature_columns)) != len(feature_columns)):
        raise ValueError("feature_columns must be a nonempty unique tuple of strings")
    for name in feature_columns:
        if (not re.fullmatch(r"feature_[A-Za-z][A-Za-z0-9_]*", name)
                or _FORBIDDEN_FEATURE_PARTS.intersection(name.lower().split("_"))
                or any(marker in name.lower()[8:] for marker in _FORBIDDEN_FEATURE_MARKERS)
                or name.lower() in {"feature_time_to_event", "feature_bars_observed"}):
            raise ValueError(f"inadmissible feature column: {name}")
    required = _REQUIRED if require_labels else ("event_id", "venue", "decision_at")
    missing = set(required + feature_columns) - set(events.columns)
    if missing:
        raise ValueError(f"missing event/feature columns: {sorted(missing)}")
    # Reject market input before any preprocessing or estimator fitting.
    if not events["venue"].eq("synthetic").all():
        raise PermissionError("learning is restricted to the synthetic venue")
    x = events.loc[:, list(required + feature_columns)].copy().reset_index(drop=True)
    ids = x["event_id"]
    if (not ids.map(lambda value: isinstance(value, str) and bool(value)
                    and value == value.strip()).all() or ids.duplicated().any()):
        raise ValueError("event_id must be a unique canonical nonempty string")
    for name in ("decision_at", "label_available_at") if require_labels else ("decision_at",):
        stamps = []
        for value in x[name]:
            try:
                stamp = pd.Timestamp(value)
                if (pd.isna(stamp) or stamp.tzinfo is None
                        or stamp.utcoffset().total_seconds() != 0):
                    raise ValueError("timestamp must be finite timezone-aware UTC")
            except (ValueError, TypeError, OverflowError) as exc:
                raise ValueError(f"{name} must contain finite timezone-aware UTC timestamps") from exc
            stamps.append(stamp)
        x[name] = pd.to_datetime(stamps, utc=True)
    if require_labels:
        if not (x["label_available_at"] > x["decision_at"]).all():
            raise ValueError("label information must become available after decision_at")
        if not x["label"].isin(CLASS_ORDER).all():
            raise ValueError("labels must be TP, SL or TIMEOUT")
    for name in feature_columns:
        if (not pd.api.types.is_numeric_dtype(x[name])
                or pd.api.types.is_bool_dtype(x[name])
                or pd.api.types.is_complex_dtype(x[name])):
            raise ValueError(f"feature column must be real numeric data: {name}")
        values = x[name].to_numpy(dtype=float, na_value=np.nan)
        if np.isinf(values).any():
            raise ValueError(f"feature column contains infinity: {name}")
        x[name] = values
    # All events sharing a clock retain one role, independently of input order.
    return x.sort_values(["decision_at", "event_id"], kind="mergesort").reset_index(drop=True)


def _validate_events(events: pd.DataFrame, feature_columns: tuple[str, ...]) -> pd.DataFrame:
    return _validate_feature_rows(events, feature_columns, require_labels=True)


def _utc_clock_string(value: str, *, name: str) -> pd.Timestamp:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a UTC timestamp string")
    try:
        timestamp = pd.Timestamp(value)
        if (pd.isna(timestamp) or timestamp.tzinfo is None
                or timestamp.utcoffset().total_seconds() != 0):
            raise ValueError("boundary must be timezone-aware UTC")
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite timezone-aware UTC timestamp") from exc
    return timestamp


def _named_boundary(value: str, clocks: pd.Series) -> pd.Timestamp:
    timestamp = _utc_clock_string(value, name="split boundary")
    if not clocks.eq(timestamp).any():
        raise ValueError("split boundary must name an existing decision clock")
    return timestamp


def _temperature_probabilities(probabilities: np.ndarray, temperature: float) -> np.ndarray:
    logits = np.log(np.clip(probabilities, np.finfo(float).tiny, 1.0)) / temperature
    logits -= logits.max(axis=1, keepdims=True)
    exponentials = np.exp(logits)
    return exponentials / exponentials.sum(axis=1, keepdims=True)


def _metrics(probabilities: np.ndarray, targets: np.ndarray) -> dict:
    true_probability = probabilities[np.arange(len(targets)), targets]
    one_hot = np.eye(len(CLASS_ORDER))[targets]
    return {
        # Multiclass Brier uses the summed class errors (range 0..2).
        "brier": float(np.mean(np.sum((probabilities - one_hot) ** 2, axis=1))),
        "log_loss": float(-np.mean(np.log(np.clip(true_probability, np.finfo(float).tiny, 1.0)))),
        "event_count": int(len(targets)),
    }


def _inference_state(bundle: dict) -> dict:
    """Canonical inference state; its hash is integrity, not authentication."""
    return {"model": bundle["model"], "preprocessing": bundle["preprocessing"],
            "temperature": bundle["calibration"]["temperature"],
            "model_available_at": bundle["model_available_at"]}


def _prediction_records(events: pd.DataFrame, probabilities: np.ndarray,
                        standardized: np.ndarray) -> list[dict]:
    predictions = []
    for index, row in enumerate(events.itertuples(index=False)):
        probability = probabilities[index]
        entropy = -float(np.sum(probability * np.log(np.clip(probability, np.finfo(float).tiny, 1)))) / math.log(3)
        predictions.append({
            "event_id": row.event_id, "decision_at": row.decision_at.isoformat(),
            "probabilities": {name: float(probability[i]) for i, name in enumerate(CLASS_ORDER)},
            "entropy": float(np.clip(entropy, 0, 1)),
            "shift_score": float(np.max(np.abs(standardized[index]))),
        })
    return predictions


def score_frozen_synthetic_baseline(bundle: dict, events: pd.DataFrame) -> list[dict]:
    """Score unlabelled synthetic rows from verified JSON state, without fitting.

    Extra event columns are ignored. No pickle/joblib or estimator loading is
    involved. SHA-256 detects accidental state changes; it does not authenticate
    a model or authorize use of empirical data, holdouts or execution.
    """
    assert_research_only()
    try:
        if (not isinstance(bundle, dict)
                or bundle["classification"] != "SYNTHETIC_ENGINEERING_ONLY"
                or bundle["empirical_training"] is not False
                or bundle["paper_execution"] is not False
                or bundle["live_execution"] is not False):
            raise ValueError("frozen bundle must remain synthetic and execution-disabled")
        model = bundle["model"]
        columns = model["feature_columns"]
        if (not isinstance(columns, list) or not columns
                or model["class_order"] != list(CLASS_ORDER)):
            raise ValueError("invalid frozen feature columns or probability class order")
        feature_columns = tuple(columns)
        count = len(feature_columns)
        coefficients = np.asarray(model["coefficients"], dtype=float)
        intercepts = np.asarray(model["intercepts"], dtype=float)
        preprocessing = bundle["preprocessing"]
        medians, means, scales = (np.asarray(preprocessing[key], dtype=float)
                                 for key in ("medians", "means", "scales"))
        temperature = float(bundle["calibration"]["temperature"])
        available_at = _utc_clock_string(bundle["model_available_at"], name="model_available_at")
        if (coefficients.shape != (3, count) or intercepts.shape != (3,)
                or any(value.shape != (count,) for value in (medians, means, scales))
                or not all(np.isfinite(value).all() for value in (coefficients, intercepts, medians, means, scales))
                or not (scales > 0).all()
                or not math.isfinite(temperature) or not 0.25 <= temperature <= 4):
            raise ValueError("invalid frozen model/preprocessing/calibration state")
        if bundle["inference_state_sha256"] != stable_hash(_inference_state(bundle)):
            raise ValueError("frozen inference state integrity verification failed")
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError("malformed frozen inference bundle") from exc
    x = _validate_feature_rows(events, feature_columns, require_labels=False)
    if (x["decision_at"] < available_at).any():
        raise PermissionError("frozen model is unavailable before model_available_at; backdated scoring is forbidden")
    values = x.loc[:, list(feature_columns)].to_numpy(dtype=float)
    filled = np.where(np.isnan(values), medians, values)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        standardized = (filled - means) / scales
        logits = standardized @ coefficients.T + intercepts
    if not np.isfinite(standardized).all() or not np.isfinite(logits).all():
        raise ValueError("nonfinite frozen inference transformation")
    logits -= logits.max(axis=1, keepdims=True)
    exponentials = np.exp(logits)
    raw = exponentials / exponentials.sum(axis=1, keepdims=True)
    probabilities = _temperature_probabilities(raw, temperature)
    return _prediction_records(x, probabilities, standardized)


def run_synthetic_baseline(
    events: pd.DataFrame, feature_columns: tuple[str, ...],
    config: LearningConfig | None = None,
    *, split_boundaries: tuple[str, str] | None = None,
) -> dict:
    """Fit one chronological synthetic fold and return test-only predictions.

    Train/calibration purging uses actual label information times and an
    explicit embargo. Test labels affect reported test metrics only. This
    function provides no empirical fitting or final-evaluation entrypoint.
    """
    assert_research_only()
    cfg = LearningConfig() if config is None else config
    if not isinstance(cfg, LearningConfig):
        raise ValueError("config must be LearningConfig")
    cfg.__post_init__()
    x = _validate_events(events, feature_columns)
    clocks = x["decision_at"].drop_duplicates().reset_index(drop=True)
    if split_boundaries is None:
        calibration_index = int(len(clocks) * cfg.train_fraction)
        test_index = int(len(clocks) * (cfg.train_fraction + cfg.calibration_fraction))
        if not 0 < calibration_index < test_index < len(clocks):
            raise ValueError("insufficient unique decision clocks for chronological split")
        calibration_start, test_start = clocks.iloc[calibration_index], clocks.iloc[test_index]
    else:
        if not isinstance(split_boundaries, tuple) or len(split_boundaries) != 2:
            raise ValueError("split_boundaries must name calibration and test clocks")
        calibration_start, test_start = (_named_boundary(value, clocks) for value in split_boundaries)
        if not clocks.iloc[0] < calibration_start < test_start <= clocks.iloc[-1]:
            raise ValueError("split boundaries must preserve nonempty chronological train/calibration/test roles")
    embargo = pd.Timedelta(seconds=cfg.embargo_seconds)
    original = {
        "train": x.loc[x["decision_at"] < calibration_start],
        "calibration": x.loc[(x["decision_at"] >= calibration_start) & (x["decision_at"] < test_start)],
        "test": x.loc[x["decision_at"] >= test_start],
    }
    limits = {"train": calibration_start - embargo, "calibration": test_start - embargo}
    splits = {
        role: frame.loc[frame["label_available_at"] < limits[role]] if role in limits else frame
        for role, frame in original.items()
    }
    minimums = {"train": cfg.min_train_events, "calibration": cfg.min_calibration_events,
                "test": cfg.min_test_events}
    for role, frame in splits.items():
        if len(frame) < minimums[role]:
            raise ValueError(f"insufficient {role} events after information-time purge and embargo")
    if set(splits["train"]["label"]) != set(CLASS_ORDER):
        raise ValueError("unsupported training fold: all TP/SL/TIMEOUT classes are required")
    train_values = splits["train"].loc[:, list(feature_columns)].to_numpy(dtype=float)
    if np.isnan(train_values).all(axis=0).any():
        raise ValueError("all-missing training feature is unsupported")

    imputer = SimpleImputer(strategy="median")
    filled_train = imputer.fit_transform(train_values)
    scaler = StandardScaler()
    scaled_train = scaler.fit_transform(filled_train)
    labels = {name: index for index, name in enumerate(CLASS_ORDER)}
    targets = {role: frame["label"].map(labels).to_numpy(dtype=int) for role, frame in splits.items()}
    estimator = LogisticRegression(C=1.0, solver="lbfgs", max_iter=1000, random_state=cfg.random_seed)
    estimator.fit(scaled_train, targets["train"])
    transformed = {"calibration": scaler.transform(imputer.transform(
        splits["calibration"].loc[:, list(feature_columns)].to_numpy(dtype=float)))}
    probability_columns = [int(np.flatnonzero(estimator.classes_ == index)[0]) for index in range(3)]
    raw = {"calibration": estimator.predict_proba(transformed["calibration"])[:, probability_columns]}
    fit = minimize_scalar(
        lambda temperature: _metrics(_temperature_probabilities(raw["calibration"], temperature),
                                      targets["calibration"])["log_loss"],
        bounds=(0.25, 4.0), method="bounded", options={"xatol": 1e-8},
    )
    if not fit.success or not math.isfinite(float(fit.x)) or not 0.25 <= fit.x <= 4:
        raise ValueError("validation-only scalar temperature fitting failed")
    temperature = float(fit.x)
    # Freeze calibration before even transforming or scoring the test window.
    transformed["test"] = scaler.transform(imputer.transform(
        splits["test"].loc[:, list(feature_columns)].to_numpy(dtype=float)))
    raw["test"] = estimator.predict_proba(transformed["test"])[:, probability_columns]
    calibrated = {role: _temperature_probabilities(probabilities, temperature) for role, probabilities in raw.items()}
    predictions = _prediction_records(splits["test"], calibrated["test"], transformed["test"])
    split_metadata = {}
    for role, frame in splits.items():
        row_ids = frame["event_id"].to_list()
        split_metadata[role] = {
            "start": original[role]["decision_at"].min().isoformat(),
            "end": original[role]["decision_at"].max().isoformat(),
            "row_ids": row_ids, "event_count": len(frame),
            "purged_row_ids": original[role].loc[~original[role]["event_id"].isin(row_ids), "event_id"].to_list(),
        }
    bundle = {
        "classification": "SYNTHETIC_ENGINEERING_ONLY",
        "model_training_on_synthetic": True, "empirical_training": False,
        "paper_execution": False, "live_execution": False,
        "model_available_at": test_start.isoformat(),
        "model": {"name": "B1_MULTINOMIAL_LOGISTIC", "C": 1.0,
                  "algorithm": "sklearn.linear_model.LogisticRegression multinomial softmax (lbfgs)",
                  "sklearn_version": sklearn_version,
                  "random_seed": cfg.random_seed, "feature_columns": list(feature_columns),
                  "class_order": list(CLASS_ORDER),
                  "coefficients": estimator.coef_[probability_columns].tolist(),
                  "intercepts": estimator.intercept_[probability_columns].tolist()},
        "config": asdict(cfg),
        "split": {"method": "CHRONOLOGICAL_GROUPED_CLOCK_WITH_INFORMATION_PURGE",
                  "fold_count": 1, "embargo_seconds": cfg.embargo_seconds, **split_metadata},
        "preprocessing": {"fit_scope": "TRAIN_ONLY", "fit_row_ids": split_metadata["train"]["row_ids"],
                          "medians": imputer.statistics_.tolist(), "means": scaler.mean_.tolist(),
                          "scales": scaler.scale_.tolist()},
        "calibration": {"method": "scalar_temperature", "temperature": temperature,
                        "bounds": [0.25, 4.0], "fit_scope": "VALIDATION_ONLY",
                        "fit_row_ids": split_metadata["calibration"]["row_ids"]},
        "metrics": {role: {"raw": _metrics(raw[role], targets[role]),
                           "calibrated": _metrics(calibrated[role], targets[role])}
                    for role in ("calibration", "test")},
        "predictions": predictions,
    }
    bundle["inference_state_sha256"] = stable_hash(_inference_state(bundle))
    return bundle


def run_synthetic_walk_forward(
    events: pd.DataFrame, feature_columns: tuple[str, ...],
    config: LearningConfig | None = None, n_splits: int = 3,
) -> dict:
    """Expanding synthetic folds with sliding calibration and disjoint tests.

    The calendar budget is fixed before labels are inspected: 20% of unique
    decision clocks for each calibration window and 30% for all test windows.
    Integer remainder clocks enter the initial training window. Each fold is
    mandatory; insufficient post-purge support fails the entire run.
    """
    assert_research_only()
    if isinstance(n_splits, bool) or not isinstance(n_splits, int) or n_splits < 1:
        raise ValueError("n_splits must be a positive integer")
    cfg = LearningConfig() if config is None else config
    if not isinstance(cfg, LearningConfig):
        raise ValueError("config must be LearningConfig")
    cfg.__post_init__()
    x = _validate_events(events, feature_columns)
    clocks = x["decision_at"].drop_duplicates().reset_index(drop=True)
    calibration_size = int(len(clocks) * WALK_FORWARD_CALIBRATION_FRACTION)
    test_size = int(len(clocks) * WALK_FORWARD_TEST_FRACTION / n_splits)
    initial_calibration_index = len(clocks) - calibration_size - n_splits * test_size
    if calibration_size < 1 or test_size < 1 or initial_calibration_index < 1:
        raise ValueError("insufficient unique decision clocks for fixed walk-forward budgets")
    folds, predictions = [], []
    for fold_index in range(n_splits):
        calibration_index = initial_calibration_index + fold_index * test_size
        test_index = calibration_index + calibration_size
        end_index = test_index + test_size
        fold_events = x if end_index == len(clocks) else x.loc[x["decision_at"] < clocks.iloc[end_index]]
        fold = run_synthetic_baseline(
            fold_events, feature_columns, cfg,
            split_boundaries=(clocks.iloc[calibration_index].isoformat(), clocks.iloc[test_index].isoformat()),
        )
        fold["fold_index"] = fold_index
        folds.append(fold)
        predictions.extend(fold["predictions"])
    ids = [prediction["event_id"] for prediction in predictions]
    if len(set(ids)) != len(ids):
        raise RuntimeError("walk-forward test windows overlap")
    return {
        "classification": "SYNTHETIC_ENGINEERING_ONLY",
        "model_training_on_synthetic": True, "empirical_training": False,
        "paper_execution": False, "live_execution": False,
        "validation_method": "EXPANDING_TRAIN_SLIDING_CALIBRATION_DISJOINT_TEST",
        "clock_budget": {"unique_clocks": len(clocks),
                         "initial_train_clocks": initial_calibration_index,
                         "calibration_clocks_per_fold": calibration_size,
                         "test_clocks_per_fold": test_size,
                         "calibration_fraction": WALK_FORWARD_CALIBRATION_FRACTION,
                         "total_test_fraction": WALK_FORWARD_TEST_FRACTION},
        "fold_count": n_splits, "folds": folds, "predictions": predictions,
    }
