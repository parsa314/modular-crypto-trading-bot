# V58 Validation, Test and Repository Structure Plan

**Project:** Autonomous AI/ML Cryptocurrency Trading Research System  
**Version:** V58  
**Stage:** Research Freeze  
**Execution:** PAPER=false / LIVE=false

## F. Validation plan

### F1. Temporal split contract

Primary design:

TRAIN → VALIDATION → TEST

All splits are chronological.

No random shuffle is permitted.

Default walk-forward family:
- expanding training window;
- bounded validation window;
- bounded test window;
- purge covering the full label horizon;
- embargo when event overlap or feature availability requires it.

Exact row counts/dates are dataset-dependent and may not be selected using test outcomes. They must be written into V58_DATA_MANIFEST.json and config before model fitting.

### F2. Final holdout

The final holdout:
- is identified and sealed before model selection;
- is never used to choose features, thresholds, calibrators, regimes, utility penalties or hyperparameters;
- is read only after the selected pipeline, config and Trial IDs are frozen.

Inherited Kraken holdout governance remains sealed unless a pre-outcome amendment explicitly replaces it.

### F3. Preprocessing scope

Every fold must instantiate its own preprocessing objects.

Train-only:
- imputer;
- scaler;
- encoder;
- feature-health filter;
- any learned regime estimator used as a model;
- dimensionality reduction if later authorized.

Validation-only selection:
- calibration method;
- probability/utility threshold;
- uncertainty threshold;
- shift threshold;
- safety margin;
- hyperparameter selection.

Test-only:
- final untouched scoring.

### F4. Event overlap and purge

For an event with entry at t_entry and horizon H, all training observations whose label-information interval overlaps validation/test decision information are purged.

Purging is based on label information intervals, not merely a fixed one-row gap.

CPCV may be used as robustness analysis only after the primary walk-forward implementation is correct.

### F5. Baseline tournament

B0:
strategy-only event baseline.

B1:
Logistic / Multinomial Logistic Regression.

B2:
Ridge / simple linear model where compatible with target formulation.

B3:
HistGradientBoosting.

B4:
XGBoost/LightGBM only if dependency and deterministic config are valid.

B5:
simple survival / competing-risk baseline.

All models receive identical eligible events, fold clocks, cost assumptions and holdout rules.

### F6. Event-specific versus global models

Required comparison:
- one global meta-model with strategy_arm as an input or stratification field;
- one model per strategy arm where event count is sufficient.

If an arm has insufficient sample size, report INSUFFICIENT_EVIDENCE rather than pooling it silently.

### F7. Calibration

Candidate calibrators:
- none;
- Platt;
- isotonic;
- temperature-style scaling where mathematically appropriate.

Calibrator selection is validation-only.

Report:
- Brier;
- Log Loss;
- calibration error;
- reliability curve data.

### F8. Abstention and selective prediction

Coverage is varied only through validation-selected/frozen policies.

Required reporting:
- coverage;
- abstention_rate;
- error_at_coverage;
- return_at_coverage;
- Sharpe_at_coverage;
- trade count at coverage.

A policy that improves Sharpe only by reducing coverage to an impractically tiny sample must be reported descriptively and not promoted automatically.

### F9. Expected utility

Compare at minimum:
1. strategy-only admission;
2. fixed probability threshold benchmark;
3. calibrated probability threshold benchmark;
4. V58 expected-utility policy;
5. NO_TRADE.

Utility coefficients are frozen before test scoring.

### F10. Cost stress

Required:
- 0 bps;
- 24 bps;
- 36 bps;
- 50 bps.

Every economic table includes gross and net results.

### F11. Ablation

Required variants:
- BASE;
- BASE + HTF;
- BASE + ICHIMOKU;
- BASE + ICT_SMC;
- BASE + BROOKS;
- BASE + ICHIMOKU + ICT_SMC;
- BASE + ICHIMOKU + BROOKS;
- BASE + ICT_SMC + BROOKS;
- ALL;
- DROP_ICHIMOKU;
- DROP_ICT_SMC;
- DROP_BROOKS;
- DROP_HTF.

Feature snapshots are held fixed across variants.

### F12. Regime analysis

At minimum:
- TREND_UP;
- TREND_DOWN;
- RANGE;
- HIGH_VOL;
- LOW_VOL;
- TRANSITION.

Report per-regime:
- event count;
- coverage;
- net return;
- Sharpe/Sortino where sample size permits;
- MDD;
- calibration;
- predictive metrics;
- confidence interval.

Small-sample regimes are labelled INSUFFICIENT_EVIDENCE.

### F13. Cross-asset validation

Required:
BTC, ETH, SOL, XRP, DOGE.

Results are reported:
- per asset;
- pooled;
- leave-one-asset-out where sample size supports it;
- shared portfolio.

Generalizable alpha is false when evidence is isolated to one asset unless a separate asset-specific hypothesis was pre-registered.

### F14. Statistical validation

Required:
- moving-block bootstrap;
- paired block bootstrap;
- confidence intervals;
- effect sizes;
- PSR;
- DSR;
- PBO / multiple-testing awareness.

When assumptions fail:
NOT_APPLICABLE with reason.

The Trial Registry records the full search space so DSR/PBO interpretation is not based on a hidden subset of trials.

### F15. Promotion sequence

1. data/PIT integrity;
2. event correctness;
3. label correctness;
4. baseline predictive validity;
5. calibration;
6. selective/utility policy;
7. cost survival;
8. risk survival;
9. shared portfolio survival;
10. multi-fold stability;
11. multi-asset support;
12. statistical audit;
13. final holdout;
14. scientific promotion decision.

Skipping a gate is prohibited.

---

## G. Test plan

### G1. Mandatory tests from the V58 contract

test_no_lookahead  
Purpose: mutate data strictly after a decision timestamp and prove all prior features/events/regimes remain identical.

test_train_only_scaling  
Purpose: prove imputer/scaler fit parameters are derived only from training data.

test_event_timestamp  
Purpose: event timestamp, available_at and feature snapshot clocks are monotonic and legal.

test_next_bar_execution  
Purpose: entry occurs no earlier than next executable bar open.

test_fee_accounting  
Purpose: entry/exit/flip/terminal costs are charged exactly once according to contract.

test_stop_target_ambiguity  
Purpose: same-bar TP+SL resolves STOP_FIRST without valid intrabar data.

test_drawdown_kill_switch  
Purpose: 5% V58 drawdown threshold vetoes new risk.

test_duplicate_event  
Purpose: deterministic event identity prevents accidental duplicate event insertion.

test_duplicate_order  
Purpose: execution simulator rejects duplicate order identity.

test_portfolio_cash_constraint  
Purpose: shared portfolio cannot spend unavailable cash or exceed frozen gross exposure.

test_feature_causality  
Purpose: future mutations do not alter past feature values.

test_pit_availability  
Purpose: available_at > decision timestamp is rejected.

test_abstention  
Purpose: uncertainty/regime/shift/utility failures produce NO_TRADE with reason.

test_cost_gate  
Purpose: expected utility and promotion metrics use the frozen cost scenario.

test_replay_determinism  
Purpose: same frozen data/config/code reproduces identical event IDs and deterministic model outputs where applicable.

test_hash_reproducibility  
Purpose: data/config/feature/model artifacts are hash-bound and tamper-detectable.

### G2. Additional V58 tests

test_validation_only_calibration  
Calibrator cannot fit on test rows.

test_final_holdout_not_used_for_selection  
Selection code must fail if a holdout-tagged frame is passed to tuning.

test_regime_causality  
Future price changes cannot alter already-issued regime labels/confidence.

test_event_id_determinism  
Same immutable event fields generate same ID; one changed identity field changes ID.

test_target_fields_immutable_after_detection  
Stop/target/horizon cannot be changed after outcome inspection.

test_arm_independence  
ARM_E generation cannot erase or rewrite ARM_A-D events.

test_expected_utility_components  
EU equals the configured decomposition and includes costs/penalties.

test_uncertainty_gate  
High uncertainty forces NO_TRADE at frozen threshold.

test_shift_gate  
Excessive shift score forces NO_TRADE.

test_risk_veto_cannot_be_bypassed  
Model approval cannot override RiskEngine rejection.

test_risk_reducing_exit_allowed  
Risk engine does not block a valid risk-reducing exit merely because entry limits are breached.

test_gross_exposure_70pct  
Shared portfolio rejects admissions above 70% gross exposure.

test_asset_weight_35pct  
Shared portfolio rejects admissions above 35% asset weight.

test_risk_per_trade_025pct  
Position-sizing path enforces <=0.25% equity risk.

test_cost_grid_complete  
0/24/36/50 bps scenarios are present.

test_no_current_value_historical_backfill  
PIT external features cannot use current values for past timestamps.

test_trial_registry_uniqueness  
Trial IDs are unique and linked to immutable config hashes.

test_live_execution_hard_false  
Any LIVE flag or live-order path raises a hard failure.

test_paper_execution_hard_false  
PAPER remains disabled during V58 baseline research stage.

### G3. Test gates

Gate T0:
schema validation.

Gate T1:
causality / PIT / event-time tests.

Gate T2:
execution / cost / risk tests.

Gate T3:
replay / hashing / registry tests.

Gate T4:
model split / calibration / holdout tests.

No empirical model training is authorized until T0-T4 pass.

---

## H. Directory and file structure

Proposed V58 structure:

```text
.
├── V58_FROZEN_RESEARCH_PROTOCOL.md
├── V58_DATA_MANIFEST.json
├── V58_FEATURE_SCHEMA.json
├── V58_EVENT_SCHEMA.json
├── V58_MODEL_REGISTRY.json
├── V58_TRIAL_REGISTRY.csv
├── configs/
│   └── v58/
│       ├── research.yaml
│       ├── events.yaml
│       ├── costs.yaml
│       ├── risk.yaml
│       ├── validation.yaml
│       └── models.yaml
├── docs/
│   └── v58/
│       ├── V58_STAGE1_AUDIT_AND_GAP_ANALYSIS.md
│       ├── V58_VALIDATION_TEST_AND_STRUCTURE_PLAN.md
│       ├── V58_AMENDMENTS.md
│       ├── V58_STATISTICAL_AUDIT.md
│       ├── V58_RESEARCH_RESULT_FA.md
│       └── V58_RESEARCH_RESULT_EN.md
├── research_bot/
│   └── v58/
│       ├── __init__.py
│       ├── contracts.py
│       ├── events.py
│       ├── targets.py
│       ├── regimes.py
│       ├── calibration.py
│       ├── uncertainty.py
│       ├── utility.py
│       ├── validation.py
│       ├── statistics.py
│       ├── ledger.py
│       ├── portfolio.py
│       ├── execution_sim.py
│       ├── model_registry.py
│       ├── trial_registry.py
│       ├── strategies/
│       │   ├── arm_a_s6.py
│       │   ├── arm_b_ichimoku.py
│       │   ├── arm_c_ict_smc.py
│       │   ├── arm_d_brooks.py
│       │   └── arm_e_confluence.py
│       └── features/
│           ├── base.py
│           ├── htf.py
│           ├── ichimoku.py
│           ├── ict_smc.py
│           └── brooks.py
├── scripts/
│   └── v58/
│       ├── build_data_manifest.py
│       ├── freeze_events.py
│       ├── run_baseline_tournament.py
│       ├── run_ablations.py
│       ├── run_cost_stress.py
│       ├── run_portfolio.py
│       └── build_final_report.py
├── tests/
│   └── v58/
│       ├── test_causality.py
│       ├── test_events.py
│       ├── test_targets.py
│       ├── test_execution.py
│       ├── test_costs.py
│       ├── test_risk.py
│       ├── test_portfolio.py
│       ├── test_validation.py
│       ├── test_calibration.py
│       ├── test_abstention.py
│       ├── test_pit.py
│       └── test_reproducibility.py
└── artifacts/
    └── v58/
        ├── manifests/
        ├── trials/
        ├── predictions/
        ├── ledgers/
        └── reports/
```

### Structure rules

- Historical v0.54/v0.57 evidence is not overwritten.
- V58 modules live in a versioned namespace until promotion.
- Generated artifacts are separated from source.
- Config is authoritative and hashable.
- Trial Registry is append-only.
- Amendments are append-only.
- No secrets or exchange credentials are stored in V58 artifacts.
- No withdrawal or transfer code is introduced.
- PAPER/LIVE endpoints are not part of V58 baseline implementation.

## Implementation authorization from this plan

Allowed after stage approval:
- create the V58 namespace and configs;
- implement data/event/feature/validation/test scaffolding;
- build manifest and ledger tooling;
- run unit tests.

Still blocked until data/target gates pass:
- model training;
- empirical tournament;
- final backtest;
- PAPER execution;
- LIVE execution.
