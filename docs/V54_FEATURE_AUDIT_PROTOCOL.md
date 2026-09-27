# v0.54 — Feature Family Audit and Walk-Forward OOS Protocol

Status: **PREREGISTERED RESEARCH GATE / IMPLEMENTATION COMPLETE / EMPIRICAL RESULT NOT YET CLAIMED / NO EXECUTION AUTHORIZATION**

v0.54 answers one question only: **which causal feature families from v0.53 add reproducible out-of-sample value after realistic trading friction, and with what uncertainty?**

This protocol is frozen before interpreting the real CoinEx result. Negative and blocked results must be retained.

## 1. Frozen scope

Universe: `BTC/USDT`, `ETH/USDT`, `SOL/USDT`, `XRP/USDT`, `DOGE/USDT` on CoinEx public spot OHLCV.

Decision timeframe: `1h`.
Context timeframe: `4h`.
Target: next 1h close-to-close return, created only after the current 1h bar is closed and available.

Canonical decision clock:

`decision_at = decision-bar available_at`

Raw `timestamp` is bar-open provenance and must never be used as the decision clock for same-bar OHLC-derived features.

Required temporal invariants:
- `decision_at >= bar_close_at`;
- every local/HTF `*_available_at <= decision_at`;
- no duplicate decision timestamps within a symbol;
- HTF data enter only through a backward point-in-time join.

## 2. Dataset provenance and reproducibility

Every completed symbol receives a manifest containing:
- source;
- row count;
- coverage start/end;
- decision-time start/end;
- canonical frame SHA-256;
- schema SHA-256;
- complete column list;
- decision-time column;
- safety state.

The runner supports two mutually exclusive modes:

### Fresh + freeze

Fetch closed CoinEx bars, build the v0.53 point-in-time feature frame, and optionally save:
- `<SYMBOL>.csv.gz`
- `<SYMBOL>.manifest.json`

### Replay

Load the previously frozen snapshot and require the current canonical frame hash and schema hash to match the saved manifest. Hash or schema mismatch is fatal. This allows exact audit reproduction without re-querying CoinEx.

The run artifact also records the source commit when available from `GITHUB_SHA`, `SOURCE_COMMIT`, or local Git.

## 3. Feature families

1. `SMC_ICT`: causal structure, sweep, displacement, FVG/OB/breaker lifecycle, premium/discount and related observable proxies.
2. `BROOKS`: deterministic Al Brooks proxies.
3. `ICHIMOKU`: causal Tenkan/Kijun plus visible-now and projected-cloud variables without future shifts.
4. `HTF`: 4h features joined only when `4h_available_at <= decision_at`.
5. `BASE`: remaining numeric causal context, where present.

HTF columns are claimed before local-prefix grouping so `4h_ichi_*`, `4h_smc_*`, etc. cannot accidentally leak into local families.

No practitioner label is treated as institutional-intent truth or validated alpha.

## 4. Feature-health prerequisite

Feature health is computed **inside each training fold only** before model fitting. Test/future values never determine whether a feature is eligible in that fold.

Default rejection conditions:
- missing fraction > 95%;
- fewer than 60 non-null training observations;
- non-finite or near-zero variance (`<= 1e-14`).

The artifact also contains a global diagnostic health report and duplicate-like correlation warnings. The global diagnostic is descriptive only and does not control fold training eligibility.

If a variant has no healthy training features in a fold, it fails closed.

## 5. Primary model

The primary audit deliberately uses a fixed, low-complexity pipeline:

`training-only median imputation -> StandardScaler -> Ridge(alpha=5.0)`

No hyperparameter search, shuffled cross-validation, deep learning, RL, threshold mining, or post-result feature engineering is permitted inside v0.54.

Complex models are challengers for later versions only after a feature family survives this gate.

## 6. Walk-forward validation

Real-run defaults:
- minimum training rows: `1200`;
- test rows: `240`;
- step rows: `240`;
- target horizon: `1` bar;
- purge: `>= 1` bar and never below the target horizon;
- expanding chronological training window;
- no random shuffling.

Every fold records train/test boundaries, row counts, number of healthy/rejected features and cost-specific metrics.

## 7. Ablations

For every available family:
- `ONLY_<family>`;
- `DROP_<family>`;
- full `ALL` model.

Primary incremental diagnostic at base cost:

`Sharpe(ALL) - Sharpe(DROP_family)`

Family-only Sharpe is also reported. Neither number alone proves alpha.

## 8. Trading friction

Every fold and aggregate is evaluated at round-trip assumptions:
- `0 bps`;
- `24 bps` base;
- `36 bps` stress.

The cost engine splits round-trip friction into one-way cost. A direct short-to-long or long-to-short flip has turnover 2 and therefore pays the full round-trip assumption. **Every fold is forcibly liquidated at its end and the terminal exit cost is charged.**

A family that only appears useful at zero cost is not robust.

## 9. Paired uncertainty inference

For each `ALL vs DROP_family` comparison at the base 24 bps assumption, v0.54 aligns OOS returns by `(decision_at, fold)` and computes the paired net-return difference.

Inference uses a moving-block bootstrap to preserve local time-series dependence:
- default resamples: `2000`;
- default block length: `24` hourly observations;
- deterministic seed: `54054 + family_index`.

Reported fields:
- paired OOS sample count;
- mean net-return difference;
- 95% bootstrap interval;
- bootstrap probability mass at/nonpositive difference (`p_nonpositive`).

This is an uncertainty diagnostic, not a license to claim causal alpha.

## 10. Cross-symbol evidence gate

The preregistered structural `promotion_candidate=true` rule remains:
1. at least 3 symbols complete;
2. median `Sharpe(ALL)-Sharpe(DROP_family) > 0`;
3. positive Sharpe increment on at least 3 symbols.

A stronger diagnostic `paired_return_support=true` additionally requires:
- the structural promotion rule passes;
- positive paired mean net-return difference on at least 3 symbols;
- median paired net-return difference across available symbols is positive.

Neither label authorizes paper or live execution.

## 11. Required artifact bundle

A complete empirical v0.54 run consists of:
- `feature_audit.json` — complete serializable audit;
- per-symbol dataset manifests;
- optional frozen `CSV.gz` input snapshots for exact replay;
- blocked-symbol reasons;
- source commit when available;
- all `ALL`, `ONLY_*`, and `DROP_*` fold/aggregate metrics;
- feature-health diagnostics;
- paired block-bootstrap inference;
- cross-symbol evidence summary;
- safety flags.

A run with fewer than 3 completed symbols is `V54_INCOMPLETE_COVERAGE`, not a negative scientific result.

## 12. Required integrity tests

- bar-open cannot act as decision time;
- `decision_at >= bar_close_at`;
- every `*_available_at <= decision_at`;
- future HTF availability fails closed;
- chronological expanding folds;
- purge covers label horizon;
- train-only feature-health eligibility;
- no non-finite predictions;
- terminal liquidation cost is charged;
- 36 bps results cannot receive less explicit friction than 0 bps;
- frozen-input hash tamper is detected;
- schema drift is detected;
- block-bootstrap output is finite when sample support is sufficient;
- family promotion never authorizes execution;
- failed/blocked symbols are retained rather than silently omitted.

## 13. Safety state

- `PAPER_EXECUTION=false`
- `LIVE_EXECUTION=false`
- Kraken sealed
- no order submission path in v0.54
- v0.51 prospective evidence unchanged
- v0.52/v0.53 evidence not retroactively rewritten
- v0.54 empirical outcome cannot silently modify this preregistered protocol

## 14. Completion definition

### Engineering completion

v0.54 engineering is complete when code, prerequisites, tests, reproducibility/replay path, manifesting, statistical inference, promotion policy and CI gate are implemented.

### Empirical completion

v0.54 empirical completion requires a real frozen CoinEx run with at least 3 completed symbols and a reproducible artifact bundle. Until that run is actually executed, the correct status is:

`V54_ENGINEERING_COMPLETE / V54_EMPIRICAL_RESULT_PENDING`

### Interpretation boundary

A green CI run establishes implementation integrity only. A positive v0.54 OOS audit is still a research-stage result and must pass the later model tournament and forward paper validation. Negative results remain part of the thesis evidence.
