"""Independent, offline PPO challenger; no exchange or promotion integration."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
from importlib.metadata import version
import json
import platform
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from .ensemble_features import extract_all_features
from .reproducibility import dataframe_fingerprint


@dataclass(frozen=True)
class WalkForwardConfig:
    initial_train_bars: int = 600
    test_bars: int = 200
    folds: int = 3
    embargo_bars: int = 1

    def __post_init__(self):
        for name, minimum in (("initial_train_bars", 2), ("test_bars", 2),
                              ("folds", 1), ("embargo_bars", 0)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")


def walk_forward_splits(n_rows: int, config: WalkForwardConfig):
    """Expanding train prefixes and disjoint test intervals; no random splitting."""
    required = config.initial_train_bars + config.folds * (config.embargo_bars + config.test_bars)
    if n_rows < required:
        raise ValueError(f"need {required} usable bars after feature warmup, received {n_rows}")
    for fold in range(config.folds):
        train_end = config.initial_train_bars + fold * (config.embargo_bars + config.test_bars)
        test_start = train_end + config.embargo_bars
        yield fold, train_end, test_start, test_start + config.test_bars


class TrainOnlyScaler:
    """Frozen per-fold feature scaling. Raw OHLCV is never transformed."""

    def __init__(self, columns):
        self.columns = list(columns)

    def fit(self, frame):
        values = frame[self.columns].to_numpy(dtype=float)
        if not len(values) or not np.isfinite(values).all():
            raise ValueError("scaler training features must be nonempty and finite")
        self.mean = values.mean(axis=0)
        self.scale = values.std(axis=0)
        self.scale[self.scale < 1e-12] = 1.0
        return self

    def transform(self, frame):
        if not hasattr(self, "mean"):
            raise RuntimeError("fit scaler on training data first")
        result = frame.copy()
        values = (frame[self.columns].to_numpy(dtype=float) - self.mean) / self.scale
        if not np.isfinite(values).all():
            raise ValueError("scaled features must be finite")
        result[self.columns] = values
        return result

    def to_dict(self):
        return {"columns": self.columns, "mean": self.mean.tolist(), "scale": self.scale.tolist()}


def synthetic_ohlcv(n: int = 1500, seed: int = 42):
    """Positive internally valid candles for software smoke tests only."""
    if n < 2:
        raise ValueError("at least two synthetic bars required")
    rng = np.random.default_rng(seed)
    previous = 100.0
    rows = []
    for _ in range(n):
        open_price = previous * np.exp(rng.normal(0, .001))
        close = open_price * np.exp(rng.normal(.0001, .008))
        high = max(open_price, close) * (1 + rng.uniform(.001, .005))
        low = min(open_price, close) * (1 - rng.uniform(.001, .005))
        rows.append((open_price, high, low, close, rng.lognormal(7, .4)))
        previous = close
    result = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"])
    result.insert(0, "timestamp", pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC"))
    return result


def equity_metrics(equity, *, periods_per_year: float):
    """Include initial capital in running peaks and compute actual net returns."""
    from .ensemble_env import empirical_cvar
    values = np.asarray(equity, dtype=float)
    if values.ndim != 1 or len(values) < 3 or not np.isfinite(values).all() or np.any(values <= 0):
        raise ValueError("equity must contain initial capital and >=2 positive finite marks")
    if not np.isfinite(periods_per_year) or periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive and finite")
    returns = values[1:] / values[:-1] - 1
    std = float(returns.std(ddof=1))
    return {
        "bars": len(returns), "initial_equity": float(values[0]),
        "final_equity": float(values[-1]), "net_return": float(values[-1] / values[0] - 1),
        "max_drawdown": float(np.max(1 - values / np.maximum.accumulate(values))),
        "annualized_sharpe": float(returns.mean() / std * np.sqrt(periods_per_year)) if std > 1e-12 else None,
        "cvar_95": max(0.0, empirical_cvar(-returns, alpha=.95)),
    }


def buy_and_hold_equity(test, config):
    """Same initial post-cost exposure as PPO; static units and terminal sale."""
    slip = config.slippage_bps / 10000
    entry = float(test.iloc[0]["open"])
    cash_per_unit = entry * (1 + slip) * (1 + config.transaction_cost)
    weight = config.max_asset_weight
    quantity = weight * config.initial_balance / (entry + weight * (cash_per_unit - entry))
    cash = config.initial_balance - quantity * cash_per_unit
    values = [config.initial_balance] + (cash + quantity * test["close"].to_numpy()).tolist()
    values[-1] = cash + quantity * float(test.iloc[-1]["close"]) * (1 - slip) * (1 - config.transaction_cost)
    return values


def evaluate_agent(agent, frame, feature_cols, config, *, start_index):
    """Continue risk-stopped episodes in cash to the common test end."""
    from .ensemble_env import EnsembleTradingEnv
    env = EnsembleTradingEnv(frame, feature_cols, config, start_index=start_index)
    obs, _ = env.reset()
    equity = [config.initial_balance]
    actions, fees, risk_stop = [], 0.0, False
    for _ in range(len(frame) - start_index - 1):
        action, _ = agent.predict(obs, deterministic=True)
        obs, _, terminated, truncated, info = env.step(action)
        equity.append(float(info["equity"]))
        fees += float(info["fees"])
        actions.append(int(np.asarray(action).item()))
        if terminated or truncated:
            risk_stop = bool(info["risk_stop"])
            break
    full_length = len(frame) - start_index
    equity.extend([equity[-1]] * (full_length - len(equity)))
    return equity, {"fees": fees, "risk_stop": risk_stop, "action_counts": {
        str(i): actions.count(i) for i in range(3)}}


def _source_record():
    root = Path(__file__).resolve().parents[1]
    files = [Path(__file__), root / "pyproject.toml", root / "research_bot/ensemble_features.py",
             root / "research_bot/ensemble_env.py", root / "research_bot/ensemble_agent.py",
             root / "research_bot/reproducibility.py"]
    hashes = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True))
    except (OSError, subprocess.CalledProcessError):
        sha, dirty = None, None
    return {"git_head": sha, "dirty": dirty, "file_sha256": hashes}


def run_experiment(frame, output, *, data_source, synthetic=False, walk_forward=None,
                   env_config=None, timesteps=50000, seeds=(42, 43, 44), torch_threads=1):
    """Fixed PPO configuration across folds/seeds; no test-based model selection."""
    from .ensemble_agent import create_agent
    from .ensemble_env import EnsembleTradingEnv, TradingEnvConfig
    import torch
    if isinstance(timesteps, bool) or not isinstance(timesteps, int) or timesteps < 128:
        raise ValueError("timesteps must be an integer >=128")
    if not seeds or len(set(seeds)) != len(seeds) or any(isinstance(s, bool) or not isinstance(s, int) or s < 0 for s in seeds):
        raise ValueError("seeds must be distinct nonnegative integers")
    if not isinstance(data_source, str) or not data_source.strip():
        raise ValueError("describe the dataset source")
    if isinstance(torch_threads, bool) or not isinstance(torch_threads, int) or torch_threads < 1:
        raise ValueError("torch_threads must be a positive integer")
    wf, cfg = walk_forward or WalkForwardConfig(), env_config or TradingEnvConfig()
    featured, cols = extract_all_features(frame)
    mask = featured[cols].notna().all(axis=1)
    if not mask.any():
        raise ValueError("no complete feature rows after warmup")
    first = int(np.flatnonzero(mask.to_numpy())[0])
    if not mask.iloc[first:].all():
        raise ValueError("missing features after warmup; do not compress the time axis")
    usable = featured.iloc[first:].reset_index(drop=True)
    splits = list(walk_forward_splits(len(usable), wf))
    context = max(cfg.lookback, cfg.cvar_window + 1)
    if wf.initial_train_bars < context:
        raise ValueError("initial training window must cover lookback and CVaR history")
    delta = pd.to_datetime(usable.timestamp.iloc[1]) - pd.to_datetime(usable.timestamp.iloc[0])
    periods = 365.25 * 86400 / delta.total_seconds()
    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {
        "status": "STARTED", "evidence_state": "ENGINEERING_SMOKE" if synthetic else "UNVALIDATED_CHALLENGER",
        "scientific_decision": "NOT_EVALUATED", "paper_execution": False, "live_execution": False,
        "data_source": data_source, "synthetic": synthetic, "dataset_sha256": dataframe_fingerprint(featured[["timestamp", "open", "high", "low", "close", "volume"]]),
        "input_bars": len(frame), "warmup_bars": first, "usable_bars": len(usable),
        "bar_seconds": delta.total_seconds(), "periods_per_year": periods,
        "feature_columns": cols, "walk_forward": asdict(wf), "environment": asdict(cfg),
        "requested_timesteps": timesteps, "seeds": list(seeds), "source": _source_record(),
        "python_version": platform.python_version(), "torch_threads": torch_threads,
        "dependencies": {p: version(p) for p in ("numpy", "pandas", "torch", "gymnasium", "stable-baselines3")},
        "protocol": "fixed PPO+LSTM; train-only scaling; embargo; next-open fills; no model selection; no promotion",
        "folds": [],
    }
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")
    rows = []
    old_threads = torch.get_num_threads()
    torch.set_num_threads(torch_threads)
    try:
        for fold, train_end, test_start, test_end in splits:
            train = usable.iloc[:train_end].copy()
            scaler = TrainOnlyScaler(cols).fit(train)
            train_scaled = scaler.transform(train)
            test_context = scaler.transform(usable.iloc[test_start-context:test_end].copy())
            test = usable.iloc[test_start:test_end]
            folder = destination / f"fold_{fold}"
            folder.mkdir()
            (folder / "scaler.json").write_text(json.dumps(scaler.to_dict(), indent=2, allow_nan=False), encoding="utf-8")
            manifest["folds"].append({"fold": fold, "train_bars": train_end,
                "train_start": train.timestamp.iloc[0].isoformat(), "train_end": train.timestamp.iloc[-1].isoformat(),
                "test_start": test.timestamp.iloc[0].isoformat(), "test_end": test.timestamp.iloc[-1].isoformat(),
                "context_bars": context, "trained_timesteps": {}})
            curves = {"timestamp": [usable.timestamp.iloc[test_start-1]] + test.timestamp.tolist()}
            for name, equity in (("cash", [cfg.initial_balance] * (len(test) + 1)),
                                 ("buy_and_hold", buy_and_hold_equity(test, cfg))):
                curves[name] = equity
                rows.append({"fold": fold, "seed": None, "model": name, **equity_metrics(equity, periods_per_year=periods)})
            for seed in seeds:
                env = EnsembleTradingEnv(train_scaled, cols, cfg)
                agent = create_agent(env, seed=seed)
                agent.learn(total_timesteps=timesteps)
                equity, details = evaluate_agent(agent, test_context, cols, cfg, start_index=context-1)
                agent.save(folder / f"ppo_seed_{seed}")
                env.close()
                manifest["folds"][-1]["trained_timesteps"][str(seed)] = int(agent.num_timesteps)
                curves[f"ppo_seed_{seed}"] = equity
                rows.append({"fold": fold, "seed": seed, "model": "ppo_lstm", **details,
                             **equity_metrics(equity, periods_per_year=periods)})
            pd.DataFrame(curves).to_csv(folder / "equity.csv", index=False)
        pd.DataFrame(rows).to_csv(destination / "metrics.csv", index=False)
        manifest["status"] = "COMPLETED"
        manifest["artifacts_sha256"] = {str(p.relative_to(destination)): hashlib.sha256(p.read_bytes()).hexdigest()
                                        for p in sorted(destination.rglob("*")) if p.is_file() and p != manifest_path}
    except Exception as exc:
        manifest["status"] = "FAILED"
        manifest["failure_type"] = type(exc).__name__
        raise
    finally:
        torch.set_num_threads(old_threads)
        manifest_path.write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")
    return pd.DataFrame(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv", type=Path, help="one regular closed-bar OHLCV series")
    source.add_argument("--synthetic-bars", type=int, help="engineering smoke data only")
    parser.add_argument("--data-source", help="venue/symbol/timeframe/provenance for CSV input")
    parser.add_argument("--output", type=Path, required=True, help="new artifact directory; existing output is refused")
    parser.add_argument("--initial-train-bars", type=int, default=600)
    parser.add_argument("--test-bars", type=int, default=200)
    parser.add_argument("--folds", type=int, default=3)
    parser.add_argument("--embargo-bars", type=int, default=1)
    parser.add_argument("--timesteps", type=int, default=50000)
    parser.add_argument("--seeds", default="42,43,44")
    parser.add_argument("--lookback", type=int, default=30)
    parser.add_argument("--fee-bps", type=float, default=10)
    parser.add_argument("--slippage-bps", type=float, default=5)
    parser.add_argument("--max-exposure", type=float, default=.35)
    parser.add_argument("--max-drawdown", type=float, default=.12)
    parser.add_argument("--max-cvar", type=float, default=.035)
    parser.add_argument("--torch-threads", type=int, default=1)
    args = parser.parse_args(argv)
    if args.csv and not args.data_source:
        parser.error("--data-source is required for CSV input")
    from .ensemble_env import TradingEnvConfig
    try:
        frame = pd.read_csv(args.csv) if args.csv else synthetic_ohlcv(args.synthetic_bars)
        cfg = TradingEnvConfig(lookback=args.lookback, transaction_cost=args.fee_bps/10000,
            slippage_bps=args.slippage_bps, max_asset_weight=args.max_exposure,
            max_drawdown=args.max_drawdown, max_cvar=args.max_cvar)
        result = run_experiment(frame, args.output, data_source=args.data_source or "synthetic seed=42; no market evidence",
            synthetic=args.csv is None, walk_forward=WalkForwardConfig(args.initial_train_bars, args.test_bars, args.folds, args.embargo_bars),
            env_config=cfg, timesteps=args.timesteps, seeds=tuple(int(s) for s in args.seeds.split(",")), torch_threads=args.torch_threads)
    except (ValueError, FileExistsError) as exc:
        parser.error(str(exc))
    print(result[["fold", "seed", "model", "net_return", "max_drawdown"]].to_string(index=False))
    print(f"Artifacts: {args.output}; scientific decision NOT_EVALUATED; PAPER/LIVE false")


if __name__ == "__main__":
    main()
