# F — Course scientific evaluation protocol v1

**State: SCIENTIFIC_DECISION_DEFERRED. PAPER_OFF / LIVE_OFF.**

This is an `ENGINEERING_EXTENSION`, not an attribution to Sajad Jamalian's notebooks. The canonical repository source inspected for this implementation is `fa31e6f5ceb09f4f14ae13fef50706f2e10e1835`. The historical scientific record remains v0.50 `V50_NONOVERLAP_FAILURE_SUPPORTED`, with v0.51 prospective evidence not canonically collected. This course research does not replace v0.51's predictor, admission, cost model, exits, assets, venues, arbitration, or risk limits. No frozen status or scientific artifact is edited.

## Data and event contract

`research_bot/course/evaluation.py` consumes an explicit chronological event dataframe and separately supplied, frozen rule-based strategy direction arrays. Required columns are `event_id`, `decision_time`, `available_at`, `entry_time`, `exit_time`, `label_available_at`, `gross_return`. Timestamps must be timezone-aware. Feature availability is the latest availability of **all** used inputs and transformations, and must satisfy `available_at <= decision_time < entry_time < exit_time <= label_available_at`. The caller must provide independently verified provenance for that claim; the validator cannot recover source availability from an ordinary float feature column. Duplicate identities, nonchronological rows, nonfinite returns, and entry on the just-computed close are rejected. Feature columns cannot contain outcome, identity, or timing fields. NaN features can be imputed on training only; Infinity is rejected.

`gross_return` is the underlying simple price return across a fully closed event, expressed per unit entry notional. Supplied direction is exactly -1, 0, or +1. Net event return is `direction * gross_return - abs(direction) * roundtrip_bps / 10000`. Costs are the **total roundtrip charged once**, not each side. Flat events incur zero cost. The 0/24/36/50 bps scenarios are sensitivity assumptions, not measured Bitpin costs. Funding, borrow, latency, order-book depth, impact, fee currency, and partial-fill effects require separate simulator evidence. A short position here is a research PnL convention, not proof of exchange support or available margin.

The dataset SHA256 covers all event rows/features/outcomes. Separate hashes cover evaluation configuration, strategy position arrays, and evaluation source. Metadata records git SHA and numpy/pandas/scikit-learn versions. Baseline records conform to existing `trial_registry_v17.TrialRecord` with status `HYPOTHESIS`; the evaluator does not append to or rewrite the canonical registry. Existing first-seen evidence must supply its own immutable artifact/ledger references. No historical notebook output is promoted to prospective evidence. `frozen_holdout_start`, when configured, rejects any row whose label is available at or after that boundary. A sealed Kraken corpus must not be supplied at all, even as a purported development dataset.

## Temporal validation and preprocessing

Rule-based baselines come first. Default `baseline_admissible=false` blocks learned models. A later registered research amendment may set it true only after an independent baseline evaluation, and the runner then requires `baseline_evidence_ref` and `preregistration_ref` in provenance. These references are auditable inputs, not an automatic scientific approval mechanism.

Expanding chronological walk-forward uses fixed `min_train` and `test_size`. A training label must already be available strictly before the first test decision. Purging uses the conservative closed interval `[decision_time, label_available_at]`, excluding any train interval overlapping a test interval. Embargo extends every test interval end by the registered number of seconds. Endpoint touching is purged. Parameter zero is an explicitly unvalidated reference, not a universal correct embargo. Select the embargo and block length before outcomes according to label horizons/dependence diagnostics.

For admitted exploratory ML, `SimpleImputer(median)`, `StandardScaler`, and either regularized LogisticRegression or HistGradientBoosting are fitted only on the purged training subset. Feature list, hyperparameters, seed and probability threshold are fixed. No held-out fit, threshold search, feature selection, or test-based calibration occurs. Each fold emits train/test identities, training medians/scaler means, model fingerprint, probabilities, Brier score, train-prior Brier comparator, and fixed-bin reliability diagnostics. Reliability bins are descriptive and do not establish a calibrated model; no automatic model promotion or shadow-to-champion replacement is implemented. Learned labels are long-event net-positive labels at the reference cost, not triple-barrier TP/SL/TIMEOUT or next-direction probabilities. A triple-barrier dataset requires an additional registered labeling contract.

## CPCV, PBO, PSR and DSR applicability

The implementation enumerates all `C(N,k)` test-group combinations, performs actual interval purging/embargo, and assigns group occurrences to `phi=C(N-1,k-1)` paths. Every path contains every chronological group exactly once. **Only fixed baseline returns are assigned to these paths.** They repeat identically because those baselines do not refit. This is a tested combinatorial purge/path audit, not full trained-model CPCV evidence. Learned CPCV fitting/path construction is explicitly `BLOCKED_NOT_IMPLEMENTED_NO_PROMOTION`. Until completed under a registered experiment, any acceptance criterion requiring full learned CPCV is `INTEGRATION_BLOCKED`. CPCV itself includes future training groups and is a cross-validation research design; it must not be confused with deployable chronological forecasting.

The existing `regime_robustness.probability_backtest_overfitting` is reused as **CSCV PBO, not CPCV**. It compares in-sample winners with their out-of-sample Sharpe ranks over all 3-of-6 group selections. The wrapper requires a complete registered trial universe declaration, at least two comparable variants, at least 120 aligned regular nonoverlapping observations, and nonzero variance for every variant in every half-panel. Missing variants, constant-return variants, irregular panels or inadequate support produce `NOT_APPLICABLE`. A caller's universe declaration still needs independent verification; omitted trials cannot be detected by the array API. PBO's ranking distribution assumes comparable strategies and reasonable stability; its number is not an execution gate.

PSR uses the unannualized observed Sharpe `SR`, sample size `T`, skewness `gamma3`, Pearson kurtosis `gamma4` and zero per-period benchmark:

`PSR = Phi((SR - SR0)*sqrt(T-1)/sqrt(1-gamma3*SR+(gamma4-1)*SR^2/4))`.

It requires regular nonoverlap, at least 30 observations, positive variance and denominator, finite fourth moment, and IID/effective-sample justification. The report labels even computable values `ASSUMPTION_CONDITIONAL`; temporal independence has not been proven by a successful computation. DSR reuses the canonical `deflated_sharpe_probability` **only** when an explicit effective independent trial count >1 and positive finite across-trial per-period Sharpe standard deviation are provided. The expected-maximum Sharpe benchmark uses the Bailey–Lopez de Prado approximation with Euler constant and Normal quantiles. Unknown search history remains `NOT_APPLICABLE`/null rather than silently setting deflation to zero or calling PSR DSR. Correlated trials require justified effective count; no universal PBO/DSR cutoff is assumed.

## Uniqueness, economic metrics and comparison

Average uniqueness integrates `1 / concurrency(t)` over each half-open execution interval `[entry,exit)` and divides by its duration. This is exact duration-weighted concurrency over the supplied event panel; it is not sample-bar weighting and does not establish independence. Adjacent events have uniqueness 1. For A=[0,4h), B=[2h,6h), both have uniqueness 0.75. The supplied panel defines the concurrency universe; label-duration uniqueness or instrument-specific uniqueness require separately labeled panels and must not be conflated with this measure. Sequential bootstrap is not implemented in this package; learned resampling promotion remains deferred.

All panels report mean net event return, per-observation Sharpe/Sortino where defined, empirical worst-alpha-fraction expected shortfall, tail count/support warning, expectancy, profit factor, trade count, active event fraction, and roundtrip notional turnover (2 units per completed unit-notional trade). Return per one-way turnover is total net event return divided by two-sided one-way notional turnover. These are event statistics, not a funded multi-asset portfolio.

Compounded return and drawdown are emitted only for verified regular nonoverlapping unit-capital periods: entry spacing and each entry-to-exit duration must exactly match registered `regular_period_seconds`. Initial equity 1 is included in the running peak, so the first loss is not hidden. CAGR, annualized Sharpe and Calmar additionally require explicit `periods_per_year`. No fixed 4h crypto annualization is inferred for heterogeneous events. Overlapping/irregular panels leave portfolio return, drawdown and annualized metrics null. Depth-based capacity is always unavailable without measured executable depth and impact. CVaR with fewer than ten tail observations is explicitly low-support, not a reliable tail-risk estimate.

Synchronous candidate-minus-baseline differences are paired. The existing canonical moving-block bootstrap is reused for percentile confidence intervals; its raw-bootstrap probability mass below zero is **not** interpreted as a null-calibrated p-value. This extension centers differences under H0: mean <=0, resamples contiguous noncircular blocks, and computes a one-sided centered-null p-value. Weak stationarity and a preregistered adequate block length remain assumptions. BH correction reuses the canonical function over actually computed comparisons, conditional on independence/positive dependence. No q-value creates scientific or execution approval. The first supplied strategy is the explicit `comparison_baseline`; record ordering intentionally determines that comparator.

## Experiments and prospective requirements

The report enumerates the requested ablation registry: classical baseline, individual course rules, Grid, triangular arbitrage, imbalance, confluence, and approved regime/Ichimoku/ICT-SMC/Brooks/microstructure feature additions. Entries without generated PIT candidates and evidence remain `REQUIRES_GENERATED_PIT_POSITIONS_AND_PROVENANCE`. This is an experiment inventory, not invented completed ablation results. Grid and arbitrage require independent first-seen timestamp registration, sequence integrity, fill/depletion/latency reconstruction, failed-leg recovery, capital/inventory reservation evidence and opportunity-decay records from the event simulator. Order-book historical validity cannot be inferred from OHLCV.

Before real evaluation, freeze dataset hashes, strategy configuration hashes, train/development/frozen-holdout calendar, label definition, assumptions, costs, benchmark, family of comparisons, minimum trade/tail support, block length, multiplicity plan and context-specific acceptance margins. Decisions consider paired net-economic differences and uncertainty, risk/cost stress and genuine external prospective replication. Accuracy improvements alone, written code, synthetic tests or a single DSR/PBO number do not authorize promotion.

## Reproduction

```python
import json
from pathlib import Path
import pandas as pd
from research_bot.course.evaluation import evaluate_research
from research_bot.course.configuration import load_evaluation_config

cfg = load_evaluation_config()  # packaged, versioned parameters and governance metadata
events = pd.read_csv("registered_development_events.csv")
# Each row's direction was produced at its registered decision time.
positions = {"classical_baseline": events.pop("baseline_direction").to_numpy()}
report = evaluate_research(events, feature_columns=["registered_feature"],
    strategy_positions=positions, git_commit="VERIFIED_SOURCE_SHA", config=cfg,
    provenance={"evidence_class": "REGISTERED_DEVELOPMENT_ONLY",
                "dataset_artifact_ref": "IMMUTABLE_ARTIFACT_HASH"})
Path("evaluation_report.json").write_text(json.dumps(report, indent=2, allow_nan=False))
```

Run controlled tests with `python -m pytest -q tests/test_course_evaluation.py`. Fixtures are synthetic engineering data; they prove selected data/temporal/cost/state invariants and do not measure alpha. No notebook side-effect cell is run. No account, token, exchange, paper or live order is needed. Real-data economic results and canonical prospective evidence are not provided by these fixtures.

## Acceptance and rollback

Implemented and controlled-testable: PIT rejection, immutable event identity checks, frozen-holdout barrier, actual interval purge, chronological train-only fitting, combinatorial baseline path audit, cost accounting, duration uniqueness, conservative event metrics, conditional statistical diagnostics and canonical TrialRecord compatibility. Full trained-model CPCV, sequential bootstrap, real-data ablation/forward evidence, calibrated execution costs, effective trial-universe independence, shadow promotion and economic superiority remain unverified or unimplemented. The overall scientific decision remains `SCIENTIFIC_DECISION_DEFERRED`; a requirement for any blocked item remains `INTEGRATION_BLOCKED`. This package has no execution port. Rollback removes these additive course evaluation files/config/tests; canonical experiment evidence and execution flags remain unchanged.
