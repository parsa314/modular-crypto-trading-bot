"""Purged chronological model comparison and costed out-of-sample selection."""
from __future__ import annotations

from dataclasses import dataclass
import importlib.metadata
import json
import math
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .feature_engineer import FEATURES
from .labeler import event_outcome


PRIMARY_FEATURES = ["atr_ratio", "volume_ratio", "rsi", "macd_hist", "regime", "hour_of_day", "signal_confidence"]


@dataclass
class PrimaryMetaLabeler:
    """Native XGBoost JSON plus explicit preprocessing/admission metadata."""
    model: object
    threshold: float
    feature_names: tuple[str, ...] = tuple(PRIMARY_FEATURES)

    def predict_proba(self, frame):
        values = frame.loc[:, list(self.feature_names)]
        if not np.isfinite(values.to_numpy(float)).all():
            raise ValueError("Primary features must be finite")
        return self.model.predict_proba(values)[:, 1]

    def save(self, path, metadata=None):
        path = Path(path)
        self.model.save_model(path)
        record = {"feature_names": list(self.feature_names), "threshold": self.threshold,
                  "features": {"atr_ratio": "ATR14 / mean ATR of previous20 calendar days",
                               "volume_ratio": "volume / mean volume of previous20 calendar days",
                               "rsi": "Wilder RSI14 /100", "macd_hist": "(MACD12,26 - EMA9 signal) / ATR14",
                               "regime": "3-state forward-filtered BTC daily HMM, calibrated before2019",
                               "hour_of_day": "UTC decision hour including quarter-hour fraction",
                               "signal_confidence": "clip(abs(EMA20-EMA50)/ATR14,0,1)"},
                  **(metadata or {})}
        path.with_suffix(".metadata.json").write_text(json.dumps(record, indent=2, allow_nan=False) + "\n")

    @classmethod
    def load(cls, path):
        from xgboost import XGBClassifier
        path = Path(path)
        metadata = json.loads(path.with_suffix(".metadata.json").read_text())
        model = XGBClassifier()
        model.load_model(path)
        return cls(model, float(metadata["threshold"]), tuple(metadata["feature_names"]))


def precision_top_decile(y, probabilities) -> dict:
    y, p = np.asarray(y), np.asarray(probabilities, dtype=float)
    if len(y) == 0 or len(y) != len(p) or not np.isfinite(p).all():
        raise ValueError("Precision requires aligned finite nonempty observations")
    k = max(1, math.ceil(.1 * len(p)))
    selected = np.argsort(-p, kind="stable")[:k]
    return {"precision": float(y[selected].mean()), "selected": k, "samples": len(p)}


def purged_time_splits(events: pd.DataFrame, folds=5, purge=5, embargo=5):
    """Group simultaneous events and remove overlaps by actual label end.

    All fitting is earlier than validation. The additional embargo is an
    exclusion at the forward boundary; no future training rows are admitted.
    """
    times = np.sort(events.bar_index.unique())
    splitter = TimeSeriesSplit(n_splits=folds)
    for train_times, test_times in splitter.split(times):
        first_test = int(times[test_times[0]])
        candidates = events.bar_index.isin(times[train_times])
        candidates &= events.bar_index < first_test - purge - embargo
        candidates &= events.exit_index < first_test - purge - embargo
        test = events.bar_index.isin(times[test_times])
        train_ids, test_ids = np.flatnonzero(candidates), np.flatnonzero(test)
        if not len(train_ids) or not len(test_ids):
            raise ValueError("No events after interval-aware purging")
        yield train_ids, test_ids


def new_model(name: str):
    if name == "xgboost":
        from xgboost import XGBClassifier
        return XGBClassifier(n_estimators=150, max_depth=3, learning_rate=.05,
                             subsample=.8, colsample_bytree=.8, n_jobs=1,
                             random_state=42, eval_metric="logloss", tree_method="hist")
    if name == "lightgbm":
        from lightgbm import LGBMClassifier
        return LGBMClassifier(n_estimators=150, max_depth=5, num_leaves=15,
                              learning_rate=.05, n_jobs=1, random_state=42,
                              verbosity=-1, deterministic=True, force_col_wise=True)
    if name == "mlp":
        return make_pipeline(StandardScaler(), MLPClassifier(hidden_layer_sizes=(32, 16),
                             max_iter=60, random_state=42, batch_size=256,
                             early_stopping=False, alpha=.01))
    raise ValueError(f"Unknown model {name}")


@dataclass
class MetaLabeler:
    model: object
    threshold: float
    model_name: str
    active: bool = False
    feature_names: tuple[str, ...] = tuple(FEATURES)

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        values = frame.loc[:, list(self.feature_names)]
        if not np.isfinite(values.to_numpy(float)).all():
            raise ValueError("Nonfinite inference features")
        return self.model.predict_proba(values)[:, 1]

    def filter(self, frame: pd.DataFrame) -> pd.DataFrame:
        return frame.loc[self.predict_proba(frame) >= self.threshold].copy()


def equity_metrics(equity: pd.Series) -> dict:
    day = equity.resample("1D").last().ffill()
    returns = day.pct_change(fill_method=None)
    returns.iloc[0] = day.iloc[0] - 1.
    volatility = float(returns.std(ddof=1))
    values = np.r_[1., equity.to_numpy()]
    dd = 1 - values / np.maximum.accumulate(values)
    return {"sharpe": float(np.sqrt(365) * returns.mean() / volatility) if volatility > 1e-14 else None,
            "return": float(equity.iloc[-1] - 1), "max_drawdown": float(dd.max()),
            "daily_observations": len(day)}


def backtest_candidates(signals: pd.DataFrame, bars: pd.DataFrame, probabilities=None,
                        threshold=None, fee_bps=15.) -> tuple[dict, pd.DataFrame, pd.Series]:
    """Evaluate every candidate, including timeouts: no label-based admission.

    One fully collateralized spot long position; both policies use identical
    next-open reference fills, 2ATR/1ATR barriers, and 15bp each side.
    """
    begin = pd.Timestamp("2024-01-01T00:00:00Z")
    end = pd.Timestamp("2025-01-01T00:00:00Z")
    selected = signals[(signals.available_at >= begin) & (signals.available_at < end)].copy()
    if probabilities is not None:
        selected["probability"] = np.asarray(probabilities)
        selected = selected[selected.probability >= threshold]
    priority = "confidence"  # identical same-bar strategy ordering in both policies
    selected = selected.sort_values(["bar_index", priority, "strategy"], ascending=[True, False, True])
    selected = selected.drop_duplicates("bar_index", keep="first")
    # Execution and marks must also remain entirely in the validation year.
    valid = bars[(bars.timestamp >= begin) & (bars.available_at < end)]
    first, final = int(valid.index[0]), int(valid.index[-1])
    equity = np.ones(len(bars))
    cash, cursor, last_exit, trades = 1., first, first - 1, []
    fee = fee_bps / 10_000
    for row in selected.to_dict("records"):
        entry = int(row["bar_index"]) + 1
        if entry <= last_exit or entry > final:
            continue
        # A position still open at split end is liquidated there. It is not
        # removed because its future outcome or timeout is inconvenient.
        row["horizon_m15_bars"] = min(row["horizon_m15_bars"], final - entry + 1)
        outcome = event_outcome(bars, row)
        if outcome is None:
            continue
        stop = outcome["exit_index"]
        equity[cursor:entry] = cash
        quantity = cash / (outcome["entry_price"] * (1 + fee))
        equity[entry:stop] = quantity * bars.close.iloc[entry:stop].to_numpy()
        before = cash
        cash = quantity * outcome["exit_price"] * (1 - fee)
        equity[stop] = cash
        trades.append({"event_id": row["event_id"], "strategy": row["strategy"],
                       "entry_index": entry, "exit_index": stop, "reason": outcome["reason"],
                       "pnl": cash - before, "net_return": cash / before - 1,
                       "entry_time": bars.timestamp.iloc[entry], "exit_time": bars.available_at.iloc[stop]})
        last_exit, cursor = stop, stop + 1
    equity[cursor:final + 1] = cash
    series = pd.Series(equity[first:final + 1], index=pd.DatetimeIndex(bars.available_at.iloc[first:final + 1]))
    metrics = equity_metrics(series)
    metrics["trades"] = len(trades)
    metrics["timeouts_traded"] = sum(t["reason"] == "timeout" for t in trades)
    return metrics, pd.DataFrame(trades), series


def run_training(features: pd.DataFrame, labels: pd.DataFrame, bars: pd.DataFrame, provenance: dict) -> dict:
    cutoff = pd.Timestamp("2024-01-01T00:00:00Z")
    end = pd.Timestamp("2025-01-01T00:00:00Z")
    events = features.merge(labels, on="event_id", validate="one_to_one")
    train = events[(events.available_at < cutoff) & (events.label_end < cutoff)].sort_values(["bar_index", "event_id"]).reset_index(drop=True)
    val = events[(events.available_at >= cutoff) & (events.label_end < end)].copy()
    if len(features) < 5000:
        raise ValueError(f"Only {len(features)} genuine candidate rows; refusing to duplicate to5000")
    scores, fitted, oof_predictions = {}, {}, {}
    for name in ("xgboost", "lightgbm", "mlp"):
        oof = np.full(len(train), np.nan)
        fold_metrics = []
        for fold, (fit_ids, test_ids) in enumerate(purged_time_splits(train), 1):
            model = new_model(name)
            model.fit(train.iloc[fit_ids][FEATURES], train.label.iloc[fit_ids].astype(int))
            p = model.predict_proba(train.iloc[test_ids][FEATURES])[:, 1]
            oof[test_ids] = p
            fold_metrics.append({"fold": fold, **precision_top_decile(train.label.iloc[test_ids], p),
                                 "train_rows": len(fit_ids), "test_rows": len(test_ids),
                                 "latest_train_label_index": int(train.exit_index.iloc[fit_ids].max()),
                                 "first_test_index": int(train.bar_index.iloc[test_ids].min())})
        scores[name] = {"folds": fold_metrics, "mean_precision_top_decile": float(np.mean([f["precision"] for f in fold_metrics]))}
        oof_predictions[name] = oof
        print(json.dumps({"phase": "A.3", "model": name, **scores[name]}), flush=True)
        model = new_model(name)
        model.fit(train[FEATURES], train.label.astype(int))
        fitted[name] = model
    selected_name = max(scores, key=lambda name: scores[name]["mean_precision_top_decile"])
    oof = oof_predictions[selected_name]
    threshold = float(np.quantile(oof[np.isfinite(oof)], .9))
    labeler = MetaLabeler(fitted[selected_name], threshold, selected_name)
    pval = labeler.predict_proba(val)
    precision = precision_top_decile(val.label, pval)
    all_val = features[(features.available_at >= cutoff) & (features.available_at < end)].copy()
    all_probability = labeler.predict_proba(all_val)
    raw, raw_trades, raw_equity = backtest_candidates(features, bars)
    filtered, filtered_trades, filtered_equity = backtest_candidates(features, bars, all_probability, threshold)
    improvement = None if raw["sharpe"] is None or filtered["sharpe"] is None else filtered["sharpe"] - raw["sharpe"]
    passed = precision["precision"] > .60 and improvement is not None and improvement > .3
    labeler.active = bool(passed)
    with open("models/meta_labeler_v1.pkl", "wb") as f:
        pickle.dump(labeler, f)
    val["probability"] = pval
    val[["event_id", "available_at", "label", "probability", "strategy"]].to_parquet("data/ai/validation_predictions.parquet", index=False)
    for tag, trades in (("raw", raw_trades), ("ml", filtered_trades)):
        trades.to_parquet(f"data/ai/{tag}_validation_trades.parquet", index=False)
    pd.DataFrame({"raw": raw_equity, "ml": filtered_equity}).to_parquet("data/ai/validation_equity.parquet")
    result = {"phase": "A", "strategy": "meta_labeling_existing_long_entry_candidates", "model": selected_name,
              "train_window": "2022-2023, after causal HMM warmup", "validation_window": "2024",
              "feature_rows": len(features), "labeled_train": len(train), "labeled_val": len(val),
              "precision_top_decile_val": precision["precision"], "top_decile": precision,
              "train_oof_probability_threshold": threshold, "model_selection": "highest mean train5fold precision only",
              "model_comparison": scores, "raw_validation": raw, "ml_validation": filtered,
              "sharpe_improvement": improvement, "verdict": "PASS" if passed else "FAIL",
              "ml_layer_active": passed, "next_phase": "B", "seed": 42, "purge_bars": 5, "embargo_bars": 5,
              "additional_overlap_purge": "actual label exit must precede validation start minus10M15bars",
              "timeout_handling": "dropped from labels only; retained in both executable validation policies",
              "cost_bps_each_side": 15, "test3_opened": False, "provenance": provenance,
              "versions": {p: importlib.metadata.version(p) for p in ("numpy", "pandas", "scikit-learn", "xgboost", "lightgbm", "hmmlearn")},
              "limitations": ["BTC-only development evidence; no multi-asset or Forex generalization claim",
                              "Individual v1 arm candidates are distinct from original confluence executed trades",
                              "v4-v6 entry candidates share newly requested barriers; not replication of old strategy Sharpe",
                              "top-decile diagnostic ranks retrospective validation probabilities; trading threshold fixed from trainOOF",
                              "Historical OHLC same-bar stop/target ties conservatively count as stop"]}
    Path("results/A_meta_labeler.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return {k: result[k] for k in ("model", "feature_rows", "precision_top_decile_val", "raw_validation", "ml_validation", "sharpe_improvement", "verdict")}
