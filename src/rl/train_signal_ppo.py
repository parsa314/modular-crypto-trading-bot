"""Phase 3 PPO trained exclusively on real ML-approved trade events."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

import pandas as pd

from .signal_env import TradingEnv, evaluate_signal_policy


def train_signal_ppo(train, steps=100000, seed=42, output_path="models/ppo_agent.zip"):
    import torch
    from stable_baselines3 import PPO

    if int(steps) < 100000:
        raise ValueError("Phase 3 requires at least 100000 actual environment steps")
    if train.empty or not train["split"].eq("train").all() or not train.probability_source.eq("purged_oof").all():
        raise ValueError("PPO fitting requires nonempty training trades with purged OOF probabilities")
    torch.set_num_threads(1)
    env = TradingEnv(train)
    model = PPO("MlpPolicy", env, seed=int(seed), device="cpu", verbose=0,
                n_steps=512, batch_size=128, n_epochs=5,
                policy_kwargs={"net_arch": [32, 32]})
    started = time.monotonic()
    model.learn(total_timesteps=int(steps))
    elapsed = time.monotonic() - started
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    model.save(output_path)
    metadata = {
        "algorithm": "PPO", "seed": int(seed), "requested_steps": int(steps),
        "actual_environment_steps": int(model.num_timesteps), "training_seconds": elapsed,
        "n_steps": 512, "batch_size": 128, "n_epochs": 5, "network": [32, 32],
        "torch_threads": 1, "device": "cpu", "approved_training_events": len(train),
        "probability_source": "purged_oof", "model_path": str(output_path),
        "model_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        "period_start": str(env.full_period_start), "period_end_exclusive": str(env.full_period_end),
        "training_episodes": env.episode_count, "episode_reset": "round-robin, fresh capital per contiguous calendar segment",
        "valuation": env.valuation,
        "observation_names": list(env.observation_names),
        "reward": "trailing 30 calendar-day realized daily NAV Sharpe minus 2*max(0, drawdown-0.1)",
        "zero_variance_reward_sharpe": 0., "zero_variance_reported_sharpe": None,
    }
    return model, metadata


def run(data_path="data/real_bot/approved_signals.parquet", output_path="results/rl_vs_fixed_sizing.json",
        model_path="models/ppo_agent.zip", steps=100000, seed=42):
    data_path = Path(data_path)
    events = pd.read_parquet(data_path)
    train = events.loc[events["split"].eq("train")].reset_index(drop=True)
    val = events.loc[events["split"].eq("val")].reset_index(drop=True)
    train.attrs.update(period_start="2022-01-01", period_end="2024-01-01")
    val.attrs.update(period_start="2024-01-01", period_end="2025-01-01")
    model, training = train_signal_ppo(train, steps=steps, seed=seed, output_path=model_path)
    policy = lambda observation: model.predict(observation, deterministic=True)[0]
    fixed, fixed_daily = evaluate_signal_policy(val, lambda _: .02)
    rl, rl_daily = evaluate_signal_policy(val, policy)
    train_fixed, _ = evaluate_signal_policy(train, lambda _: .02)
    train_rl, _ = evaluate_signal_policy(train, policy)
    passed = bool(rl["sharpe_defined"] and fixed["sharpe_defined"] and rl["sharpe"] > fixed["sharpe"])
    result = {
        "phase": 3, "input_file": str(data_path),
        "input_sha256": hashlib.sha256(data_path.read_bytes()).hexdigest(),
        "event_source": "actual EMA20/50 signals approved by ML; includes all approved timeout outcomes",
        "training": training, "fixed_fraction": .02,
        "fixed": fixed, "rl": rl, "train_fixed": train_fixed, "train_rl": train_rl,
        "train_period": ["2022-01-01", "2024-01-01"],
        "validation_period": ["2024-01-01", "2025-01-01"],
        "calendar_end_exclusive": True, "daily_close_binning": "exit_time minus 1ns",
        "cost_accounting": "event net_return already includes 15bp entry and 15bp exit; no double charging",
        "acceptance_rule": "defined validation Sharpe RL > defined validation Sharpe fixed",
        "verdict": "PASS" if passed else "FAIL", "rl_active": passed,
        "selected_policy": "ppo" if passed else "fixed_fraction_2pct",
        "test_opened": False,
        "limitations": [
            "NAV uses provided intratrade closed-bar marks when available; exit-only fallback is labeled in each metric.",
            "All decision observations have flat position and zero unrealized PnL by nonoverlap construction.",
            "Action sizes capital allocation only; stops and targets are fixed by the upstream real strategy.",
            "Models and acceptance are assessed on validation; no fresh-test profitability claim.",
        ],
    }
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    daily_path = output_path.with_name(output_path.stem + "_daily.parquet")
    pd.DataFrame({"fixed_return": fixed_daily, "rl_return": rl_daily}).to_parquet(daily_path)
    result["daily_returns_file"] = str(daily_path)
    temporary = output_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    temporary.replace(output_path)
    print(json.dumps({"phase": 3, "fixed_sharpe": fixed["sharpe"], "rl_sharpe": rl["sharpe"],
                      "rl_maxdd": rl["max_drawdown"], "verdict": result["verdict"]}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/real_bot/approved_signals.parquet")
    parser.add_argument("--output", default="results/rl_vs_fixed_sizing.json")
    parser.add_argument("--model", default="models/ppo_agent.zip")
    parser.add_argument("--steps", type=int, default=100000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    run(args.data, args.output, args.model, args.steps, args.seed)


if __name__ == "__main__":
    main()
