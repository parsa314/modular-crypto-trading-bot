"""Frozen research CLI: official history -> calibrated ML -> cost-aware account.

This module never imports execution services or loads exchange credentials.
Quality failure produces a retained blocked report, never synthetic repairs.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import importlib.metadata
import json
import math
from pathlib import Path
import subprocess

import pandas as pd

from .profit_backtest import BacktestConfig, run_backtest, run_baselines
from .profit_model import calendar_folds, probability_diagnostics, walk_forward_predictions
from .spot_history import DataIntegrityError, load_validated_history


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT.parent / "configs/binance_profit_study.json"
SOURCE_PATHS = ("research/profit_pipeline.py", "research/profit_backtest.py",
                "research/profit_model.py", "research/profit_features.py",
                "research/spot_history.py", "features.py", "binance_spot_archive.py")


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2,
                               allow_nan=False) + "\n", encoding="utf-8")


def source_hashes() -> dict:
    return {name: sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_PATHS}


def load_config(path: str | Path) -> dict:
    config = json.loads(Path(path).read_text())
    canonical = json.loads(CONFIG_PATH.read_text())
    # This protocol fixes all knobs before results. A different experiment
    # requires its own reviewed version rather than silent CLI overrides.
    if config != canonical or any(type(config[k]) is not type(v) for k, v in canonical.items()):
        raise ValueError("Configuration differs from the frozen V1 protocol")
    if config["execution_authorized"] is not False:
        raise ValueError("Historical research cannot authorize execution")
    return config


def freeze(config_path: str | Path, output: str | Path) -> dict:
    config = load_config(config_path)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise FileExistsError("Freeze directory must be empty; preserve prior trials")
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                              check=True, capture_output=True, text=True).stdout.strip()
    record = {"experiment_id": config["experiment_id"], "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "config": config, "config_sha256": sha256(Path(config_path).read_bytes()).hexdigest(),
              "source_hashes": source_hashes(), "base_git_sha": revision,
              "planned_folds": len(calendar_folds(config["start_utc"], config["end_exclusive_utc"])),
              "metrics_status": "NOT_EVALUATED", "execution_authorized": False}
    _write(output / "freeze.json", record)
    return record


def gate_summary(metrics: dict, stress: dict, calibration: dict, *, fitted_folds: int,
                 expected_folds: int, is_sharpe: float | None, config: dict) -> dict:
    def ge(value, threshold):
        return value is not None and math.isfinite(float(value)) and value >= threshold
    def lt(value, threshold):
        return value is not None and math.isfinite(float(value)) and value < threshold
    sharpe = metrics["sharpe"]
    stress_degradation = (max(0., (sharpe - stress["sharpe"]) / sharpe)
                          if ge(sharpe, 1e-12) and stress["sharpe"] is not None else None)
    ratio = sharpe / is_sharpe if ge(is_sharpe, 1e-12) and sharpe is not None else None
    gates = {
        "folds_complete": fitted_folds == expected_folds,
        "minimum_folds": fitted_folds >= config["minimum_walk_forward_folds"],
        "monthly_return": ge(metrics["monthly_geometric_return"], config["target_monthly_net_return"]),
        "drawdown": lt(metrics["max_drawdown"], config["max_acceptable_drawdown_fraction"]),
        "sharpe": ge(sharpe, config["minimum_daily_annualized_sharpe"]),
        "round_trips": metrics["trade_count"] >= config["minimum_oos_round_trips"],
        "annual_trade_rate": metrics["trade_count"] * 365 / metrics["elapsed_days"] >= config["minimum_round_trips_per_year"],
        "win_rate": ge(metrics["win_rate"], config["minimum_win_rate"]),
        "profit_factor": ge(metrics["profit_factor"], config["minimum_profit_factor"]),
        "calmar": ge(metrics["calmar"], config["minimum_calmar"]),
        "calibration": lt(calibration["ece_10"], config["maximum_oos_calibration_error"]),
        "oos_is_sharpe": ge(ratio, config["minimum_oos_is_sharpe_ratio"]),
        "cost_stress": lt(stress_degradation, config["maximum_stress_sharpe_degradation"]),
        # Hourly OHLCV cannot simulate a measured 500ms fill or a forward month.
        "latency_stress_verified": False, "prospective_evidence_verified": False,
    }
    return {"checks": gates, "failed_checks": [k for k, v in gates.items() if not v],
            "historical_economic_checks_passed": all(v for k, v in gates.items() if k not in {"latency_stress_verified", "prospective_evidence_verified"}),
            "oos_is_sharpe_ratio": ratio, "stress_sharpe_degradation": stress_degradation,
            "execution_authorized": False}


def run(config_path: str | Path, history: str | Path, output: str | Path) -> dict:
    config = load_config(config_path)
    output = Path(output)
    frozen = json.loads((output / "freeze.json").read_text())
    if frozen["source_hashes"] != source_hashes() or frozen["config_sha256"] != sha256(Path(config_path).read_bytes()).hexdigest():
        raise ValueError("Frozen config/source changed; a new trial is required")
    if (output / "report.json").exists():
        raise FileExistsError("Trial has already produced a report; never overwrite evidence")
    manifest = json.loads((Path(history) / "manifest.json").read_text())
    identity = (manifest.get("symbol"), manifest.get("market"), manifest.get("timeframe"),
                manifest.get("start_inclusive"), manifest.get("end_exclusive"))
    expected = (config["symbol"].replace("/", ""), config["market"], config["timeframe"],
                pd.Timestamp(config["start_utc"]).isoformat(), pd.Timestamp(config["end_exclusive_utc"]).isoformat())
    if identity != expected:
        raise ValueError("Dataset identity/window differs from the frozen experiment")
    report = {"experiment_id": config["experiment_id"], "execution_authorized": False,
              "live_readiness": "NOT_AUTHORIZED", "history_manifest_sha256": sha256((Path(history) / "manifest.json").read_bytes()).hexdigest(),
              "source_hashes": source_hashes(), "economic_metrics": None,
              "created_at_utc": datetime.now(timezone.utc).isoformat()}
    try:
        ohlcv, verified = load_validated_history(history)
    except (DataIntegrityError, OSError, KeyError, ValueError) as exc:
        report.update(status="BLOCKED_DATA_QUALITY_OR_INTEGRITY", metrics_status="NOT_EVALUATED",
                      error_type=type(exc).__name__, next_action="REVIEW_SOURCE_QUALITY_WITHOUT_BACKFILL_OR_OOS_RETUNING")
        _write(output / "report.json", report)
        return report
    result = walk_forward_predictions(ohlcv, start=config["start_utc"], end=config["end_exclusive_utc"],
                                      horizon_bars=config["horizon_bars"], fee_bps=config["fee_bps_per_side"],
                                      slippage_bps=config["slippage_bps_per_side"])
    result.predictions.to_csv(output / "predictions.csv", index=False)
    _write(output / "folds.json", {"folds": result.folds})
    folds = calendar_folds(config["start_utc"], config["end_exclusive_utc"])
    oos = result.predictions.loc[result.predictions.is_out_of_sample]
    frame = result.featured.loc[(result.featured.timestamp >= folds[0].test_start) &
                                (result.featured.timestamp < folds[-1].test_end)].copy()
    frame = frame.merge(oos[["timestamp", "probability"]], on="timestamp", how="left", validate="one_to_one")
    cfg = BacktestConfig(initial_capital=config["initial_capital_usdt"], risk_per_trade=config["risk_per_trade_fraction"],
                         max_daily_loss=config["max_daily_loss_fraction"], max_drawdown_halt=config["drawdown_halt_fraction"],
                         max_exposure=config["max_exposure_fraction"], fee_bps=config["fee_bps_per_side"],
                         slippage_bps=config["slippage_bps_per_side"], entry_threshold=config["entry_probability"],
                         exit_threshold=config["exit_probability"], atr_stop_multiple=config["stop_atr_multiple"],
                         max_holding_bars=config["maximum_holding_bars"])
    evaluation = run_backtest(frame, cfg)
    stress = run_backtest(frame, replace(cfg, fee_bps=cfg.fee_bps * config["stress_fee_multiplier"],
                                         slippage_bps=cfg.slippage_bps * config["stress_slippage_multiplier"]))
    is_scores = []
    for fold in folds:
        p = result.predictions.loc[(result.predictions.fold_id == fold.fold_id) & (result.predictions.phase == "train")]
        if p.empty:
            continue
        x = result.featured.loc[(result.featured.timestamp >= p.timestamp.min()) & (result.featured.timestamp <= p.timestamp.max())]
        score = run_backtest(x.merge(p[["timestamp", "probability"]], on="timestamp", how="left", validate="one_to_one"), cfg).metrics["sharpe"]
        if score is not None:
            is_scores.append(score)
    is_sharpe = float(pd.Series(is_scores).median()) if is_scores else None
    calibration = probability_diagnostics(oos.target.to_numpy(), oos.probability.to_numpy())
    fitted = sum(f["status"] == "FITTED" for f in result.folds)
    gates = gate_summary(evaluation.metrics, stress.metrics, calibration, fitted_folds=fitted,
                         expected_folds=len(folds), is_sharpe=is_sharpe, config=config)
    baselines = run_baselines(frame, cfg)
    for prefix, replay in {"oos": evaluation, "cost_stress": stress, **baselines}.items():
        for kind in ("equity", "trades", "events"):
            getattr(replay, kind).to_csv(output / f"{prefix}_{kind}.csv", index=False)
    report.update(status="HISTORICAL_CHECKS_PASSED_REQUIRES_PROSPECTIVE_REVIEW" if gates["historical_economic_checks_passed"] else "NO_MODEL_PROMOTED",
                  metrics_status="EVALUATED_HISTORICAL_ONLY", economic_metrics=evaluation.metrics,
                  cost_stress_metrics=stress.metrics, calibration=calibration, gates=gates,
                  baselines={k: v.metrics for k, v in baselines.items()},
                  fit_diagnostics={"median_train_sharpe": is_sharpe, "causal_strategy_evidence": False},
                  dataset_sha256=verified["dataset_sha256"], prediction_coverage=float(frame.probability.notna().mean()),
                  package_versions={n: importlib.metadata.version(n) for n in ("numpy", "pandas", "scikit-learn", "pyarrow")})
    _write(output / "report.json", report)
    _write(output / "artifact_manifest.json", {"files": {p.name: sha256(p.read_bytes()).hexdigest() for p in sorted(output.iterdir()) if p.is_file()}})
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "run"))
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--output", required=True)
    parser.add_argument("--history")
    args = parser.parse_args(argv)
    if args.command == "run" and not args.history:
        parser.error("run requires --history")
    report = freeze(args.config, args.output) if args.command == "freeze" else run(args.config, args.history, args.output)
    print(json.dumps({"status": report.get("status", "FROZEN"), "execution_authorized": False}, allow_nan=False))
    return 2 if report.get("status", "").startswith("BLOCKED") else 0


if __name__ == "__main__":
    raise SystemExit(main())
