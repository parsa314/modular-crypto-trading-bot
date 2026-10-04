# MASTER v3 Stage 0 — engineering verification

Date: 2026-10-04 UTC. Scope: Stage 0 only, on the isolated
`feature/master-v3-stage0-20261004` worktree based on
`6d9a222c2f2dffb765e5e010b311275343f7ccc2` (the separate operator CLI change).
No economic result or promotion decision is produced by these checks.

## Fresh local evidence

Environment: Python 3.11.16 on Linux; CCXT 4.5.77, NumPy 2.4.6, Pandas 3.0.6,
Torch 2.14.1 CPU, Stable-Baselines3 2.9.0 and Gymnasium 1.3.0.
The optional RL stack exercises existing tests; no new model selection or market
experiment was performed. `uv pip check` checked 72 packages with no incompatibility.

```bash
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 python -m pytest -q
```

Result: **529 passed, 1 skipped, 3 warnings; exit 0** in 47.63 seconds.
The PostgreSQL integration test is skipped because `TEST_DATABASE_URL` is not
configured; PostgreSQL settlement is not freshly verified here. Existing warnings
concern the FastAPI test client's httpx transition and Gym Box infinite bounds.

The foundation/ledger/runtime/policy focused run preceding the final compatibility
test was **174 passed**. The final full suite includes all 48 foundation cases.
After an interrupted-initialization recovery fix, the independent reviewer ran
the foundation and preserved ledger checks: **85 passed** in 1.28 seconds.

```bash
python -S -m research_bot.execution.foundation \
  --config configs/stage0.example.json \
  --ledger artifacts/stage0/ledger.sqlite \
  --report artifacts/stage0/report.json
```

Initial execution and identical restart: **exit 0**. Report:

```json
{
  "stage": 0,
  "status": "AWAITING_USER_REVIEW",
  "mode": "BACKTEST",
  "config_valid": true,
  "ledger_initialized": true,
  "execution_authorized": false,
  "metrics_status": "NOT_EVALUATED",
  "python_version": "3.11.16",
  "config_sha256": "96c65fd3d611b49c752e93aa5234b45cfd1e62777f07ea5353ece518fe7abfdb"
}
```

The full report additionally records source hashes, unresolved capital/venue and
the initialization timestamp. Stage 0 awaits review and stages 1–14 remain pending.
SQLite and JSON outputs stay in ignored local `artifacts/`; only public validation
evidence is committed. The initialized ledger is not an execution account journal.

## What the checks establish

- L0 ordinary mutation is blocked; snapshots are detached; mode/limit/finite/JSON
  validation and the 5% MICRO capital boundary are exercised.
- Credential fields, duplicate keys and unknown config fields fail closed;
  structured events omit raw signed-URL exceptions and arbitrary log extras.
- Execution foundation imports under `python -S` do not load research, legacy
  research contracts, CCXT, Pandas, NumPy or ML packages.
- Source/config identity is pinned; restart is idempotent and repairs an audit
  event interrupted after state persistence; forged stage approval
  is rejected; report paths cannot overwrite config, ledger or its WAL/SHM files.
- Legacy execution exports and LiveConfig/LiveLedger import identities are
  preserved. The ledger implementation is unchanged; the old simulator has only
  its relative contracts import adjusted after the move into the package.
- The six stale service assertions now verify the current research firewall,
  sealed holdout and canonical negative result. Service implementation is unchanged.
- `live` and `testnet` entrypoints fail before reading missing input files, which
  verifies that they cannot proceed to model or exchange initialization in Stage 0.

## Limits of this evidence

No exchange login, order submission, deployment, PAPER process or live process was
started. Venue connectivity, 2FA resume, strategy alpha, OOS metrics, live-feed
quality, the 30/60-day gates and production uptime remain unverified. Existing
legacy model-serving imports are not fully migrated by this Stage 0 change.
The new workflow is committed for future CI; local results do not establish that
GitHub-hosted runners are available. User constraints and Stage 1 go-ahead remain
outstanding.
