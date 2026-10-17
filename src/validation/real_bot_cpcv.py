"""Nested primary EMA+ML+PPO CPCV using purgedcv 0.1.11.

Each outer split refits both learned layers. Calendar groups hold simultaneous
events together; actual trade exits extend their purge intervals. Five OOS
paths are reconstructed separately, never pooled as independent observations.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import time

import numpy as np
import pandas as pd
from purgedcv import (CombinatorialPurgedCV, deflated_sharpe_ratio_full,
                      probability_of_backtest_overfitting)

DAY = pd.Timedelta(days=1)
MARGIN = pd.Timedelta(minutes=75)
CANDIDATES = ("fixed_fraction_2pct", "ppo")


def _utc(value):
    time = pd.Timestamp(value)
    return time.tz_localize("UTC") if time.tzinfo is None else time.tz_convert("UTC")


def _safe(value):
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [_safe(item) for item in value]
    if isinstance(value, (pd.Timestamp, Path)):
        return str(value)
    if isinstance(value, np.generic):
        return _safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(_safe(value), indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


@dataclass
class OuterFold:
    fold_id: int
    train_dates: np.ndarray
    test_dates: np.ndarray
    test_groups: tuple[int, ...]


@dataclass
class CPCVLayout:
    dates: pd.DatetimeIndex
    group_ids: np.ndarray
    splitter: CombinatorialPurgedCV
    folds: list[OuterFold]


def build_cpcv_layout(events, *, start=None, end=None):
    """Build actual N=6,k=2 purgedcv folds on complete calendar-date groups."""
    if importlib.metadata.version("purgedcv") != "0.1.11":
        raise RuntimeError("This experiment requires installed purgedcv==0.1.11")
    if events.empty or not {"available_at", "exit_time"}.issubset(events):
        raise ValueError("Events need prediction available_at and actual exit_time including timeouts")
    prediction = pd.to_datetime(events.available_at, utc=True)
    evaluation = pd.to_datetime(events.exit_time, utc=True)
    if prediction.isna().any() or evaluation.isna().any() or (evaluation <= prediction).any():
        raise ValueError("Every event needs a later finite actual exit_time, including timeouts")
    start = _utc(start).floor("D") if start is not None else prediction.min().floor("D")
    end = _utc(end).ceil("D") if end is not None else evaluation.max().ceil("D")
    dates = pd.date_range(start, end, freq="1D", inclusive="left")
    if len(dates) < 6:
        raise ValueError("Six nonempty calendar groups are required")
    actual_end = pd.Series(evaluation.to_numpy(), index=prediction.dt.floor("D")).groupby(level=0).max()
    actual_end.index = pd.to_datetime(actual_end.index, utc=True)
    actual_end = pd.to_datetime(actual_end, utc=True).reindex(dates)
    # Every test date includes its entire return period. Longer actual event
    # horizons extend that interval; no event may leak across a held boundary.
    daily_end = pd.Series(dates + DAY, index=dates)
    evaluation_by_date = pd.concat([daily_end, actual_end], axis=1).max(axis=1)
    cv = CombinatorialPurgedCV(6, 2, prediction_times=pd.Series(dates),
                               evaluation_times=pd.Series(evaluation_by_date.to_numpy()),
                               purge_horizon=MARGIN, embargo=MARGIN)
    group_ids = np.empty(len(dates), dtype=int)
    for group, indices in enumerate(np.array_split(np.arange(len(dates)), 6)):
        group_ids[indices] = group
    folds = [OuterFold(number, train, test, tuple(np.unique(group_ids[test]).tolist()))
             for number, (train, test) in enumerate(cv.split(np.zeros((len(dates), 1))), 1)]
    return CPCVLayout(dates, group_ids, cv, folds)


def rank_utility(returns):
    """Monotone Sharpe rank utility; zero-volatility slices rank strictly last."""
    values = np.asarray(returns, dtype=float)
    std = float(values.std(ddof=0))
    if len(values) < 2 or not math.isfinite(std) or std <= 1e-14:
        return -2.
    return float(2 * np.arctan(values.mean() / std) / np.pi)


def _sharpe(values, ddof=1):
    std = float(np.std(values, ddof=ddof)) if len(values) > ddof else 0.
    return float(np.mean(values) / std) if math.isfinite(std) and std > 1e-14 else None


def _distribution(values):
    finite = np.asarray([value for value in values if value is not None], dtype=float)
    if not len(finite):
        return {"defined": 0, "mean": None, "median": None, "min": None, "max": None,
                "p05": None, "p95": None}
    return {"defined": len(finite), "mean": float(finite.mean()), "median": float(np.median(finite)),
            "min": float(finite.min()), "max": float(finite.max()),
            "p05": float(np.quantile(finite, .05)), "p95": float(np.quantile(finite, .95))}


def summarize_paths(paths, *, n_trials=2):
    """Calculate actual library CSCV PBO and moment-correct DSR per OOS path."""
    matrix = np.asarray(paths, dtype=float)
    if matrix.ndim != 3 or matrix.shape[0] != 5 or matrix.shape[1] != 2 or matrix.shape[2] < 6:
        raise ValueError("Expected five paths by two real candidates by at least six daily returns")
    if not np.isfinite(matrix).all() or (matrix < -1).any():
        raise ValueError("Daily path returns must be finite and cannot lose more than capital")
    if not isinstance(n_trials, int) or n_trials < 2:
        raise ValueError("DSR multiplicity must include at least the two actual sizing candidates")
    reports = []
    for number, candidates in enumerate(matrix, 1):
        observed_sr = [_sharpe(candidate, ddof=0) for candidate in candidates]
        defined = [value is not None for value in observed_sr]
        pbo = pbo_diagnostics = None
        if any(defined):
            result = probability_of_backtest_overfitting(candidates, n_splits=6, metric=rank_utility)
            pbo, pbo_diagnostics = float(result.pbo), _safe(asdict(result))
        dsr = dsr_diagnostics = None
        reason = None
        if not defined[1]:
            reason = "PPO path has zero volatility; its Sharpe and DSR are undefined"
        elif not all(defined):
            reason = "Two defined candidate Sharpes are required to estimate trial Sharpe variance"
        else:
            # Population per-observation Sharpes match purgedcv's DSR moment
            # convention. No annualized Sharpe enters the deflated benchmark.
            variance = float(np.var(observed_sr, ddof=1))
            try:
                result = deflated_sharpe_ratio_full(candidates[1], n_trials=n_trials, var_sharpe=variance)
                dsr, dsr_diagnostics = float(result.dsr), asdict(result)
            except ValueError as exc:
                reason = str(exc)
        wealth = np.r_[1., np.cumprod(1 + candidates[1])]
        drawdown = 1 - np.divide(wealth, np.maximum.accumulate(wealth))
        reports.append({"path": number, "pbo": pbo, "pbo_defined": pbo is not None,
                        "dsr": dsr, "dsr_defined": dsr is not None, "dsr_undefined_reason": reason,
                        "fixed_sharpe": None if _sharpe(candidates[0]) is None else _sharpe(candidates[0]) * np.sqrt(365),
                        "ppo_sharpe": None if _sharpe(candidates[1]) is None else _sharpe(candidates[1]) * np.sqrt(365),
                        "ppo_return": float(wealth[-1] - 1), "ppo_max_drawdown": float(drawdown.max()),
                        "observations": matrix.shape[2],
                        "zero_volatility_candidates": [name for name, ok in zip(CANDIDATES, defined) if not ok],
                        "pbo_diagnostics": pbo_diagnostics, "dsr_diagnostics": dsr_diagnostics})
    complete_pbo = all(path["pbo_defined"] for path in reports)
    complete_dsr = all(path["dsr_defined"] for path in reports)
    pbo = float(np.mean([path["pbo"] for path in reports])) if complete_pbo else None
    dsr = float(min(path["dsr"] for path in reports)) if complete_dsr else None
    rejected = pbo is None or dsr is None or pbo > .5 or dsr < .95
    return _safe({"pbo": pbo, "pbo_defined": complete_pbo, "dsr": dsr, "dsr_defined": complete_dsr,
                  "conservative_dsr_rejection_bound": 0. if not complete_dsr else dsr,
                  "pbo_aggregation": "mean of five separately computed path CSCV PBO values",
                  "dsr_aggregation": "minimum of five separate path DSR probabilities",
                  "pbo_scope": list(CANDIDATES), "candidate_count": 2, "n_trials": n_trials,
                  "pbo_method": "purgedcv.probability_of_backtest_overfitting, CSCV six blocks/20 combinations per path",
                  "pbo_rank_utility": "monotone atan(per-observation Sharpe), constant slices ranked below every defined Sharpe",
                  "dsr_units": "per-observation population Sharpe; sample variance across two actual candidates; non-excess kurtosis",
                  "multiplicity_limitation": "Two sizing algorithms are the observed selection scope; earlier project experiments are not a known complete independent-trial census",
                  "path_statistics": reports,
                  "ppo_sharpe_distribution": _distribution([path["ppo_sharpe"] for path in reports]),
                  "fixed_sharpe_distribution": _distribution([path["fixed_sharpe"] for path in reports]),
                  "rejection_rule": "PBO > 0.5 or DSR < 0.95; undefined statistics cannot establish acceptance",
                  "verdict": "REJECTED" if rejected else "PASS"})


def _segments(layout, positions):
    """Split surviving dates at gaps and at each of the six group boundaries."""
    positions = np.sort(np.asarray(positions, dtype=int))
    if not len(positions):
        return []
    cuts = np.flatnonzero((np.diff(positions) != 1)
                         | (layout.group_ids[positions[1:]] != layout.group_ids[positions[:-1]])) + 1
    return [(layout.dates[part[0]], layout.dates[part[-1]] + DAY, part)
            for part in np.split(positions, cuts)]


def add_fold_distribution(report, model_directory="models/cpcv"):
    """Add descriptive fifteen-fold scores without pooling folds into PBO/DSR."""
    scores = []
    for fold in report["folds"]:
        path = Path(model_directory) / f"fold_{fold['fold_id']:02d}" / "oos_returns.parquet"
        returns = pd.read_parquet(path)
        score = {"fold": fold["fold_id"], "held_daily_observations": len(returns)}
        for name in CANDIDATES:
            value = _sharpe(returns[name].to_numpy(float))
            score[name] = value * np.sqrt(365.) if value is not None else None
        scores.append(score)
    report["fold_sharpes"] = scores
    report["fold_sharpe_distribution"] = {
        name: _distribution([fold[name] for fold in scores]) for name in CANDIDATES}
    report["fold_score_definition"] = (
        "Daily Sharpe across that fold's actual held dates; group episodes reset capital. "
        "Fold scores are descriptive and are not pooled into path PBO/DSR observations.")
    return report


def _fold_worker(events, bars, layout, fold, output_directory, steps, seed):
    from src.ml.real_bot_pipeline import fit_primary_model, score_primary_events, trade_stream
    from src.rl.train_signal_ppo import train_signal_ppo
    from src.rl.signal_env import evaluate_signal_policy

    started = time.monotonic()
    folder = Path(output_directory) / f"fold_{fold.fold_id:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    train_segments = _segments(layout, fold.train_dates)
    training_parts = []
    for episode, (start, end, _) in enumerate(train_segments):
        part = events[(events.available_at >= start) & (events.available_at < end)
                      & (events.exit_time <= end)].copy()
        part["episode_id"] = episode
        training_parts.append(part)
    train = pd.concat(training_parts, ignore_index=True).sort_values(["available_at", "event_id"])
    held_dates = set(layout.dates[fold.test_dates])
    if set(train.available_at.dt.floor("D")) & held_dates:
        raise ValueError("Outer test dates leaked into model fitting")
    model, threshold, oof, inner = fit_primary_model(train)
    if not np.isfinite(oof.reindex(train.event_id).to_numpy(float)).all():
        raise ValueError("All outer-training events, including timeouts, require genuine inner OOF scores")
    model.save(folder / "meta_labeler.json", {"outer_fold": fold.fold_id, "seed": seed,
                                             "threshold_source": "inner purged five-fold OOF only"})
    approved_parts, periods = [], {}
    for episode, (start, end, _) in enumerate(train_segments):
        approved = trade_stream(train[train.episode_id.eq(episode)], bars, oof, threshold, start, end)
        approved["episode_id"] = episode
        approved_parts.append(approved)
        periods[episode] = {"start": start, "end": end}
    approved = pd.concat(approved_parts, ignore_index=True)
    approved["split"], approved["probability_source"] = "train", "purged_oof"
    approved.attrs.update(episode_periods=periods, period_start=layout.dates[0], period_end=layout.dates[-1] + DAY)
    artifact = approved.copy()
    artifact.attrs = {}
    artifact.to_parquet(folder / "approved_train.parquet", index=False)
    agent, training = train_signal_ppo(approved, steps=steps, seed=seed, output_path=folder / "ppo.zip")
    if training["actual_environment_steps"] < 100000:
        raise ValueError("A completed fold must train PPO for at least 100000 actual steps")
    values = np.zeros((2, len(fold.test_dates)))
    lookup = {index: offset for offset, index in enumerate(fold.test_dates)}
    evaluations = []
    for start, end, indices in _segments(layout, fold.test_dates):
        held = events[(events.available_at >= start) & (events.available_at < end)].copy()
        probability = score_primary_events(model, held)
        stream = trade_stream(held, bars, probability, threshold, start, end)
        stream["split"], stream["probability_source"] = "val", "frozen_train_model"
        stream.attrs.update(period_start=start, period_end=end)
        if len(stream) and (stream.exit_time > end).any():
            raise ValueError("A held trade outcome crossed the held group boundary")
        segment = {"start": str(start), "end_exclusive": str(end), "approved_events": len(stream)}
        for candidate, policy in enumerate((lambda _: .02,
                                             lambda observation: agent.predict(observation, deterministic=True)[0])):
            metrics, daily = evaluate_signal_policy(stream, policy, start=start, end=end)
            calendar = layout.dates[indices]
            if not isinstance(daily.index, pd.DatetimeIndex) or not daily.index.equals(calendar):
                raise ValueError("Sizing evaluation must return every actual held calendar day exactly once")
            result = daily.to_numpy(float)
            if not np.isfinite(result).all():
                raise ValueError("Nonfinite OOS daily returns")
            values[candidate, [lookup[int(index)] for index in indices]] = result
            segment[CANDIDATES[candidate]] = metrics
        evaluations.append(segment)
    pd.DataFrame(values.T, index=layout.dates[fold.test_dates], columns=CANDIDATES).to_parquet(folder / "oos_returns.parquet")
    report = {"fold_id": fold.fold_id, "status": "COMPLETED", "test_groups": fold.test_groups,
              "train_events": len(train), "train_binary_labels": int(train.label.notna().sum()),
              "approved_training_events": len(approved), "inner_folds": inner,
              "episode_periods": periods,
              "threshold": threshold, "threshold_source": "outer-train inner OOF90th percentile",
              "training": training, "test_segments": evaluations,
              "elapsed_seconds": time.monotonic() - started, "model_directory": str(folder)}
    _write_json(folder / "result.json", report)
    print(json.dumps(_safe({"phase": 4, "fold": fold.fold_id, "status": "completed",
                           "ppo_steps": training["actual_environment_steps"], "seconds": report["elapsed_seconds"]})), flush=True)
    return report, values


def run_cpcv(*, output="results/final_validation.json", models="models/cpcv", steps=100000, seed=42, workers=2):
    """Run fifteen genuine nested refits; never reuse a model fit on all events."""
    from src.ml.real_bot_pipeline import load_primary_events

    if workers not in (1, 2) or steps < 100000:
        raise ValueError("Use one or two isolated workers and at least100000 PPO steps per fold")
    events, bars = load_primary_events()
    events = events.copy()
    for name in ("available_at", "exit_time", "label_end"):
        events[name] = pd.to_datetime(events[name], utc=True)
    layout = build_cpcv_layout(events, start=bars.timestamp.min(), end=bars.available_at.max())
    results, predictions = {}, {}
    if workers == 1:
        for fold in layout.folds:
            report, values = _fold_worker(events, bars, layout, fold, models, steps, seed)
            results[fold.fold_id], predictions[fold.fold_id] = report, values
    else:
        with ProcessPoolExecutor(max_workers=2) as pool:
            pending = {pool.submit(_fold_worker, events, bars, layout, fold, models, steps, seed): fold.fold_id
                       for fold in layout.folds}
            for future in as_completed(pending):
                report, values = future.result()
                results[report["fold_id"]], predictions[report["fold_id"]] = report, values
    fixed_paths = layout.splitter.reconstruct_paths([predictions[fold.fold_id][0] for fold in layout.folds])
    ppo_paths = layout.splitter.reconstruct_paths([predictions[fold.fold_id][1] for fold in layout.folds])
    paths = np.stack([fixed_paths, ppo_paths], axis=1)
    statistics = summarize_paths(paths)
    path_file = Path(output).with_name(Path(output).stem + "_paths.parquet")
    path_file.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({f"{name}_path_{path + 1}": paths[path, candidate]
                  for path in range(5) for candidate, name in enumerate(CANDIDATES)}, index=layout.dates).to_parquet(path_file)
    source = Path("data/real_bot/primary_events.parquet")
    report = {"phase": 4, "strategy": "primary_ema20_50_ml_ppo", "purgedcv_version": importlib.metadata.version("purgedcv"),
              "N": 6, "k": 2, "outer_splits": 15, "oos_paths": 5, "completed_refits": len(results),
              "prediction_grouping": "whole UTC calendar dates; simultaneous events never split",
              "purge_bars": 5, "embargo_bars": 5, "bar_minutes": 15,
              "purge_padding_minutes": 75, "embargo_minutes": 75,
              "intervals": "calendar-day interval extended to latest actual event exit; conservative whole-date purge",
              "training": "ML and threshold refit inside every outer train; PPO trains only inner-OOF approved training events",
              "group_boundary_policy": "training outcomes crossing segments are dropped; held trades close at their final actual permitted close",
              "timeout_policy": "retained in ML inference and PPO sizing, never fabricated binary classes",
              "daily_close_binning": "actual closed marks/exit availability minus1ns; no duplicated dates across the five separate paths",
              "candidate_scope": list(CANDIDATES), "seed": seed, "requested_steps_per_fold": steps,
              "workers": workers, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "folds": [results[fold.fold_id] for fold in layout.folds],
              "path_returns_file": str(path_file), **statistics,
              "limitations": ["Retrospective2022–2024 source; previously inspected validation is not a new holdout",
                              "Date-grouped purging is conservative relative to individual15minute event rows",
                              "Five reconstructed CPCV paths are dependent resamplings, not five independent datasets",
                              "PBO selection scope is exactly fixed2%versusPPO sizing; prior project search multiplicity is not fully counted"]}
    add_fold_distribution(report, models)
    _write_json(output, report)
    return report
