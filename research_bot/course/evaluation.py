"""Preregistration-oriented evaluation, never execution or automatic promotion.

ENGINEERING_EXTENSION: course examples do not establish these statistical tools.
Events are outcomes on unit entry notional. Overlapping event returns cannot be
compounded as a portfolio; this module refuses that economic interpretation.
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from hashlib import sha256
from itertools import combinations, pairwise
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np
import pandas as pd
import sklearn
from numpy.typing import ArrayLike
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ..regime_robustness import (
    benjamini_hochberg,
    deflated_sharpe_probability,
    moving_block_bootstrap,
    probability_backtest_overfitting,
)
from ..trial_registry_v17 import TrialRecord


@dataclass(frozen=True)
class EvaluationConfig:
    version: str = "COURSE_EVALUATION_V1"
    cost_bps: tuple[float, ...] = (0.0, 24.0, 36.0, 50.0)
    reference_cost_bps: float = 24.0
    min_train: int = 60
    test_size: int = 20
    embargo_seconds: int = 0
    cpcv_groups: int = 4
    cpcv_test_groups: int = 2
    bootstrap_block: int = 8
    bootstrap_runs: int = 400
    random_seed: int = 42
    cvar_alpha: float = 0.05
    model_names: tuple[str, ...] = ("logistic", "hgb")
    probability_threshold: float = 0.60
    baseline_admissible: bool = False
    regular_period_seconds: int | None = None
    periods_per_year: float | None = None
    frozen_holdout_start: str | None = None
    independent_trials: int | None = None
    trial_sharpe_std: float | None = None

    def __post_init__(self) -> None:
        values = (*self.cost_bps, self.reference_cost_bps, self.cvar_alpha,
                  self.probability_threshold)
        if not all(np.isfinite(values)) or any(x < 0 for x in self.cost_bps):
            raise ValueError("INVALID_EVALUATION_PARAMETER")
        if self.reference_cost_bps < 0 or not 0 < self.cvar_alpha < 1:
            raise ValueError("INVALID_COST_OR_CVAR")
        if not 0 < self.probability_threshold < 1:
            raise ValueError("INVALID_PROBABILITY_THRESHOLD")
        if self.min_train < 2 or self.test_size < 1 or self.embargo_seconds < 0:
            raise ValueError("INVALID_TEMPORAL_SPLIT")
        if not 1 <= self.cpcv_test_groups < self.cpcv_groups:
            raise ValueError("INVALID_CPCV_GROUPS")
        if self.bootstrap_block < 1 or self.bootstrap_runs < 20:
            raise ValueError("INVALID_BOOTSTRAP")
        if set(self.model_names) - {"logistic", "hgb"}:
            raise ValueError("UNAPPROVED_MODEL_DEPENDENCY")
        if self.periods_per_year is not None and (
            not np.isfinite(self.periods_per_year) or self.periods_per_year <= 0
        ):
            raise ValueError("INVALID_ANNUALIZATION")
        if self.regular_period_seconds is not None and self.regular_period_seconds <= 0:
            raise ValueError("INVALID_PERIOD_DURATION")


TIME_COLUMNS = ("decision_time", "available_at", "entry_time", "exit_time", "label_available_at")


def _aware_timestamp(value: Any) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if pd.isna(result) or result.tzinfo is None:
        raise ValueError("INVALID_OR_NAIVE_TIMESTAMP")
    return result.tz_convert("UTC")


def validate_events(events: pd.DataFrame, config: EvaluationConfig) -> pd.DataFrame:
    """Reject rather than silently sort/filter an inadmissible event corpus."""
    required = {*TIME_COLUMNS, "event_id", "gross_return"}
    if not required.issubset(events.columns) or events.empty:
        raise ValueError("INCOMPLETE_EVENT_CORPUS")
    x = events.copy().reset_index(drop=True)
    for column in TIME_COLUMNS:
        x[column] = pd.to_datetime([_aware_timestamp(v) for v in x[column]], utc=True)
    if x.event_id.isna().any() or x.event_id.astype(str).str.strip().eq("").any():
        raise ValueError("INVALID_EVENT_ID")
    if x.event_id.duplicated().any():
        raise ValueError("DUPLICATE_EVENT_ID")
    if not x.decision_time.is_monotonic_increasing:
        raise ValueError("NONCHRONOLOGICAL_EVENT_CORPUS")
    if not ((x.available_at <= x.decision_time) & (x.entry_time > x.decision_time)
            & (x.exit_time > x.entry_time) & (x.label_available_at >= x.exit_time)).all():
        raise ValueError("POINT_IN_TIME_OR_EXECUTION_TIMING_VIOLATION")
    x["gross_return"] = pd.to_numeric(x.gross_return, errors="raise")
    if not np.isfinite(x.gross_return.to_numpy(float)).all() or (x.gross_return <= -1).any():
        raise ValueError("INVALID_UNDERLYING_RETURN")
    if config.frozen_holdout_start is not None:
        cutoff = _aware_timestamp(config.frozen_holdout_start)
        if (x.label_available_at >= cutoff).any():
            raise ValueError("FROZEN_HOLDOUT_MUST_NOT_BE_CONSUMED")
    return x


def purge_train_indices(events: pd.DataFrame, train: Sequence[int] | np.ndarray, test: Sequence[int] | np.ndarray,
                        embargo_seconds: int = 0) -> np.ndarray:
    """Closed label intervals [decision, label_available] plus post-test embargo.

    Purging extends to decision time rather than entry time, conservatively
    excluding shared decision/label windows. Endpoints touching are purged.
    """
    keep = np.asarray(train, dtype=int)
    delta = pd.Timedelta(seconds=embargo_seconds)
    for row in events.iloc[list(test)].itertuples():
        candidate = events.iloc[keep]
        overlaps = ((candidate.decision_time <= row.label_available_at + delta)
                    & (candidate.label_available_at >= row.decision_time))
        keep = keep[~overlaps.to_numpy()]
    return keep


def walk_forward_splits(events: pd.DataFrame, config: EvaluationConfig
                        ) -> list[tuple[np.ndarray, np.ndarray]]:
    result = []
    for start in range(config.min_train, len(events), config.test_size):
        test = np.arange(start, min(start + config.test_size, len(events)))
        first_decision = events.iloc[start].decision_time
        train = np.flatnonzero((events.label_available_at < first_decision).to_numpy())
        train = train[train < start]
        train = purge_train_indices(events, train, test, config.embargo_seconds)
        if len(train) >= config.min_train:
            result.append((train, test))
    return result


def combinatorial_splits(events: pd.DataFrame, config: EvaluationConfig) -> list[dict[str, Any]]:
    """All C(N,k) splits with explicit group-occurrence assignment to φ paths.

    Every group appears C(N-1,k-1) times. Assign its successive occurrences to
    paths 0..φ-1; each resulting path contains every chronological group once.
    This is combinatorial validation, not a deployable chronological training
    process: some training groups lie after the tested group.
    """
    if len(events) < config.cpcv_groups:
        return []
    groups = np.array_split(np.arange(len(events)), config.cpcv_groups)
    occurrence = [0] * config.cpcv_groups
    result = []
    for split_id, selected in enumerate(combinations(range(config.cpcv_groups),
                                                      config.cpcv_test_groups)):
        test = np.concatenate([groups[g] for g in selected])
        train = np.setdiff1d(np.arange(len(events)), test)
        train = purge_train_indices(events, train, test, config.embargo_seconds)
        assignments = []
        for group in selected:
            assignments.append({"group": group, "path": occurrence[group],
                                "indices": groups[group].tolist()})
            occurrence[group] += 1
        result.append({"split_id": split_id, "train": train, "test": test,
                       "test_groups": selected, "assignments": assignments})
    return result


def average_uniqueness(events: pd.DataFrame) -> dict[str, Any]:
    """Exact time-weighted uniqueness over half-open execution intervals [t0,t1).

    This is duration-weighted, not sample-bar weighted. Adjacent intervals do
    not overlap. Interval identity remains distinct for simultaneous events.
    """
    start = events.entry_time.astype("int64").to_numpy()
    end = events.exit_time.astype("int64").to_numpy()
    points = np.unique(np.concatenate([start, end]))
    accumulated = np.zeros(len(events))
    max_concurrency = 0
    for left, right in pairwise(points):
        active = (start <= left) & (end > left)
        concurrency = int(active.sum())
        max_concurrency = max(max_concurrency, concurrency)
        if concurrency:
            accumulated[active] += (int(right) - int(left)) / concurrency
    values = accumulated / (end - start)
    return {"method": "TIME_WEIGHTED_HALF_OPEN_INTERVAL", "mean": float(values.mean()),
            "minimum": float(values.min()), "maximum_concurrency": max_concurrency,
            "by_event": dict(zip(events.event_id.astype(str), values.tolist()))}


def net_returns(gross: ArrayLike, positions: ArrayLike, cost_bps: float) -> np.ndarray:
    """One fully closed unit-notional event; cost is total roundtrip, charged once.

    Position is direction, not a continuous leveraged portfolio allocation.
    Gross underlying simple return times direction assumes linear PnL on entry
    notional. No funding/borrow/margin cost is silently claimed to be included.
    """
    r, p = np.asarray(gross, float), np.asarray(positions, float)
    if r.ndim != 1 or r.shape != p.shape or not np.isfinite(r).all() or not np.isin(p, [-1, 0, 1]).all():
        raise ValueError("INVALID_RETURN_OR_POSITION_ARRAY")
    if not np.isfinite(cost_bps) or cost_bps < 0:
        raise ValueError("INVALID_COST")
    return p * r - np.abs(p) * cost_bps / 10000.0


def _regular_nonoverlap(events: pd.DataFrame, config: EvaluationConfig) -> bool:
    seconds = config.regular_period_seconds
    if seconds is None or len(events) < 2:
        return False
    duration = (events.exit_time - events.entry_time).dt.total_seconds().to_numpy()
    spacing = events.entry_time.diff().dt.total_seconds().to_numpy()[1:]
    return bool(np.all(duration == seconds) and np.all(spacing == seconds))


def economic_metrics(returns: ArrayLike, positions: ArrayLike,
                     *, portfolio_path_valid: bool = False,
                     periods_per_year: float | None = None, cvar_alpha: float = .05
                     ) -> dict[str, Any]:
    r, p = np.asarray(returns, float), np.asarray(positions, float)
    if r.ndim != 1 or not len(r) or r.shape != p.shape or not np.isfinite(r).all() or not np.isin(p, [-1, 0, 1]).all():
        raise ValueError("INVALID_METRIC_INPUT")
    if not 0 < cvar_alpha < 1:
        raise ValueError("INVALID_CVAR_ALPHA")
    std = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    downside = float(np.sqrt(np.mean(np.minimum(r, 0) ** 2)))
    tail_count = max(1, math.ceil(len(r) * cvar_alpha))
    trades = r[p != 0]
    result: dict[str, Any] = {
        "observations": len(r), "trade_count": int(np.count_nonzero(p)),
        "mean_net_event_return": float(r.mean()),
        "sharpe_per_observation": float(r.mean() / std) if std > 0 else None,
        "sortino_per_observation": float(r.mean() / downside) if downside > 0 else None,
        "expected_shortfall_loss": float(-np.sort(r)[:tail_count].mean()),
        "cvar_alpha": cvar_alpha, "empirical_tail_observations": tail_count,
        "cvar_applicability": "LOW_TAIL_SUPPORT" if tail_count < 10 else "EMPIRICAL_ONLY",
        "expectancy_per_trade": float(trades.mean()) if len(trades) else None,
        "profit_factor": float(trades[trades > 0].sum() / -trades[trades < 0].sum())
        if (trades < 0).any() else None,
        "roundtrip_notional_turnover": float(2 * np.abs(p).sum()),
        "active_event_fraction": float(np.mean(p != 0)),
        "return_per_one_way_turnover": float(r.sum() / (2 * np.abs(p).sum()))
        if np.abs(p).sum() else None,
        "capacity_estimate": None, "capacity_reason": "NO_EXECUTABLE_DEPTH_AND_IMPACT_DATA",
        "total_return": None, "max_drawdown": None, "cagr": None,
        "annualized_sharpe": None, "calmar": None,
        "portfolio_path_status": "VALID_UNIT_CAPITAL_NONOVERLAP" if portfolio_path_valid
        else "NOT_APPLICABLE_OVERLAP_OR_IRREGULAR_EVENT_PANEL",
    }
    if portfolio_path_valid and np.all(r > -1):
        equity = np.concatenate(([1.0], np.cumprod(1 + r)))
        dd = equity / np.maximum.accumulate(equity) - 1
        result["total_return"] = float(equity[-1] - 1)
        result["max_drawdown"] = float(dd.min())
        if periods_per_year is not None:
            result["cagr"] = float(equity[-1] ** (periods_per_year / len(r)) - 1)
            result["annualized_sharpe"] = float(r.mean() / std * np.sqrt(periods_per_year)) if std > 0 else None
            result["calmar"] = float(result["cagr"] / -dd.min()) if dd.min() < 0 else None
    return result


def paired_comparison(candidate: ArrayLike, baseline: ArrayLike,
                      config: EvaluationConfig) -> dict[str, Any]:
    """Reuse canonical MBB percentile CI, add a centered-null one-sided test.

    The existing raw-bootstrap mass below zero is not treated as a calibrated
    null p-value. This extension centers differences for H0: mean <= 0.
    Weak stationarity and adequate block length remain research assumptions.
    """
    c, b = np.asarray(candidate, float), np.asarray(baseline, float)
    if c.ndim != 1 or c.shape != b.shape or not np.isfinite(c).all() or not np.isfinite(b).all():
        raise ValueError("INVALID_PAIRED_PANEL")
    diff = c - b
    if len(diff) < max(40, 2 * config.bootstrap_block):
        return {"status": "NOT_APPLICABLE", "reason": "INSUFFICIENT_BLOCK_SUPPORT"}
    ci = moving_block_bootstrap(diff, config.bootstrap_block, config.bootstrap_runs,
                               config.random_seed)
    centered = diff - diff.mean()
    rng = np.random.default_rng(config.random_seed)
    block, n = config.bootstrap_block, len(diff)
    exceed = 0
    for _ in range(config.bootstrap_runs):
        starts = rng.choice(np.arange(n - block + 1), size=math.ceil(n / block), replace=True)
        sample = np.concatenate([centered[s:s + block] for s in starts])[:n]
        exceed += int(sample.mean() >= diff.mean())
    return {"status": "ASSUMPTION_CONDITIONAL", "method": "PAIRED_MOVING_BLOCK_CENTERED_NULL",
            "n": n, "block": block, "runs": config.bootstrap_runs,
            "delta_mean": float(diff.mean()), "ci": [ci["ci_low"], ci["ci_high"]],
            "p_one_sided": (1 + exceed) / (1 + config.bootstrap_runs),
            "assumptions": ["WEAK_STATIONARITY", "BLOCK_LENGTH_COVERS_DEPENDENCE", "PREREGISTERED_COMPARISONS"]}


def _pipeline(name: str, seed: int) -> Pipeline:
    estimator = (LogisticRegression(C=1, random_state=seed, max_iter=500)
                 if name == "logistic" else HistGradientBoostingClassifier(
                     learning_rate=.05, max_iter=100, max_leaf_nodes=15,
                     l2_regularization=1, random_state=seed))
    return Pipeline([("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                     ("scaler", StandardScaler()), ("model", estimator)])


def walk_forward_predictions(events: pd.DataFrame, feature_columns: Sequence[str],
                             config: EvaluationConfig) -> dict[str, Any]:
    """Fixed-threshold model experiment; all preprocessing is fitted on training.

    Never tunes on test, never uses frozen holdout, and never changes predictor
    or thresholds in the existing v0.51 experiment.
    """
    if not config.baseline_admissible:
        return {"status": "BLOCKED", "reason": "BASELINES_NOT_YET_SCIENTIFICALLY_ADMISSIBLE"}
    if not feature_columns or not set(feature_columns).issubset(events.columns):
        raise ValueError("MISSING_FEATURE_COLUMNS")
    forbidden = {*TIME_COLUMNS, "gross_return", "target", "event_id"}
    if set(feature_columns) & forbidden:
        raise ValueError("OUTCOME_OR_TIMING_AS_FEATURE_FORBIDDEN")
    features = events[list(feature_columns)].astype(float)
    if np.isinf(features.to_numpy()).any():
        raise ValueError("INFINITE_FEATURE")
    target = (events.gross_return > config.reference_cost_bps / 10000).astype(int)
    result: dict[str, Any] = {"status": "EXPERIMENT_ONLY", "target": "LONG_NEXT_EVENT_NET_POSITIVE", "folds": []}
    for fold_id, (train, test) in enumerate(walk_forward_splits(events, config)):
        if target.iloc[train].nunique() != 2:
            result["folds"].append({"fold": fold_id, "status": "SINGLE_CLASS_TRAIN"})
            continue
        for name in config.model_names:
            model = _pipeline(name, config.random_seed)
            model.fit(features.iloc[train], target.iloc[train])
            probability = model.predict_proba(features.iloc[test])[:, 1]
            calibration_bins = []
            actual = target.iloc[test].to_numpy()
            for lower, upper in pairwise(np.linspace(0, 1, 11)):
                mask = (probability >= lower) & ((probability < upper) if upper < 1 else probability <= upper)
                if mask.any():
                    calibration_bins.append({"lower": float(lower), "upper": float(upper),
                                             "count": int(mask.sum()), "predicted": float(probability[mask].mean()),
                                             "observed": float(actual[mask].mean())})
            train_hash = sha256(pd.concat([features.iloc[train], target.iloc[train]], axis=1).to_csv(index=False).encode()).hexdigest()
            model_version = sha256(json.dumps({"model": name, "config": asdict(config),
                                                "train_hash": train_hash, "features": list(feature_columns),
                                                "code_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
                                                "sklearn": sklearn.__version__, "numpy": np.__version__,
                                                "pandas": pd.__version__}, sort_keys=True).encode()).hexdigest()
            result["folds"].append({"fold": fold_id, "model": name, "train_indices": train.tolist(),
                                    "test_indices": test.tolist(), "probability": probability.tolist(),
                                    "positions": (probability >= config.probability_threshold).astype(int).tolist(),
                                    "brier": float(np.mean((probability - target.iloc[test].to_numpy()) ** 2)),
                                    "prior_brier": float(np.mean((float(target.iloc[train].mean()) - actual) ** 2)),
                                    "calibration_bins": calibration_bins, "model_version": model_version,
                                    "train_imputer_medians": model.named_steps["imputer"].statistics_.tolist(),
                                    "train_scaler_means": model.named_steps["scaler"].mean_.tolist()})
    return result


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_clean(v) for v in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    return value


def evaluate_research(events: pd.DataFrame, *, feature_columns: Sequence[str],
                      strategy_positions: Mapping[str, ArrayLike], git_commit: str,
                      config: EvaluationConfig | None = None,
                      provenance: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Machine-readable analysis of an explicitly supplied, versioned event panel.

    Supplied baseline positions must originate from PIT hypothesis generation;
    arrays alone cannot establish that provenance. Unknown evidence stays
    deferred. No network, model persistence, registry mutation, or order port.
    """
    cfg = config or EvaluationConfig()
    x = validate_events(events, cfg)
    prov = dict(provenance or {})
    if not git_commit or not strategy_positions:
        raise ValueError("MISSING_PROVENANCE_OR_BASELINES")
    if cfg.baseline_admissible and not all(prov.get(k) for k in (
            "baseline_evidence_ref", "preregistration_ref")):
        raise ValueError("ADMITTED_BASELINES_REQUIRE_EVIDENCE_AND_PREREGISTRATION")
    positions = {str(k): np.asarray(v, float) for k, v in strategy_positions.items()}
    reference = {k: net_returns(x.gross_return, p, cfg.reference_cost_bps)
                 for k, p in positions.items()}
    regular = _regular_nonoverlap(x, cfg)
    dataset_hash = sha256(x.to_csv(index=False).encode()).hexdigest()
    config_hash = sha256(json.dumps(asdict(cfg), sort_keys=True).encode()).hexdigest()
    candidate_set_hash = sha256(json.dumps({k: v.tolist() for k, v in positions.items()}, sort_keys=True,
                                           allow_nan=False).encode()).hexdigest()
    report: dict[str, Any] = {
        "status": "SCIENTIFIC_DECISION_DEFERRED", "execution_authorization": "DENIED_RESEARCH_ONLY",
        "evidence_class": prov.get("evidence_class", "UNVERIFIED_INPUT_CORPUS"),
        "provenance": {**prov, "git_commit": git_commit, "dataset_hash": dataset_hash,
                       "config_hash": config_hash, "code_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
                       "candidate_set_hash": candidate_set_hash, "feature_columns": list(feature_columns),
                       "dependencies": {"numpy": np.__version__, "pandas": pd.__version__, "sklearn": sklearn.__version__}},
        "config": asdict(cfg), "average_uniqueness": average_uniqueness(x),
        "cost_model": {"unit": "BASIS_POINTS_ON_ENTRY_NOTIONAL", "scope": "TOTAL_ROUNDTRIP_ONCE_PER_COMPLETED_EVENT",
                       "missing_components": ["FUNDING", "BORROW", "DEPTH", "LATENCY", "IMPACT"]},
        "cost_stress": {}, "paired_comparisons": {}, "walk_forward": {}, "cpcv": {},
        "ablation_registry": {name: "REQUIRES_GENERATED_PIT_POSITIONS_AND_PROVENANCE" for name in (
            "classical_baseline", "individual_course_strategies", "grid_only", "arbitrage_only",
            "order_book_imbalance", "technical_confluence", "technical_regime", "technical_ichimoku",
            "technical_ict_smc", "technical_brooks", "technical_microstructure", "all_approved_features")},
        "remaining_gates": ["PREREGISTRATION_AND_REAL_DATA_PROVENANCE", "UNTOUCHED_EXTERNAL_HOLDOUT",
                            "PROSPECTIVE_FIRST_SEEN_COLLECTION", "COST_AND_EXECUTION_CALIBRATION",
                            "INDEPENDENT_REVIEW_NO_AUTOMATIC_PROMOTION"],
    }
    for cost in cfg.cost_bps:
        report["cost_stress"][str(cost)] = {
            k: economic_metrics(net_returns(x.gross_return, p, cost), p,
                                portfolio_path_valid=regular, periods_per_year=cfg.periods_per_year,
                                cvar_alpha=cfg.cvar_alpha) for k, p in positions.items()}
    baseline = next(iter(reference))
    report["comparison_baseline"] = baseline
    for name, r in reference.items():
        if name != baseline:
            report["paired_comparisons"][name] = paired_comparison(r, reference[baseline], cfg)
    qvalues = benjamini_hochberg({k: v["p_one_sided"] for k, v in report["paired_comparisons"].items()
                                 if "p_one_sided" in v})
    for name, qvalue in qvalues.items():
        report["paired_comparisons"][name]["bh_qvalue"] = qvalue
        report["paired_comparisons"][name]["fdr_assumption"] = "INDEPENDENCE_OR_POSITIVE_DEPENDENCE_REQUIRES_REVIEW"
    report["walk_forward"] = walk_forward_predictions(x, feature_columns, cfg)
    splits = combinatorial_splits(x, cfg)
    n_paths = math.comb(cfg.cpcv_groups - 1, cfg.cpcv_test_groups - 1)
    report["cpcv"] = {"method": "COMBINATORIAL_PURGE_AUDIT_WITH_FIXED_BASELINE_PATHS",
                      "status": "BASELINE_PATHS_ONLY", "n_splits": len(splits), "n_paths": n_paths,
                      "learned_paths_status": "BLOCKED_NOT_IMPLEMENTED_NO_PROMOTION",
                      "splits": [{"split_id": s["split_id"], "train_indices": s["train"].tolist(),
                                  "test_indices": s["test"].tolist(), "assignments": s["assignments"]} for s in splits],
                      "paths": []}
    for path in range(n_paths):
        indices = sorted(i for s in splits for assignment in s["assignments"]
                         if assignment["path"] == path for i in assignment["indices"])
        report["cpcv"]["paths"].append({"path": path, "indices": indices,
                                       "returns": {k: r[indices].tolist() for k, r in reference.items()}})
    report["cpcv"]["limitation"] = "FROZEN_BASELINES_HAVE_IDENTICAL_PATHS; THIS_IS_NOT_MODEL_CPCV_EVIDENCE"
    report["pbo"] = {"status": "NOT_APPLICABLE", "reason": "NEEDS_COMPLETE_REGISTERED_VARIANT_UNIVERSE_AND_REGULAR_PANEL"}
    if regular and prov.get("complete_trial_universe") is True and len(reference) >= 2 and len(x) >= 120:
        matrix = pd.DataFrame(reference)
        groups = np.array_split(np.arange(len(x)), 6)
        all_variances_valid = all((matrix.iloc[np.concatenate([groups[g] for g in selected])].std(ddof=1) > 0).all()
                                  for selected in combinations(range(6), 3))
        if all_variances_valid:
            report["pbo"] = {"status": "ASSUMPTION_CONDITIONAL", "method": "CSCV_PBO_NOT_CPCV",
                             **probability_backtest_overfitting(matrix, n_groups=6),
                             "assumptions": ["COMPLETE_SEARCH_UNIVERSE", "COMPARABLE_TRIALS", "STABLE_RANKING_DISTRIBUTION"]}
    report["sharpe_probabilities"] = {}
    for name, r in reference.items():
        item: dict[str, Any] = {"psr": None, "dsr": None, "status": "NOT_APPLICABLE",
                               "reason": "REGULAR_NONOVERLAP_AND_30_OBSERVATIONS_REQUIRED"}
        if regular and len(r) >= 30 and r.std(ddof=1) > 0:
            sr = r.mean() / r.std(ddof=1)
            skew, kurt = pd.Series(r).skew(), pd.Series(r).kurt() + 3
            variance = 1 - skew * sr + (kurt - 1) * sr ** 2 / 4
            if variance > 0 and np.isfinite(variance):
                item = {"psr": float(NormalDist().cdf(sr * np.sqrt(len(r) - 1) / np.sqrt(variance))),
                        "dsr": None, "status": "ASSUMPTION_CONDITIONAL", "benchmark_per_period": 0,
                        "assumptions": ["IID_OR_ADEQUATE_EFFECTIVE_SAMPLE_JUSTIFICATION", "FINITE_FOURTH_MOMENT"],
                        "dsr_reason": "INDEPENDENT_TRIAL_COUNT_AND_SHARPE_DISPERSION_NOT_ESTABLISHED"}
                if cfg.independent_trials is not None and cfg.independent_trials > 1 and (
                        cfg.trial_sharpe_std is not None and cfg.trial_sharpe_std > 0
                        and np.isfinite(cfg.trial_sharpe_std)):
                    item["dsr"] = deflated_sharpe_probability(r, cfg.independent_trials, cfg.trial_sharpe_std)
                    item["dsr_reason"] = "ASSUMPTION_CONDITIONAL_INDEPENDENT_TRIAL_UNIVERSE"
        report["sharpe_probabilities"][name] = item
    # Integrate with the canonical TrialRecord contract without appending to its
    # registry or modifying any existing scientific evidence.
    report["trial_records"] = [TrialRecord(
        trial_id=f"{cfg.version}:{name}:{dataset_hash[:12]}", git_commit=git_commit,
        dataset_fingerprint=dataset_hash, feature_family="COURSE_RULE_BASELINE",
        model_family=name, hyperparameters={"config_hash": config_hash}, random_seed=cfg.random_seed,
        forecast_horizon="EXPLICIT_EVENT_ENTRY_EXIT_INTERVAL", decision_rule="FROZEN_SUPPLIED_PIT_POSITIONS",
        cost_model=report["cost_model"], train_window="NOT_APPLICABLE_RULE_BASELINE",
        validation_window=f"{x.decision_time.iloc[0].isoformat()}/{x.label_available_at.iloc[-1].isoformat()}",
        holdout_window=None, return_path_artifact=f"REPORT:cost_stress:{name}", status="HYPOTHESIS"
    ).canonical_payload() for name in positions]
    cleaned = _clean(report)
    json.dumps(cleaned, allow_nan=False)
    return cleaned
