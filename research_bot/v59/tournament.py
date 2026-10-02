from __future__ import annotations

from dataclasses import asdict, dataclass
from math import log
from typing import Iterable, Sequence

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .hashing import stable_hash


CLASSES = ("TP", "SL", "TIMEOUT")


@dataclass(frozen=True)
class WalkForwardConfig:
    min_train_rows: int = 240
    validation_rows: int = 80
    test_rows: int = 80
    step_rows: int = 80
    embargo_rows: int = 1
    max_folds: int = 8

    def __post_init__(self) -> None:
        for name in (
            "min_train_rows", "validation_rows", "test_rows",
            "step_rows", "embargo_rows", "max_folds",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or value < (0 if name == "embargo_rows" else 1):
                raise ValueError(f"{name} has an invalid value")


@dataclass(frozen=True)
class TournamentConfig:
    walk_forward: WalkForwardConfig = WalkForwardConfig()
    models: tuple[str, ...] = ("PRIOR", "LOGISTIC", "HIST_GRADIENT_BOOSTING")
    cost_scenarios_bps: tuple[float, ...] = (0.0, 24.0, 36.0, 50.0)
    entropy_abstain_threshold: float = 0.98
    min_test_events_per_trial: int = 40
    seed: int = 59
    evidence_class: str = "ENGINEERING_FIXTURE"

    def __post_init__(self) -> None:
        allowed = {"PRIOR", "LOGISTIC", "HIST_GRADIENT_BOOSTING"}
        if not self.models or any(name not in allowed for name in self.models):
            raise ValueError("unsupported tournament model")
        if len(set(self.models)) != len(self.models):
            raise ValueError("model list must be unique")
        if not self.cost_scenarios_bps:
            raise ValueError("cost scenarios are required")
        for value in self.cost_scenarios_bps:
            if not np.isfinite(float(value)) or float(value) < 0:
                raise ValueError("cost scenarios must be finite and nonnegative")
        if not 0 <= float(self.entropy_abstain_threshold) <= 1:
            raise ValueError("entropy_abstain_threshold must be in [0,1]")
        if not isinstance(self.min_test_events_per_trial, int) or self.min_test_events_per_trial <= 0:
            raise ValueError("min_test_events_per_trial must be positive")
        if not isinstance(self.seed, int):
            raise ValueError("seed must be an integer")
        if not isinstance(self.evidence_class, str) or not self.evidence_class.strip():
            raise ValueError("evidence_class is required")


@dataclass(frozen=True)
class FoldSpec:
    fold_id: str
    train_start: int
    train_end: int
    validation_start: int
    validation_end: int
    test_start: int
    test_end: int


@dataclass(frozen=True)
class TrialResult:
    trial_id: str
    strategy_id: str
    model_id: str
    cost_bps: float
    fold_count: int
    test_events: int
    coverage: float
    multiclass_logloss: float
    multiclass_brier: float
    accuracy: float
    mean_realized_return: float
    compounded_return: float
    max_drawdown: float
    profit_factor: float
    admitted_events: int
    evidence_class: str
    promotable: bool
    reason: str
    prediction_hash: str

    @property
    def result_hash(self) -> str:
        return stable_hash(asdict(self))


def _validate_events(frame: pd.DataFrame, feature_columns: Sequence[str]) -> pd.DataFrame:
    required = {
        "timestamp", "strategy_id", "label", "reward_fraction",
        "loss_fraction", "timeout_loss_fraction",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"event dataset missing fields: {sorted(missing)}")
    if not feature_columns:
        raise ValueError("feature_columns are required")
    feature_missing = set(feature_columns) - set(frame.columns)
    if feature_missing:
        raise ValueError(f"event dataset missing features: {sorted(feature_missing)}")

    x = frame.copy()
    x["timestamp"] = pd.to_datetime(x["timestamp"], utc=True, errors="raise")
    if x["timestamp"].duplicated().any() and "event_id" in x.columns and x["event_id"].duplicated().any():
        raise ValueError("duplicate event identities are forbidden")
    if not x["label"].isin(CLASSES).all():
        raise ValueError("labels must be TP/SL/TIMEOUT")
    for name in ("reward_fraction", "loss_fraction", "timeout_loss_fraction"):
        x[name] = pd.to_numeric(x[name], errors="raise")
        if (~np.isfinite(x[name].to_numpy(float))).any() or (x[name] < 0).any():
            raise ValueError(f"{name} must be finite and nonnegative")
    for name in feature_columns:
        x[name] = pd.to_numeric(x[name], errors="raise")
        if not np.isfinite(x[name].to_numpy(float)).all():
            raise ValueError(f"feature {name} must be finite")
    return x.sort_values(["strategy_id", "timestamp"], kind="mergesort").reset_index(drop=True)


def make_walk_forward_folds(rows: int, config: WalkForwardConfig) -> tuple[FoldSpec, ...]:
    if rows <= 0:
        return ()
    folds: list[FoldSpec] = []
    train_end = config.min_train_rows
    while len(folds) < config.max_folds:
        validation_start = train_end + config.embargo_rows
        validation_end = validation_start + config.validation_rows
        test_start = validation_end + config.embargo_rows
        test_end = test_start + config.test_rows
        if test_end > rows:
            break
        folds.append(
            FoldSpec(
                fold_id=f"F{len(folds):02d}",
                train_start=0,
                train_end=train_end,
                validation_start=validation_start,
                validation_end=validation_end,
                test_start=test_start,
                test_end=test_end,
            )
        )
        train_end += config.step_rows
    return tuple(folds)


def _prior_probabilities(labels: pd.Series, count: int) -> np.ndarray:
    counts = labels.value_counts()
    total = max(1, len(labels))
    p = np.array([(float(counts.get(label, 0)) + 1.0) / (total + len(CLASSES)) for label in CLASSES])
    p = p / p.sum()
    return np.tile(p, (count, 1))


def _fit_model(model_id: str, X: pd.DataFrame, y: pd.Series, seed: int):
    if model_id == "LOGISTIC":
        model = Pipeline(
            [
                ("scale", StandardScaler()),
                (
                    "model",
                    LogisticRegression(
                        max_iter=1000,
                        random_state=seed,
                    ),
                ),
            ]
        )
    elif model_id == "HIST_GRADIENT_BOOSTING":
        model = HistGradientBoostingClassifier(
            max_depth=4,
            learning_rate=0.05,
            max_iter=120,
            random_state=seed,
        )
    else:
        raise ValueError(f"cannot fit model {model_id}")
    model.fit(X, y)
    return model


def _probability_axis(model, X: pd.DataFrame) -> np.ndarray:
    raw = np.asarray(model.predict_proba(X), dtype=float)
    classes = [str(value) for value in model.classes_]
    out = np.zeros((len(X), len(CLASSES)), dtype=float)
    for i, label in enumerate(CLASSES):
        if label in classes:
            out[:, i] = raw[:, classes.index(label)]
    row_sum = out.sum(axis=1)
    if np.any(row_sum <= 0):
        raise RuntimeError("model emitted zero probability mass")
    return out / row_sum[:, None]


def _calibrate_isotonic(
    validation_prob: np.ndarray,
    validation_y: pd.Series,
    test_prob: np.ndarray,
) -> np.ndarray:
    result = np.zeros_like(test_prob, dtype=float)
    y_values = validation_y.to_numpy(str)
    for index, label in enumerate(CLASSES):
        binary = (y_values == label).astype(float)
        p_val = validation_prob[:, index]
        if len(np.unique(binary)) < 2 or len(np.unique(p_val)) < 2:
            result[:, index] = test_prob[:, index]
            continue
        calibrator = IsotonicRegression(out_of_bounds="clip")
        calibrator.fit(p_val, binary)
        result[:, index] = calibrator.predict(test_prob[:, index])
    row_sum = result.sum(axis=1)
    bad = row_sum <= 0
    if np.any(bad):
        result[bad] = test_prob[bad]
        row_sum = result.sum(axis=1)
    return np.clip(result / row_sum[:, None], 1e-9, 1.0)


def _entropy(prob: np.ndarray) -> np.ndarray:
    p = np.clip(prob, 1e-12, 1.0)
    raw = -(p * np.log(p)).sum(axis=1)
    return raw / log(len(CLASSES))


def _multiclass_logloss(y: np.ndarray, p: np.ndarray) -> float:
    index = {label: i for i, label in enumerate(CLASSES)}
    losses = [-log(max(float(p[row, index[str(label)]]), 1e-12)) for row, label in enumerate(y)]
    return float(np.mean(losses))


def _multiclass_brier(y: np.ndarray, p: np.ndarray) -> float:
    target = np.zeros_like(p)
    index = {label: i for i, label in enumerate(CLASSES)}
    for row, label in enumerate(y):
        target[row, index[str(label)]] = 1.0
    return float(np.mean(np.sum((p - target) ** 2, axis=1)))


def _economic_path(
    rows: pd.DataFrame,
    probabilities: np.ndarray,
    *,
    cost_bps: float,
    entropy_threshold: float,
) -> tuple[np.ndarray, np.ndarray]:
    cost = float(cost_bps) / 10_000.0
    reward = rows["reward_fraction"].to_numpy(float)
    loss = rows["loss_fraction"].to_numpy(float)
    timeout = rows["timeout_loss_fraction"].to_numpy(float)
    expected = (
        probabilities[:, 0] * reward
        - probabilities[:, 1] * loss
        - probabilities[:, 2] * timeout
        - cost
    )
    entropy = _entropy(probabilities)
    admitted = (expected > 0.0) & (entropy <= entropy_threshold)
    realized = np.zeros(len(rows), dtype=float)
    labels = rows["label"].to_numpy(str)
    realized[(labels == "TP") & admitted] = reward[(labels == "TP") & admitted] - cost
    realized[(labels == "SL") & admitted] = -loss[(labels == "SL") & admitted] - cost
    realized[(labels == "TIMEOUT") & admitted] = -timeout[(labels == "TIMEOUT") & admitted] - cost
    return admitted, realized


def _drawdown(returns: np.ndarray) -> tuple[float, float]:
    if len(returns) == 0:
        return 0.0, 0.0
    equity = np.cumprod(1.0 + returns)
    peak = np.maximum.accumulate(np.concatenate(([1.0], equity))) [1:]
    dd = 1.0 - equity / peak
    return float(equity[-1] - 1.0), float(np.max(dd))


def _profit_factor(returns: np.ndarray) -> float:
    wins = float(returns[returns > 0].sum())
    losses = float(-returns[returns < 0].sum())
    if losses == 0:
        return 0.0 if wins == 0 else wins / 1e-12
    return wins / losses


def run_tournament(
    events: pd.DataFrame,
    *,
    feature_columns: Sequence[str],
    config: TournamentConfig | None = None,
) -> dict:
    cfg = TournamentConfig() if config is None else config
    frame = _validate_events(events, feature_columns)
    strategy_ids = tuple(sorted(str(value) for value in frame["strategy_id"].unique()))
    if not strategy_ids:
        raise ValueError("no strategy ids found")

    attempted: list[dict] = []
    trial_results: list[TrialResult] = []
    prediction_evidence: list[dict] = []

    for strategy_id in strategy_ids:
        strategy = frame.loc[frame["strategy_id"] == strategy_id].reset_index(drop=True)
        folds = make_walk_forward_folds(len(strategy), cfg.walk_forward)
        for model_id in cfg.models:
            fold_predictions: list[pd.DataFrame] = []
            for fold in folds:
                train = strategy.iloc[fold.train_start:fold.train_end]
                validation = strategy.iloc[fold.validation_start:fold.validation_end]
                test = strategy.iloc[fold.test_start:fold.test_end]
                X_train = train.loc[:, feature_columns]
                X_validation = validation.loc[:, feature_columns]
                X_test = test.loc[:, feature_columns]
                y_train = train["label"]
                y_validation = validation["label"]

                attempted.append(
                    {
                        "strategy_id": strategy_id,
                        "model_id": model_id,
                        "fold_id": fold.fold_id,
                        "train_rows": len(train),
                        "validation_rows": len(validation),
                        "test_rows": len(test),
                        "train_end_timestamp": train["timestamp"].iloc[-1].isoformat(),
                        "validation_start_timestamp": validation["timestamp"].iloc[0].isoformat(),
                        "test_start_timestamp": test["timestamp"].iloc[0].isoformat(),
                    }
                )
                if model_id == "PRIOR":
                    validation_prob = _prior_probabilities(y_train, len(validation))
                    test_prob = _prior_probabilities(y_train, len(test))
                else:
                    model = _fit_model(model_id, X_train, y_train, cfg.seed)
                    validation_prob = _probability_axis(model, X_validation)
                    test_prob = _probability_axis(model, X_test)
                    test_prob = _calibrate_isotonic(validation_prob, y_validation, test_prob)

                part = test[
                    ["timestamp", "strategy_id", "label", "reward_fraction", "loss_fraction", "timeout_loss_fraction"]
                ].copy()
                part["fold_id"] = fold.fold_id
                for index, label in enumerate(CLASSES):
                    part[f"p_{label}"] = test_prob[:, index]
                fold_predictions.append(part)

            if not fold_predictions:
                continue
            predictions = pd.concat(fold_predictions, ignore_index=True)
            p = predictions[[f"p_{label}" for label in CLASSES]].to_numpy(float)
            y = predictions["label"].to_numpy(str)
            prediction_hash = stable_hash(
                [
                    {
                        "timestamp": pd.Timestamp(row["timestamp"]).isoformat(),
                        "strategy_id": row["strategy_id"],
                        "label": row["label"],
                        "fold_id": row["fold_id"],
                        **{f"p_{label}": float(row[f"p_{label}"]) for label in CLASSES},
                    }
                    for row in predictions.to_dict(orient="records")
                ]
            )
            prediction_evidence.append(
                {
                    "strategy_id": strategy_id,
                    "model_id": model_id,
                    "prediction_hash": prediction_hash,
                    "test_events": len(predictions),
                }
            )
            for cost_bps in cfg.cost_scenarios_bps:
                admitted, realized = _economic_path(
                    predictions,
                    p,
                    cost_bps=float(cost_bps),
                    entropy_threshold=float(cfg.entropy_abstain_threshold),
                )
                compounded, max_dd = _drawdown(realized)
                trial_id = stable_hash(
                    {
                        "strategy_id": strategy_id,
                        "model_id": model_id,
                        "cost_bps": float(cost_bps),
                        "prediction_hash": prediction_hash,
                        "config": asdict(cfg),
                    }
                )
                enough = len(predictions) >= cfg.min_test_events_per_trial
                promotable = bool(enough and cfg.evidence_class != "ENGINEERING_FIXTURE")
                reason = (
                    "ENGINEERING_FIXTURE_NOT_PROMOTABLE"
                    if cfg.evidence_class == "ENGINEERING_FIXTURE"
                    else "INSUFFICIENT_TEST_EVENTS"
                    if not enough
                    else "ELIGIBLE_FOR_SEPARATE_PROMOTION_REVIEW"
                )
                result = TrialResult(
                    trial_id=trial_id,
                    strategy_id=strategy_id,
                    model_id=model_id,
                    cost_bps=float(cost_bps),
                    fold_count=len(folds),
                    test_events=len(predictions),
                    coverage=float(admitted.mean()) if len(admitted) else 0.0,
                    multiclass_logloss=_multiclass_logloss(y, p),
                    multiclass_brier=_multiclass_brier(y, p),
                    accuracy=float(
                        np.mean(
                            np.array(CLASSES, dtype=object)[np.argmax(p, axis=1)] == y
                        )
                    ),
                    mean_realized_return=float(np.mean(realized)) if len(realized) else 0.0,
                    compounded_return=compounded,
                    max_drawdown=max_dd,
                    profit_factor=_profit_factor(realized),
                    admitted_events=int(admitted.sum()),
                    evidence_class=cfg.evidence_class,
                    promotable=promotable,
                    reason=reason,
                    prediction_hash=prediction_hash,
                )
                trial_results.append(result)

    trial_rows = [asdict(row) | {"result_hash": row.result_hash} for row in trial_results]
    attempted_hash = stable_hash(attempted)
    results_hash = stable_hash(trial_rows)
    registry = {
        "classification": "V59_MODEL_STRATEGY_TOURNAMENT",
        "classes": list(CLASSES),
        "feature_columns": list(feature_columns),
        "strategy_ids": list(strategy_ids),
        "config": asdict(cfg),
        "attempted_folds": attempted,
        "attempted_folds_hash": attempted_hash,
        "trial_results": trial_rows,
        "trial_results_hash": results_hash,
        "prediction_evidence": prediction_evidence,
        "paper_execution": False,
        "live_execution": False,
    }
    registry["registry_hash"] = stable_hash(registry)
    return registry
