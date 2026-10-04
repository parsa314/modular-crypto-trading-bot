# LIVE execution engineering verification — 2026-10-04

Scope: the owner's explicitly requested, separately invoked spot execution CLI,
stacked on PR #101 at `151639b10276ddd627dd48f07a29499b8473fd40`.
No exchange order, authenticated exchange request, deployment or service-flag
change was performed. This record is engineering evidence, not model promotion.

Environment: Python 3.11.16; CCXT 4.5.77 from the authoritative project pin;
NumPy 2.4.6, Pandas 3.0.6, PyTorch 2.14.1+cpu, Gymnasium 1.3.0,
Stable-Baselines3 2.9.0. `uv pip check` reported all 72 installed packages
compatible. Compilation and `git diff --check` passed.

## Fresh automated checks

```bash
python -m pytest -q \
  tests/test_ensemble_features.py tests/test_ensemble_env.py \
  tests/test_ensemble_agent.py tests/test_ensemble_rl.py \
  tests/test_live_exchange.py tests/test_live_ledger.py \
  tests/test_live_runtime.py tests/test_live_policy.py
```

**318 passed**: 93 existing ensemble checks and 225 new execution checks
(97 exchange, 37 ledger, 48 runtime, 43 policy). All private-path tests intercept
requests or use fake venues; none use real API credentials.

Material coverage:

- Actual pinned CCXT request construction verifies native spot IOC parameters
  for CoinEx, Binance and OKX, and a single submission transport attempt.
- Account-wide regular and conditional-order checks, price freshness,
  precision/minimums, bounded limits, actual fill-cost provenance and fees.
- SQLite crash recovery, exclusive process locks, concurrent claims, identity
  binding, monotonic fills, rollback failure, hardlink/replaced-path rejection
  and immutable, regular candle history.
- Accepted-order timeout/crash, partial execution, missing terminal cost,
  growing cumulative fills, wrong recovery identity/amount/cost, transfers,
  stale quotes, late inference, mid-cycle stops and persistent daily-loss limits.
- Fresh-price risk rechecks, capped exits, sell-only intrabar reductions,
  CVaR outside strategy entry windows and closed peaks preserved across trims.
- A real 128-step PPO artifact verifies checksum/source compatibility and exact
  float32 live/offline observation equality for matching history/account state.
  Loading verified byte snapshots resists file changes between verification and
  model/scaler loading. Synthetic models are rejected for LIVE.

An independent agent reviewed the ledger and another reviewed the root runtime,
metadata and final bootstrap/stop-path changes. Their identified defects were
fixed with fresh regressions; final review reported no remaining material finding.

## Whole-repository result and inherited failures

Fresh `python -m pytest -q`: **475 passed, 6 failed, 1 skipped**.
The six failures are the unchanged assertions in `test_service_v1.py` and
`test_v08_service.py` expecting PAPER mode, older research-status fields or
HTTP 200 from deliberately locked endpoints. All six were reproduced with the
same environment on a fresh archive of original `main`
`876e229a8d3e73f2e9d646d4126c8ce08e4c11b4`. The PostgreSQL integration check
remained skipped. The research-service lock was preserved.

## Smoke test and limits of this evidence

The installed ensemble CLI completed one fold/seed with 350 synthetic bars and
128 PPO timesteps and wrote its model/scaler/manifest artifacts. This is only a
software smoke test; its output is forbidden in LIVE.

`python -m research_bot.live_runtime --help` succeeded. A public-only CoinEx
dry-run CLI attempt failed at public market initialization with a sanitized
`NetworkError`; it reached no private method and sent no order. End-to-end venue
connectivity, real-account permissions, actual fills and sandbox availability
are therefore **not verified** in this environment. Hosted CI success is not
claimed by this local verification record.

Live startup uses 299 public closed bars, which can differ from the full training
history origin. The training simulator's next-open fill and this executor's
post-close IOC fill also differ. These engineering tests establish implementation
contracts; they do not establish economic performance or guarantee liquidation
at a configured risk threshold. See [the operator guide](LIVE_EXECUTION_FA.md).
