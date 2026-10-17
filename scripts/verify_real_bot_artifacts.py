"""Reproduce saved experiment measurements without refitting or network calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import zipfile

import numpy as np
import pandas as pd


def _same(actual, expected):
    if actual is None or expected is None:
        assert actual is expected, (actual, expected)
    else:
        assert np.isclose(actual, expected, rtol=1e-8, atol=1e-10), (actual, expected)


def verify_ml():
    from src.ml.meta_labeler import PrimaryMetaLabeler
    from src.strategies import PrimarySignal
    from src.ml.feature_engineer import validate_bars
    from src.ml.real_bot_pipeline import execute_primary_events, score_primary_events

    report = json.loads(Path("results/ml_filtered_backtest.json").read_text())
    model = PrimaryMetaLabeler.load("models/meta_labeler_v1.json")
    assert hashlib.sha256(Path("models/meta_labeler_v1.json").read_bytes()).hexdigest() == report["model_sha256"]
    assert hashlib.sha256(Path(report["rl_input"]).read_bytes()).hexdigest() == report["approved_signals_sha256"]
    events = pd.read_parquet("data/real_bot/primary_events.parquet")
    bars = validate_bars(pd.read_parquet("data/btc_usdt_m15_2022_2024.parquet"))
    crosses = PrimarySignal.raw_entries(bars)
    assert crosses.iloc[events.bar_index].all()
    validation = events[(events.available_at >= "2024-01-01") & (events.available_at < "2025-01-01")
                        & (events.label_end <= "2025-01-01") & events.label.notna()]
    probability = model.predict_proba(validation)
    top = np.argsort(-probability, kind="stable")[:math.ceil(len(validation) / 10)]
    precision = float(validation.label.iloc[top].mean())
    _same(precision, report["precision_top_decile"])
    approved = pd.read_parquet(report["rl_input"])
    oof = pd.read_parquet("data/real_bot/train_oof.parquet").set_index("event_id").ml_probability
    training = approved[approved["split"].eq("train")]
    np.testing.assert_allclose(training.ml_probability, training.event_id.map(oof), rtol=0, atol=0)
    assert approved.ml_probability.ge(model.threshold).all()
    for _, period in approved.groupby("split"):
        assert (period.available_at.to_numpy()[1:] >= period.exit_time.to_numpy()[:-1]).all()
        assert period.mark_times.map(len).gt(0).all()
    nav = pd.read_parquet("data/real_bot/ml_validation_equity.parquet")
    for name, key in (("raw", "raw"), ("ml", "ml_filtered")):
        daily = nav[name].set_axis(nav.index - pd.Timedelta(nanoseconds=1)).resample("1D").last()
        returns = daily.pct_change(fill_method=None).dropna()
        actual = np.sqrt(365) * returns.mean() / returns.std(ddof=1)
        _same(float(actual), report[key]["sharpe"])
        _same(float(nav[name].iloc[-1] - 1), report[key]["return"])
    probability = score_primary_events(model, events)
    fresh, _, _ = execute_primary_events(events, bars, probability, model.threshold,
                                        pd.Timestamp("2024-01-01", tz="UTC"),
                                        pd.Timestamp("2025-01-01", tz="UTC"))
    for key in ("sharpe", "return", "num_trades", "max_drawdown"):
        _same(fresh[key], report["ml_filtered"][key])
    return {"phase": 2, "precision": precision, "ml_sharpe": report["ml_filtered"]["sharpe"],
            "approved_train": len(training), "native_model_and_oof_verified": True}


def verify_rl():
    from stable_baselines3 import PPO
    from src.rl.signal_env import evaluate_signal_policy

    report = json.loads(Path("results/rl_vs_fixed_sizing.json").read_text())
    path = Path(report["training"]["model_path"])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == report["training"]["model_sha256"]
    assert hashlib.sha256(Path(report["input_file"]).read_bytes()).hexdigest() == report["input_sha256"]
    model = PPO.load(path, device="cpu")
    assert model.num_timesteps == report["training"]["actual_environment_steps"] >= 100000
    data = pd.read_parquet(report["input_file"])
    validation = data[data["split"].eq("val")]
    for name, policy in (("fixed", lambda _: .02),
                         ("rl", lambda observation: model.predict(observation, deterministic=True)[0])):
        metrics, _ = evaluate_signal_policy(validation, policy, start="2024-01-01", end="2025-01-01")
        for key in ("sharpe", "return", "max_drawdown", "num_trades"):
            _same(metrics[key], report[name][key])
    return {"phase": 3, "steps": model.num_timesteps, "rl_sharpe": report["rl"]["sharpe"],
            "fixed_sharpe": report["fixed"]["sharpe"], "saved_policy_reproduced": True}


def verify_cpcv():
    from src.validation.real_bot_cpcv import CANDIDATES, summarize_paths

    report = json.loads(Path("results/final_validation.json").read_text())
    assert report["N"] == 6 and report["k"] == 2 and report["completed_refits"] == 15
    returns = pd.read_parquet(report["path_returns_file"])
    paths = np.stack([np.stack([returns[f"{name}_path_{number}"].to_numpy()
                               for name in CANDIDATES]) for number in range(1, 6)])
    reproduced = summarize_paths(paths)
    for key in ("pbo", "dsr"):
        _same(reproduced[key], report[key])
    assert reproduced["verdict"] == report["verdict"]
    steps = []
    hashes = []
    for fold in report["folds"]:
        model_path = Path(fold["training"]["model_path"])
        digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
        assert digest == fold["training"]["model_sha256"]
        hashes.append(digest)
        with zipfile.ZipFile(model_path) as checkpoint:
            data = json.loads(checkpoint.read("data"))
        assert data["num_timesteps"] == fold["training"]["actual_environment_steps"] >= 100000
        approved = pd.read_parquet(Path(fold["model_directory"]) / "approved_train.parquet")
        for segment in fold["test_segments"]:
            begin, end = pd.Timestamp(segment["start"]), pd.Timestamp(segment["end_exclusive"])
            assert not ((approved.available_at >= begin) & (approved.available_at < end)).any()
            assert not ((approved.entry_time < end) & (approved.exit_time > begin)).any()
        steps.append(data["num_timesteps"])
    assert len(set(hashes)) == 15, "Each outer fold must have its own independently fitted PPO checkpoint"
    return {"phase": 4, "refits": len(steps), "actual_ppo_steps": sum(steps),
            "pbo": reproduced["pbo"], "dsr": reproduced["dsr"], "path_statistics_reproduced": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=(2, 3, 4), type=int, required=True)
    args = parser.parse_args()
    result = {2: verify_ml, 3: verify_rl, 4: verify_cpcv}[args.phase]()
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
