"""Frozen no-retuning real-market development audit for V58.

Only byte-verified development archives registered by V58 are accepted. These
archives are never upgraded to final holdout evidence and never authorize PAPER
or LIVE execution.
"""
from __future__ import annotations

from dataclasses import dataclass
import gzip
from hashlib import sha256
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from sklearn import __version__ as sklearn_version
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from .contracts import assert_research_only
from .data_audit import audit_archive, canonical_json, inspect_csv
from .development_sources import SOURCES
from .events import stable_hash
from .generators import generate_candidates
from .immutable_io import write_immutable_bundle
from .pipeline import run_verified_development_pipeline

CLASS_ORDER = ("TP", "SL", "TIMEOUT")
FEATURE_MAP = (
    ("returns_1", "feature_returns_1"),
    ("ATR_percent", "feature_ATR_percent"),
    ("realized_volatility", "feature_past_volatility"),
    ("trend_strength", "feature_trend_strength"),
    ("tenkan_kijun_distance_atr", "feature_tenkan_kijun_distance_atr"),
    ("displacement_body_ratio", "feature_displacement_body_ratio"),
    ("relative_volume", "feature_relative_volume"),
    ("trading_range_position", "feature_trading_range_position"),
)
FEATURE_COLUMNS = tuple(target for _, target in FEATURE_MAP)
ALLOWED_VENUES = {"binance", "coinex"}


@dataclass(frozen=True)
class Protocol:
    n_splits: int = 3
    calibration_fraction: float = 0.20
    total_test_fraction: float = 0.30
    embargo_seconds: int = 14_400
    min_train: int = 30
    min_calibration: int = 10
    min_test: int = 10
    logistic_c: float = 1.0
    seed: int = 58
    temperature_bounds: tuple[float, float] = (0.25, 4.0)
    round_trip_cost_bps: float = 24.0
    timeout_loss_multiple: float = 0.25
    uncertainty_loss_multiple: float = 0.05
    min_regime_confidence: float = 0.20
    max_entropy: float = 0.98
    max_shift: float = 8.0


PROTOCOL = Protocol()


def _utc(values: pd.Series, name: str) -> pd.Series:
    stamps = pd.to_datetime(values, utc=True, errors="raise")
    for original in values:
        stamp = pd.Timestamp(original)
        if pd.isna(stamp) or stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
            raise ValueError(f"{name} must contain timezone-aware UTC timestamps")
    return stamps


def _validate(events: pd.DataFrame) -> pd.DataFrame:
    required = {
        "event_id", "venue", "symbol", "decision_at", "label_available_at", "label",
        "regime_confidence", "entry_price", "stop_price", "target_price", "gross_return",
        "data_version", "policy_hash", *FEATURE_COLUMNS,
    }
    if not isinstance(events, pd.DataFrame) or events.empty:
        raise ValueError("market audit requires nonempty events")
    if events.columns.duplicated().any() or required - set(events.columns):
        raise ValueError(f"missing/duplicate market-audit columns: {sorted(required - set(events.columns))}")
    x = events.loc[:, sorted(required)].copy().reset_index(drop=True)
    if x.event_id.duplicated().any() or not x.event_id.map(lambda v: isinstance(v, str) and bool(v.strip())).all():
        raise ValueError("event_id must be unique canonical strings")
    venues = {str(v).lower() for v in x.venue}
    if len(venues) != 1 or not venues.issubset(ALLOWED_VENUES):
        raise PermissionError("exactly one registered development venue is required")
    x["decision_at"] = _utc(x.decision_at, "decision_at")
    x["label_available_at"] = _utc(x.label_available_at, "label_available_at")
    if not (x.label_available_at > x.decision_at).all() or not x.label.isin(CLASS_ORDER).all():
        raise ValueError("invalid label or label-availability chronology")
    for name in ("regime_confidence", "entry_price", "stop_price", "target_price", "gross_return"):
        x[name] = pd.to_numeric(x[name], errors="raise")
        if not np.isfinite(x[name].to_numpy(float)).all():
            raise ValueError(f"nonfinite {name}")
    if not x.regime_confidence.between(0, 1).all():
        raise ValueError("regime_confidence must be in [0,1]")
    if not ((x.stop_price > 0) & (x.stop_price < x.entry_price) & (x.entry_price < x.target_price)).all():
        raise ValueError("only valid LONG spot geometry is supported")
    for name in FEATURE_COLUMNS:
        x[name] = pd.to_numeric(x[name], errors="raise")
        if np.isinf(x[name].to_numpy(float)).any():
            raise ValueError(f"infinite feature: {name}")
    for name in ("data_version", "policy_hash"):
        if not x[name].map(lambda v: isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdef" for c in v.lower())).all():
            raise ValueError(f"invalid SHA-256 identity: {name}")
    return x.sort_values(["decision_at", "event_id"], kind="mergesort").reset_index(drop=True)


def _temperature(prob: np.ndarray, value: float) -> np.ndarray:
    logits = np.log(np.clip(prob, np.finfo(float).tiny, 1.0)) / value
    logits -= logits.max(axis=1, keepdims=True)
    exp = np.exp(logits)
    return exp / exp.sum(axis=1, keepdims=True)


def _metrics(prob: np.ndarray, y: np.ndarray) -> dict:
    one_hot = np.eye(3)[y]
    correct = prob[np.arange(len(y)), y]
    return {
        "brier": float(np.mean(np.sum((prob - one_hot) ** 2, axis=1))),
        "log_loss": float(-np.mean(np.log(np.clip(correct, np.finfo(float).tiny, 1.0)))),
        "event_count": int(len(y)),
    }


def _entropy(prob: np.ndarray) -> float:
    value = -float(np.sum(prob * np.log(np.clip(prob, np.finfo(float).tiny, 1.0)))) / math.log(3)
    return float(np.clip(value, 0, 1))


def _admission(row: pd.Series, prob: np.ndarray, entropy: float, shift: float) -> tuple[float, str]:
    p = PROTOCOL
    risk = (row.entry_price - row.stop_price) / row.entry_price
    reward = (row.target_price - row.entry_price) / row.entry_price
    utility = (prob[0] * reward - prob[1] * risk - prob[2] * p.timeout_loss_multiple * risk
               - p.round_trip_cost_bps / 10_000 - p.uncertainty_loss_multiple * risk * entropy)
    if row.regime_confidence < p.min_regime_confidence:
        return float(utility), "LOW_REGIME_CONFIDENCE"
    if entropy > p.max_entropy:
        return float(utility), "HIGH_ENTROPY"
    if shift > p.max_shift:
        return float(utility), "DISTRIBUTION_SHIFT"
    if utility <= 0:
        return float(utility), "COST_UTILITY_GATE"
    return float(utility), "ADMITTED"


def _split(x: pd.DataFrame, calibration_start: pd.Timestamp, test_start: pd.Timestamp) -> dict:
    p = PROTOCOL
    original = {
        "train": x[x.decision_at < calibration_start],
        "calibration": x[(x.decision_at >= calibration_start) & (x.decision_at < test_start)],
        "test": x[x.decision_at >= test_start],
    }
    embargo = pd.Timedelta(seconds=p.embargo_seconds)
    split = {
        "train": original["train"][original["train"].label_available_at < calibration_start - embargo],
        "calibration": original["calibration"][original["calibration"].label_available_at < test_start - embargo],
        "test": original["test"],
    }
    for role, minimum in (("train", p.min_train), ("calibration", p.min_calibration), ("test", p.min_test)):
        if len(split[role]) < minimum:
            raise ValueError(f"insufficient {role} support after purge/embargo")
    if set(split["train"].label) != set(CLASS_ORDER):
        raise ValueError("training fold must contain TP, SL and TIMEOUT")
    split["original"] = original
    return split


def _fold(x: pd.DataFrame, index: int, calibration_start: pd.Timestamp, test_start: pd.Timestamp):
    p = PROTOCOL
    split = _split(x, calibration_start, test_start)
    class_index = {name: i for i, name in enumerate(CLASS_ORDER)}
    y = {role: split[role].label.map(class_index).to_numpy(int) for role in ("train", "calibration", "test")}
    train = split["train"].loc[:, FEATURE_COLUMNS].to_numpy(float)
    if np.isnan(train).all(axis=0).any():
        raise ValueError("all-missing training feature")
    imputer = SimpleImputer(strategy="median")
    scaler = StandardScaler()
    train_scaled = scaler.fit_transform(imputer.fit_transform(train))
    model = LogisticRegression(C=p.logistic_c, solver="lbfgs", max_iter=1000, random_state=p.seed)
    model.fit(train_scaled, y["train"])
    order = [int(np.flatnonzero(model.classes_ == i)[0]) for i in range(3)]
    cal_x = scaler.transform(imputer.transform(split["calibration"].loc[:, FEATURE_COLUMNS].to_numpy(float)))
    test_x = scaler.transform(imputer.transform(split["test"].loc[:, FEATURE_COLUMNS].to_numpy(float)))
    cal_raw = model.predict_proba(cal_x)[:, order]
    test_raw = model.predict_proba(test_x)[:, order]
    fitted = minimize_scalar(
        lambda t: _metrics(_temperature(cal_raw, t), y["calibration"])["log_loss"],
        bounds=p.temperature_bounds, method="bounded", options={"xatol": 1e-8},
    )
    if not fitted.success:
        raise ValueError("validation-only temperature fit failed")
    calibrated = _temperature(test_raw, float(fitted.x))
    prevalence = np.array([(split["train"].label == c).mean() for c in CLASS_ORDER], float)
    baseline = np.repeat(prevalence.reshape(1, -1), len(split["test"]), axis=0)
    pred = []
    for i, (_, row) in enumerate(split["test"].iterrows()):
        ent = _entropy(calibrated[i])
        shift = float(np.max(np.abs(test_x[i])))
        utility, reason = _admission(row, calibrated[i], ent, shift)
        pred.append({
            "fold_index": index, "event_id": row.event_id, "symbol": row.symbol,
            "decision_at": row.decision_at.isoformat(), "label": row.label,
            "probabilities": {c: float(calibrated[i, j]) for j, c in enumerate(CLASS_ORDER)},
            "class_prevalence": {c: float(prevalence[j]) for j, c in enumerate(CLASS_ORDER)},
            "entropy": ent, "shift_score": shift, "expected_utility": utility,
            "admission_reason": reason, "gross_return": float(row.gross_return),
            "net_event_return_after_24bps": float(row.gross_return - p.round_trip_cost_bps / 10_000),
        })
    compact_split = {}
    for role in ("train", "calibration", "test"):
        kept = split[role].event_id.tolist()
        compact_split[role] = {
            "start": split["original"][role].decision_at.min().isoformat(),
            "end": split["original"][role].decision_at.max().isoformat(),
            "event_count": len(kept), "row_ids_sha256": stable_hash(kept),
            "purged_event_count": len(split["original"][role]) - len(kept),
        }
    state = {
        "fold_index": index, "model_available_at": test_start.isoformat(),
        "model": {"name": "B1_MULTINOMIAL_LOGISTIC", "C": p.logistic_c,
                  "sklearn_version": sklearn_version, "feature_columns": list(FEATURE_COLUMNS),
                  "class_order": list(CLASS_ORDER), "coefficients": model.coef_[order].tolist(),
                  "intercepts": model.intercept_[order].tolist()},
        "preprocessing": {"fit_scope": "TRAIN_ONLY", "medians": imputer.statistics_.tolist(),
                          "means": scaler.mean_.tolist(), "scales": scaler.scale_.tolist()},
        "calibration": {"fit_scope": "VALIDATION_ONLY", "temperature": float(fitted.x),
                        "bounds": list(p.temperature_bounds)},
        "training_class_prevalence": {c: float(prevalence[j]) for j, c in enumerate(CLASS_ORDER)},
        "split": compact_split, "paper_execution": False, "live_execution": False,
    }
    state["inference_state_sha256"] = stable_hash(state)
    calibrated_metrics, baseline_metrics = _metrics(calibrated, y["test"]), _metrics(baseline, y["test"])
    admitted = [row["net_event_return_after_24bps"] for row in pred if row["admission_reason"] == "ADMITTED"]
    summary = {
        "fold_index": index, "split": compact_split,
        "metrics": {"model_calibrated": calibrated_metrics, "class_prevalence": baseline_metrics,
                    "brier_improvement_vs_prevalence": baseline_metrics["brier"] - calibrated_metrics["brier"],
                    "log_loss_improvement_vs_prevalence": baseline_metrics["log_loss"] - calibrated_metrics["log_loss"]},
        "admitted_event_count": len(admitted), "test_event_count": len(pred),
        "coverage": len(admitted) / len(pred),
        "mean_admitted_net_event_return_24bps": float(np.mean(admitted)) if admitted else None,
    }
    return summary, pred, state


def _weighted(folds: list[dict], family: str, metric: str) -> float:
    count = sum(f["metrics"][family]["event_count"] for f in folds)
    return float(sum(f["metrics"][family][metric] * f["metrics"][family]["event_count"] for f in folds) / count)


def _run_market_audit(events: pd.DataFrame, source_manifest: dict) -> dict:
    assert_research_only()
    x, p = _validate(events), PROTOCOL
    clocks = x.decision_at.drop_duplicates().reset_index(drop=True)
    calibration_size = int(len(clocks) * p.calibration_fraction)
    test_size = int(len(clocks) * p.total_test_fraction / p.n_splits)
    first_calibration = len(clocks) - calibration_size - p.n_splits * test_size
    if min(calibration_size, test_size, first_calibration) < 1:
        raise ValueError("insufficient decision clocks for frozen audit")
    folds, predictions, models = [], [], []
    for i in range(p.n_splits):
        cal_i = first_calibration + i * test_size
        test_i, end_i = cal_i + calibration_size, cal_i + calibration_size + test_size
        fold_events = x if end_i == len(clocks) else x[x.decision_at < clocks.iloc[end_i]]
        fold, pred, model = _fold(fold_events, i, clocks.iloc[cal_i], clocks.iloc[test_i])
        folds.append(fold); predictions.extend(pred); models.append(model)
    ids = [row["event_id"] for row in predictions]
    if len(ids) != len(set(ids)):
        raise RuntimeError("test windows overlap")
    admitted = [r["net_event_return_after_24bps"] for r in predictions if r["admission_reason"] == "ADMITTED"]
    mb, pb = _weighted(folds, "model_calibrated", "brier"), _weighted(folds, "class_prevalence", "brier")
    ml, pl = _weighted(folds, "model_calibrated", "log_loss"), _weighted(folds, "class_prevalence", "log_loss")
    aggregate = {
        "test_event_count": len(predictions), "model_calibrated_brier": mb, "class_prevalence_brier": pb,
        "brier_improvement_vs_prevalence": pb - mb, "model_calibrated_log_loss": ml,
        "class_prevalence_log_loss": pl, "log_loss_improvement_vs_prevalence": pl - ml,
        "beats_prevalence_brier": mb < pb, "beats_prevalence_log_loss": ml < pl,
        "admitted_event_count": len(admitted), "coverage": len(admitted) / len(predictions),
        "mean_admitted_net_event_return_24bps": float(np.mean(admitted)) if admitted else None,
        "development_economic_positive": bool(admitted and np.mean(admitted) > 0),
    }
    report = {
        "classification": "REAL_MARKET_DEVELOPMENT_MODEL_AUDIT",
        "evidence_scope": "PREVIOUSLY_INSPECTED_DEVELOPMENT_ONLY", "source_manifest": source_manifest,
        "venue": x.venue.iloc[0], "symbols": sorted(set(x.symbol)), "event_count": len(x),
        "feature_columns": list(FEATURE_COLUMNS), "frozen_protocol": PROTOCOL.__dict__,
        "validation_method": "EXPANDING_TRAIN_SLIDING_CALIBRATION_DISJOINT_TEST_WITH_INFORMATION_TIME_PURGE",
        "fold_count": len(folds), "folds": folds, "aggregate": aggregate,
        "same_test_retuning_prohibited": True, "development_model_training": True,
        "final_holdout": False, "profitability_confirmed": False,
        "paper_execution": False, "live_execution": False, "promotion_authorized": False,
        "blockers": ["FINAL_HOLDOUT_NOT_SEALED", "PREVIOUSLY_INSPECTED_DEVELOPMENT_DATA", "NO_PAPER_OR_LIVE_AUTHORIZATION"],
    }
    report["development_gate"] = (
        "ELIGIBLE_FOR_INDEPENDENT_HOLDOUT_REVIEW"
        if aggregate["beats_prevalence_brier"] and aggregate["beats_prevalence_log_loss"] and aggregate["development_economic_positive"]
        else "FAILED_DEVELOPMENT_GATE"
    )
    report["report_sha256"] = stable_hash(report)
    return {"report": report, "predictions": predictions, "models": models, "events": x}


def build_verified_development_events(raw_csv: bytes, *, venue: str, symbol: str,
                                      dataset_sha256: str, dataset_id: str) -> pd.DataFrame:
    replay = run_verified_development_pipeline(
        raw_csv, venue=venue, symbol=symbol, dataset_sha256=dataset_sha256,
        manifest_status="HISTORICAL_BYTES_VERIFIED_DEVELOPMENT_ONLY",
        round_trip_cost_bps=PROTOCOL.round_trip_cost_bps,
    )
    frame, quality = inspect_csv(raw_csv)
    if quality["csv_sha256"] != dataset_sha256:
        raise PermissionError("dataset digest changed after authentication")
    candidates = {event.event_id: event for event in generate_candidates(frame, venue=venue, symbol=symbol)}
    rows = []
    for record in replay.records:
        if record["outcome"] not in CLASS_ORDER or record["resolved_at"] is None or record["gross_return"] is None:
            continue
        candidate = candidates.get(record["event_id"])
        if candidate is None:
            raise RuntimeError("missing causal candidate snapshot")
        features = dict(candidate.feature_values)
        rows.append({
            "event_id": record["event_id"], "venue": venue, "symbol": symbol, "dataset_id": dataset_id,
            "decision_at": record["event_timestamp"], "label_available_at": record["resolved_at"],
            "label": record["outcome"], "regime_confidence": record["regime_confidence"],
            "entry_price": record["entry_price"], "stop_price": record["stop_price"],
            "target_price": record["target_price"], "gross_return": record["gross_return"],
            "data_version": record["data_version"], "policy_hash": record["policy_hash"],
            **{target: features.get(source) for source, target in FEATURE_MAP},
        })
    if not rows:
        raise ValueError("no complete labelled audit events")
    return pd.DataFrame(rows)


def _archive_identity(venue: str) -> tuple[str, int, int]:
    identities = {(r["archive_sha256"], r["source_run"], r["source_artifact"]) for r in SOURCES if r["venue"] == venue}
    if len(identities) != 1:
        raise ValueError("venue must have exactly one frozen archive identity")
    return identities.pop()


def audit_verified_archive(*, path: Path, venue: str, output: Path) -> dict:
    assert_research_only()
    venue = venue.lower()
    if venue not in ALLOWED_VENUES:
        raise PermissionError("no frozen V58 audit source is registered for this venue")
    archive_hash, source_run, source_artifact = _archive_identity(venue)
    source, members = audit_archive(path, venue=venue, expected_sha256=archive_hash,
                                    source_run=source_run, source_artifact=source_artifact)
    frames = []
    for dataset in source["datasets"]:
        frames.append(build_verified_development_events(
            gzip.decompress(members[dataset["file"]]), venue=venue, symbol=dataset["symbol"],
            dataset_sha256=dataset["csv_sha256"], dataset_id=dataset["dataset_id"],
        ))
    manifest = {
        "archive_sha256": archive_hash, "source_run": source_run, "source_artifact": source_artifact,
        "datasets": [{k: d[k] for k in ("dataset_id", "symbol", "csv_sha256", "row_count", "start_timestamp", "end_timestamp")}
                     for d in source["datasets"]],
    }
    result = _run_market_audit(pd.concat(frames, ignore_index=True), manifest)
    files = {
        "event_dataset.csv": result["events"].to_csv(index=False).encode(),
        "predictions.jsonl": "".join(json.dumps(r, sort_keys=True, allow_nan=False) + "\n" for r in result["predictions"]).encode(),
        "frozen_models.json": canonical_json(result["models"]),
        "market_audit_report.json": canonical_json(result["report"]),
        "source_manifest.json": canonical_json(manifest),
    }
    artifact_map = {name: sha256(content).hexdigest() for name, content in sorted(files.items())}
    bundle = {"classification": "REAL_MARKET_DEVELOPMENT_MODEL_AUDIT", "files": artifact_map,
              "artifact_map_sha256": stable_hash(artifact_map), "paper_execution": False,
              "live_execution": False, "promotion_authorized": False}
    files["artifact_manifest.json"] = canonical_json(bundle)
    write_immutable_bundle(output, files)
    return {**result["report"], "artifact_map_sha256": bundle["artifact_map_sha256"]}
