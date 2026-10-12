# V58 FROZEN RESEARCH PROTOCOL

**Project:** Autonomous AI/ML Cryptocurrency Trading Research System  
**Version:** V58  
**Freeze date:** 2026-09-25  
**Branch:** research/v58-research-freeze  
**Repository:** parsa314/modular-crypto-trading-bot  
**Mode:** STRICT SCIENTIFIC RESEARCH  
**INSTITUTIONAL_RESEARCH:** true  
**PAPER_EXECUTION:** false  
**LIVE_EXECUTION:** false

## 1. Freeze status

This document freezes the V58 scientific design before implementation or model training.

The design is frozen at the level of:
- research question and hypotheses;
- event architecture;
- target semantics;
- feature-family definitions;
- validation logic;
- cost and risk constraints;
- model tournament;
- calibration and abstention policy;
- statistical testing;
- promotion/rejection criteria;
- evidence and reproducibility requirements.

No V58 model has been trained and no V58 economic result exists at this freeze.

Exact dataset file hashes are not yet frozen. V58 empirical execution is therefore blocked until V58_DATA_MANIFEST.json is created from admissible point-in-time datasets and the manifest is locked before outcome inspection.

Any change after this freeze requires a dated amendment that states:
1. what changed;
2. why it changed;
3. whether any V58 validation/test/economic outcome had already been inspected;
4. affected hashes and Trial IDs.

No silent protocol edits are allowed.

## 2. Non-negotiable scientific contract

1. Evidence Before Opinion.
2. No result may be fabricated, interpolated from memory, or inferred from missing artifacts.
3. Future information may not enter a historical decision row.
4. Imputation, scaling, encoding, feature selection, calibration and threshold/utility tuning are fitted only on permitted training/validation data.
5. Final holdout data may not tune a threshold, hyperparameter, feature set, regime rule, cost rule, stop/target rule or uncertainty cutoff.
6. NO_TRADE is a valid benchmark and decision.
7. Predictive accuracy is not economic profitability.
8. All economic results are reported after explicit costs.
9. Negative evidence is retained.
10. Ichimoku, ICT/SMC and Al Brooks are falsifiable hypothesis families, not assumed alpha.
11. Simple baselines precede deep learning and reinforcement learning.
12. LIVE_EXECUTION remains false.
13. PAPER_EXECUTION remains false until a separate scientific promotion decision.
14. No exchange withdrawal/transfer capability is permitted.
15. Every experiment has an immutable Trial ID.
16. Source, data, config and code hashes are recorded.
17. Final empirical protocol is frozen before main outcome inspection.
18. Backtests are historical evidence, never proof of future profitability.
19. Every model and policy is compared with a relevant baseline and NO_TRADE.
20. Missing point-in-time data are recorded as UNAVAILABLE_PIT_DATA, never backfilled using present-day values.

## 3. Audited starting state

Repository-level V58 work starts from the current canonical repository, not from an assumed V57 branch.

Observed repository facts at freeze:
- canonical repository: parsa314/modular-crypto-trading-bot;
- current default branch: main;
- current repository execution policy: RESEARCH_ONLY / PAPER_OFF / LIVE_OFF;
- the current main lineage contains newer v0.54 data/evidence automation commits;
- no branches named v55, v56 or v57 were found;
- a frozen v0.54 scientific source exists at f234a85028ed2d86cd3bc672e7491f72b0245b9b;
- v0.54 implements causal availability checks, train-only preprocessing, expanding walk-forward, feature-family ablation, moving-block bootstrap support, dataset fingerprints and frozen replay;
- v0.54 does not implement the V58 event/meta/utility architecture.

The V57 description supplied for this research transition is treated as an off-repository research-state report unless corresponding immutable artifacts are later imported and hashed. It is not silently promoted to canonical GitHub evidence.

## 4. Main research question

Do Ichimoku, ICT/SMC and Al Brooks feature families provide incremental predictive and economic information beyond generic price-action/base/HTF information, and can an event-conditional meta-model select economically useful setups while abstaining when evidence, regime confidence or expected utility is insufficient?

Primary economic question:
- Does a V58 meta-policy improve net, risk-adjusted, cost-aware outcomes relative to the underlying strategy arm and NO_TRADE under strictly temporal out-of-sample validation?

Primary information question:
- Does adding each technical family materially improve predictions or economic outcomes after controlling for correlated base price-action features?

## 5. Hypotheses

H1. Feature families do not have equal incremental value.

H2. Incremental value is regime-dependent.

H3. Event-specific models may be more stable than a single global model.

H4. A cost-aware expected-utility policy may dominate a fixed probability threshold when evaluated out of sample.

H5. Uncertainty-aware abstention may reduce weak or unstable trade admissions.

H6. Complex models may only be promoted after beating simple baselines under the same data and validation budget.

H7. More confirmations do not necessarily imply more alpha.

All hypotheses are two-sided/falsifiable in interpretation. Failure to support a hypothesis is retained as evidence.

## 6. V58 architecture

PIT DATA
→ EVENT GENERATOR
→ CONTINUOUS FEATURE ENGINE
→ REGIME ENGINE
→ META MODEL
→ CALIBRATION / UNCERTAINTY
→ EXPECTED UTILITY
→ RISK ENGINE
→ PORTFOLIO ENGINE
→ EXECUTION SIMULATOR
→ EVIDENCE LEDGER

Hard separations:
- strategy generation cannot inspect model outcomes;
- model code cannot bypass risk;
- risk cannot rewrite strategy history;
- execution simulation cannot change event labels;
- evidence ledger records every rejection and abstention.

## 7. Strategy arms

ARM_A — S6 / trend breakout.

ARM_B — Ichimoku trend, pullback and rejection hypotheses.

ARM_C — ICT/SMC event chain:
liquidity sweep → displacement → MSS/CISD → FVG/OB retracement.

ARM_D — Al Brooks-inspired event family:
trend, trading range, breakout, failed breakout, signal-bar quality and follow-through.

ARM_E — four-framework confluence.

Each arm emits independent immutable events. A combined arm must not overwrite or deduplicate the component-arm events. Duplicate detection is identity-based and does not merge scientifically distinct arm observations.

## 8. Event identity and lifecycle

Every event receives a deterministic unique_event_id derived from immutable pre-outcome fields:
- symbol;
- venue;
- strategy_arm;
- direction;
- setup_timestamp;
- entry_reference;
- feature_snapshot_id;
- strategy_version.

Event lifecycle:
DETECTED → SNAPSHOT_FROZEN → ENTRY_ELIGIBILITY_EVALUATED → TARGET_LABELLED → MODEL_SCORED → UTILITY_EVALUATED → RISK_EVALUATED → PORTFOLIO_EVALUATED → SIMULATED_OR_ABSTAINED → LEDGER_FINALIZED.

No stage may alter earlier event fields except through an explicit correction record with provenance.

## 9. Entry and execution-time semantics

Decision features use only closed and available information at the event decision timestamp.

Default V58 execution semantics:
- signal/event is formed on a closed decision bar;
- simulated entry is not earlier than the next executable bar open;
- if the next bar is unavailable or invalid, the event is not silently shifted to a later favorable price;
- fees, spread and slippage are charged by the execution simulator;
- final open positions are force-closed for accounting only when the frozen evaluation design requires terminal liquidation, and that closing cost is charged.

Current spot-oriented research lineage remains non-live. Any short-direction event that is not executable under the frozen instrument contract must be retained as a research observation but cannot be treated as an executable spot trade.

## 10. Event target

Primary target:
Y ∈ {TP, SL, TIMEOUT}

Required auxiliary target:
time_to_event

Target labels are created only after event fields, entry reference, stop, target and holding horizon have been frozen.

If TP and SL are both touched inside the same OHLC bar and no valid intrabar sequence is available:
- STOP_FIRST is mandatory.

No optimistic intrabar assumption is permitted.

The exact numerical stop/target construction and maximum holding horizon must be locked in config and V58_EVENT_SCHEMA.json before any model training. If the inherited strategy arm does not provide a deterministic stop/target/horizon without future data, that arm is BLOCKED_FROM_V58_TRAINING until the definition is pre-registered.

## 11. Competing-risk views

V58 must evaluate at least two statistical views of the same event data.

A. Multiclass:
- P(TP);
- P(SL);
- P(TIMEOUT).

B. Event-time / competing-risk:
- P(TP before SL | X);
- P(SL before TP | X);
- time-to-event.

A survival/competing-risk implementation that is unavailable or statistically inappropriate for a fold is reported as UNAVAILABLE_MODEL rather than replaced post hoc.

## 12. Continuous feature families

V58 preferentially uses continuous, causal measurements. Boolean flags may remain only when they represent an event state that cannot be meaningfully parameterized, and their rationale must be recorded.

Required candidate families are defined in V58_FEATURE_SCHEMA.json.

Key family groups:
- BASE / MARKET;
- HTF;
- ICHIMOKU;
- ICT_SMC;
- BROOKS.

The same feature snapshot is reused across model variants. Ablation variants may select columns but may not recompute historical features using outcome-dependent logic.

## 13. Regime engine

Minimum causal states:
- TREND_UP;
- TREND_DOWN;
- RANGE;
- HIGH_VOL;
- LOW_VOL;
- TRANSITION.

Required stored outputs:
- regime_label;
- regime_probability or regime_confidence;
- regime_model_version;
- regime_available_at.

Candidates to compare without future information:
- deterministic trend-strength/volatility state;
- CUSUM change detection;
- causal change-point method if dependency and online semantics are valid.

Regime rules are fitted/estimated only on allowed past data. Full-sample regime relabeling is prohibited.

## 14. Baseline model tournament

Allowed baseline stage:
B0 strategy-only;
B1 Logistic / Multinomial Logistic Regression;
B2 Ridge / simple linear model where target formulation permits;
B3 HistGradientBoosting;
B4 XGBoost/LightGBM only if dependencies are available and reproducibly pinned;
B5 simple survival / competing-risk model.

No Transformer, Mamba, time-series foundation model or RL candidate may enter the V58 promotion gate before the baseline stage produces sufficient evidence.

All candidates receive the same temporal data budget, fold boundaries and evaluation budget.

## 15. Calibration

Probability models are assessed with:
- Brier score;
- log loss;
- reliability curve;
- calibration error.

Permitted calibration challengers:
- Platt;
- isotonic;
- temperature-style scaling when model output semantics support it.

Calibration fitting occurs on validation data only. Final test is never used to fit or choose a calibrator.

## 16. Uncertainty and abstention

Every scored event must expose:
- expected_edge;
- uncertainty;
- regime_confidence;
- model_confidence;
- shift_score;
- abstain_reason if rejected.

Mandatory abstention:
- invalid/missing critical features;
- unknown or insufficiently confident regime when the frozen policy requires regime confidence;
- excessive uncertainty;
- out-of-distribution/shift score above frozen validation-derived threshold;
- expected utility below safety margin;
- risk veto;
- portfolio veto;
- invalid execution state.

NO_TRADE is recorded, not dropped.

## 17. Expected utility

V58 may not use probability > 0.5 as its primary decision policy.

Frozen structural form:

EU =
P_TP × Reward
- P_SL × Loss
- P_TIMEOUT × TimeoutPenalty
- TransactionCosts
- UncertaintyPenalty
- RiskPenalty

Trade admission requires:
EU > SafetyMargin

Every coefficient, penalty and safety margin must be selected only from training/validation data or fixed ex ante. Final-test tuning is prohibited.

A probability-only threshold remains a benchmark, not the promoted policy.

## 18. Cost model

Required round-trip stress grid:
- 0 bps;
- 24 bps;
- 36 bps;
- 50 bps.

Cost decomposition, where measurable:
- fees;
- spread;
- slippage proxy.

Latency, partial fill and market impact are stress-tested only when the required data are valid. Missing microstructure data are not fabricated.

## 19. Validation

Primary protocol:
expanding walk-forward with strict chronological train → validation → test.

When event labels overlap:
purging and embargo are mandatory.

CPCV may be added as a robustness analysis, not used to leak information across time.

Prohibited:
- random shuffle split;
- global preprocessing before fold construction;
- final-test threshold tuning;
- full-sample feature selection;
- fitting regime/calibration models on final holdout.

Minimum split invariant:
max(train information time) < min(validation decision time) < min(test decision time), with purge/embargo expanded to cover target overlap.

## 20. Required ablation matrix

BASE

BASE + HTF

BASE + ICHIMOKU

BASE + ICT_SMC

BASE + BROOKS

BASE + ICHIMOKU + ICT_SMC

BASE + ICHIMOKU + BROOKS

BASE + ICT_SMC + BROOKS

ALL

DROP_ICHIMOKU

DROP_ICT_SMC

DROP_BROOKS

DROP_HTF

Incremental contribution is assessed both predictively and economically. A family is not supported merely because one metric improves.

## 21. Regime ablation

Report every eligible result separately for:
- trend;
- range;
- high volatility;
- low volatility;
- transition.

Global average is insufficient. If a family works only in one regime, the result is labelled regime-specific and is not generalized globally.

## 22. Cross-asset validation

Required asset universe:
- BTC;
- ETH;
- SOL;
- XRP;
- DOGE.

A finding positive on only one asset is not generalizable alpha unless a separate asset-specific hypothesis was frozen before outcome inspection.

Kraken remains sealed as an inherited external-holdout venue unless a later pre-outcome amendment explicitly changes the holdout design.

## 23. Statistical validation

Required:
- moving-block bootstrap;
- paired bootstrap for matched policy/model comparisons;
- confidence intervals;
- effect size;
- PSR;
- DSR;
- PBO or equivalent multiple-testing/selection-bias awareness.

If the number of trials is too small or assumptions are not met for a statistic, report NOT_APPLICABLE rather than forcing a value.

Multiple testing and model-search breadth must be recorded in the Trial Registry.

## 24. Metrics

Economic:
- Net Return;
- Annualized Return;
- Sharpe;
- Sortino;
- Maximum Drawdown;
- Calmar;
- Profit Factor;
- Expectancy;
- Turnover;
- Exposure;
- Trade Count;
- Average Holding Time;
- Transaction Cost;
- VaR;
- CVaR.

Predictive:
- Brier;
- Log Loss;
- AUROC where appropriate;
- Precision;
- Recall;
- calibration metrics.

Selective trading:
- coverage;
- abstention_rate;
- return_at_coverage;
- Sharpe_at_coverage;
- error_at_coverage.

## 25. Risk engine

Risk is independent and has veto authority.

V58 research limits:
- risk_per_trade <= 0.25% of equity;
- max_asset_weight <= 35%;
- gross_exposure <= 70%;
- drawdown_kill_switch = 5%.

The AI/meta-model may not override RiskEngine.

Risk-reducing exits must not be blocked by an entry-oriented risk gate.

## 26. Portfolio engine

Both views are mandatory:
1. separate-account per-asset analysis;
2. shared multi-asset portfolio simulation.

Shared portfolio tracks:
- cash;
- capital competition;
- gross exposure;
- net exposure;
- asset concentration;
- correlation/cluster exposure;
- turnover;
- mark-to-market equity.

The shared portfolio result is required for promotion; summing independent accounts is not sufficient.

## 27. Fundamental, on-chain and whale data

Such features enter historical V58 tests only when they satisfy:
- point-in-time timestamps;
- effective_at;
- available_at;
- observed_at;
- source traceability;
- source hash;
- historical availability.

Schema:
entity
metric
effective_at
available_at
observed_at
source
value
confidence
source_hash

If historical PIT data do not exist:
UNAVAILABLE_PIT_DATA.

No current-day metric may be copied backward into historical rows.

## 28. Promotion gate

Promotion requires all of:
- positive net economic contribution after costs;
- reasonable turnover;
- acceptable drawdown;
- multi-fold stability;
- multi-asset support;
- confidence-interval evidence;
- no detected leakage;
- acceptable calibration;
- baseline superiority on relevant dimensions;
- NO_TRADE superiority on the promoted coverage region;
- protocol and artifact reproducibility.

Accuracy alone never promotes a model.

## 29. Automatic rejection

REJECT if any material case holds:
- accuracy improves while net economic outcome worsens;
- return improvement requires explosive turnover;
- only one asset works without an asset-specific preregistration;
- only one fold works;
- effect disappears at realistic costs;
- confidence interval materially includes harmful outcomes;
- NO_TRADE dominates;
- results are threshold-fragile;
- leakage is detected;
- final-test tuning occurred;
- dataset identity cannot be reproduced.

## 30. Deep-learning gate

Only after baseline promotion evidence:
- PatchTST;
- iTransformer;
- Mamba/SSM.

Then, if justified:
- TimesFM;
- Chronos;
- Moirai.

They receive no privileged budget or validation exception.

## 31. RL gate

RL is prohibited for primary directional prediction in the baseline stage.

Only after a supervised/meta layer survives promotion criteria may RL be researched for:
- position sizing;
- execution;
- dynamic exits;
- portfolio allocation.

Potential candidates:
PPO, SAC, DQN where appropriate, Decision Transformer, offline RL.

Reward must be cost/risk aware:
NetPnL - λ1 Costs - λ2 Drawdown - λ3 CVaR - λ4 Turnover.

## 32. Reproducibility

Every run records:
- run_id;
- trial_id;
- git_commit;
- dataset_hash;
- config_hash;
- feature_version;
- model_version;
- random_seed;
- timestamp;
- dependency/environment fingerprint.

Every final artifact must be traceable to the exact source and data identity.

## 33. Required tests before empirical training

At minimum:
- test_no_lookahead;
- test_train_only_scaling;
- test_event_timestamp;
- test_next_bar_execution;
- test_fee_accounting;
- test_stop_target_ambiguity;
- test_drawdown_kill_switch;
- test_duplicate_event;
- test_duplicate_order;
- test_portfolio_cash_constraint;
- test_feature_causality;
- test_pit_availability;
- test_abstention;
- test_cost_gate;
- test_replay_determinism;
- test_hash_reproducibility.

Additional V58 gate tests:
- test_validation_only_calibration;
- test_final_holdout_not_used_for_selection;
- test_regime_causality;
- test_event_id_determinism;
- test_risk_veto_cannot_be_bypassed;
- test_live_execution_hard_false;
- test_paper_execution_hard_false.

## 34. Required final artifacts

V58_FROZEN_RESEARCH_PROTOCOL.md
V58_DATA_MANIFEST.json
V58_FEATURE_SCHEMA.json
V58_EVENT_SCHEMA.json
V58_MODEL_REGISTRY.json
V58_TRIAL_REGISTRY.csv
V58_ABLATION_RESULTS.csv
V58_REGIME_RESULTS.csv
V58_ASSET_RESULTS.csv
V58_COST_STRESS_RESULTS.csv
V58_CALIBRATION_RESULTS.csv
V58_PORTFOLIO_RESULTS.csv
V58_BOOTSTRAP_RESULTS.csv
V58_STATISTICAL_AUDIT.md
V58_RESEARCH_RESULT_FA.md
V58_RESEARCH_RESULT_EN.md
V58_REPRODUCIBILITY_MANIFEST.json

## 35. Scientific language

Without evidence, do not claim:
- profitable bot;
- guaranteed return;
- high win-rate strategy;
- production-ready trading AI;
- proven alpha;
- LIVE ready.

Permitted failure/uncertainty labels:
- INSUFFICIENT_EVIDENCE;
- NO_INCREMENTAL_ALPHA_EVIDENCE;
- FAILED_PROMOTION_GATE;
- RESEARCH_CHALLENGER;
- HYPOTHESIS_ONLY;
- UNAVAILABLE_PIT_DATA.

## 36. Freeze gate

Research-design freeze: PASS.

Implementation scaffolding authorization: CONDITIONAL_GO.

Model training/backtest authorization: NO_GO until all of the following are true:
1. V58_EVENT_SCHEMA.json has no unresolved target/stop/target/horizon field required for the chosen arm;
2. V58_DATA_MANIFEST.json contains exact source and dataset hashes;
3. mandatory leakage and temporal tests pass;
4. cost/risk configs are frozen;
5. Trial Registry is initialized;
6. final holdout identity is sealed and not used for tuning.

PAPER_EXECUTION remains false.
LIVE_EXECUTION remains false.
