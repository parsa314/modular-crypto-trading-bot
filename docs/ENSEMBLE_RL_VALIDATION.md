# Offline ensemble challenger: engineering verification

Date: 2026-10-02. Base: `876e229a8d3e73f2e9d646d4126c8ce08e4c11b4`.
This is software verification, not a scientific performance result or promotion.

Verified with Python 3.11.16, NumPy 2.4.6, Pandas 3.0.6, PyTorch 2.14.1+cpu,
Gymnasium 1.3.0 and Stable-Baselines3 2.9.0. Installed the authoritative
`.[dev,rl]` extras in an isolated environment; dependency compatibility passed.

Commands and fresh results:

```bash
python -m pytest -q tests/test_ensemble_features.py tests/test_ensemble_env.py tests/test_ensemble_agent.py tests/test_ensemble_rl.py
# 93 passed

OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 python -m pytest -q
# 250 passed, 6 failed, 1 skipped

python -m research_bot.ensemble_rl --synthetic-bars 800 --initial-train-bars 300 --test-bars 100 --folds 3 --timesteps 128 --seeds 42 --output artifacts/ensemble-final-smoke
# completed: 3 independent PPO trainings, 9 metrics rows, 101 equity marks per fold

python -m compileall -q research_bot scripts
git diff --cached --check
# passed
```

The focused suite checks confirmation-time feature causality, train-only scaling,
explicit CVaR samples, cash conservation, next-open execution, adverse slippage,
single fee charging, log-reward telescoping, post-cost exposure, drawdown stops,
terminal liquidation without PPO continuation bootstrapping, expanding folds,
matching benchmark allocation, model training/save/reload and artifact hashes.
Synthetic PPO chose cash in all three evaluation folds. This is preserved as
smoke behavior, not treated as alpha or a trained economic model.

The six broad-suite failures are unchanged historical API assertions in
`tests/test_service_v1.py` and `tests/test_v08_service.py`: they expect PAPER mode,
legacy status fields and HTTP 200 from execution endpoints, whereas the current
service exposes RESEARCH_ONLY and locks execution with HTTP 423. The same six
failures were reproduced on a fresh extraction of the unmodified base commit.
No service code, execution flags or legacy tests were changed to conceal them.
The skipped test requires a separate PostgreSQL integration environment.

The managed sandbox initially blocked the async TestClient event-loop wakeup;
the completed broad and baseline checks ran with permitted networking. They
called the in-process service, not exchange order APIs.

All four execution environment guards, canonical scientific status/dependency
contracts, workflow YAML parsing and final source/artifact digest checks passed.
The optional CPU RL workflow covers the new path separately; hosted CI results
must be read from the PR. Local results do not establish hosted CI success.

No real market performance, final sealed holdout, prospective evidence, paper
execution or live trading was evaluated. Outputs remain local under ignored
`artifacts/`; no dataset or binary weights are committed. See
[the Persian execution guide](ENSEMBLE_RL_CHALLENGER_FA.md) for assumptions and
commands for an authorized development dataset.
