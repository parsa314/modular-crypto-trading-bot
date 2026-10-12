# V58 Stage 1 — Repository Audit and Research Gap Analysis

**Date:** 2026-09-25  
**Repository:** parsa314/modular-crypto-trading-bot  
**Branch:** research/v58-research-freeze  
**Execution:** RESEARCH_ONLY / PAPER_OFF / LIVE_OFF

## A. Audit of V57 / inherited research state

### Repository reality

The canonical repository is confirmed as parsa314/modular-crypto-trading-bot.

At the time of audit:
- main contains commits through 2026-09-24;
- the most recent observed main commit is 876e229a8d3e73f2e9d646d4126c8ce08e4c11b4;
- explicit branches exist for v0.54 research;
- no branches named v55, v56 or v57 were found;
- therefore V57 cannot currently be treated as a repository-verifiable version lineage.

The V57 state supplied for this transition is retained as a research-state report:
- Ichimoku / ICT / SMC / Al Brooks proxies exist in the research lineage;
- S6 baseline exists;
- H4/HTF causal features exist in the research lineage;
- cost-aware and risk-aware components exist;
- Logistic meta-layer/replay work was reported;
- a 0.5 meta threshold accepted no trades;
- independent forward alpha was not established.

Because the corresponding V57 immutable branch, commit, artifact manifest and full result bundle were not found in GitHub, these V57 claims are not promoted to canonical repository evidence.

### Nearest repository-verifiable scientific base

The closest verifiable base relevant to V58 is v0.54.

Frozen v0.54 source:
f234a85028ed2d86cd3bc672e7491f72b0245b9b

Observed v0.54 strengths:
- decision time tied to bar availability;
- explicit future-availability rejection;
- chronological expanding walk-forward;
- purge coverage for target horizon;
- scikit-learn Pipeline keeps imputation/scaling train-only;
- feature health is computed on train only per fold;
- feature-family ALL/ONLY/DROP ablation exists;
- cost stress includes 0/24/36 bps;
- terminal liquidation cost is explicitly charged;
- moving-block paired inference exists;
- dataset/schema hashing and frozen replay exist;
- PAPER and LIVE remain false.

Observed v0.54 scientific limitations:
- target is one-step future_return, not TP/SL/TIMEOUT;
- model output is converted with sign(pred), not an event-conditional meta-policy;
- execution is return-vector accounting rather than event-level next-bar-open stop/target simulation;
- no 50-bps required stress point in the v0.54 audit;
- no required calibration layer;
- no explicit uncertainty/abstention layer;
- no expected-utility admission policy;
- no required event-specific ARM_A...ARM_E architecture;
- no V58 shared-portfolio promotion result;
- regime-specific V58 ablations are not part of the v0.54 feature-family audit;
- PSR/DSR/PBO are not demonstrated in this v0.54 path;
- current v0.54 risk defaults differ materially from V58 research limits.

### Governance drift

The main README/canonical navigation still identifies v0.50 as the latest completed scientific result and v0.51 as the active prospective question, while later v0.54 source/evidence automation and data-collection commits are present.

This is a documentation/lineage governance gap. It must be repaired without rewriting historical records or flattening stacked scientific branches.

## B. Research Gap Analysis

### Gap G1 — Version provenance

Problem:
V57 is not a GitHub-verifiable branch/version.

Required V58 action:
- use explicit V58 branch and immutable freeze commits;
- import any V57 artifact only with source hash, artifact hash and provenance note;
- do not reconstruct missing V57 evidence from narrative.

Status:
BLOCKER_FOR_V57_REPRODUCTION, not blocker for new V58 design.

### Gap G2 — Event architecture

Problem:
v0.54 is row/return-prediction oriented.

Required:
- event generator;
- immutable event_id;
- strategy-arm identity;
- frozen feature snapshot;
- event lifecycle and ledger.

Status:
REQUIRED_IMPLEMENTATION.

### Gap G3 — Target mismatch

Problem:
v0.54 predicts next-period future_return.

Required:
- TP / SL / TIMEOUT target;
- time_to_event;
- STOP_FIRST ambiguity rule;
- competing-risk view.

Status:
REQUIRED_IMPLEMENTATION.

### Gap G4 — Execution semantics

Problem:
v0.54 economic audit multiplies sign(pred) by future return.

Required:
- next-bar-open entry semantics;
- stop/target path simulation;
- fees/spread/slippage;
- conservative intrabar ambiguity handling;
- no favorable delayed fill.

Status:
REQUIRED_IMPLEMENTATION.

### Gap G5 — Continuous information content

Problem:
many current technical fields are state flags/proxies.

Required:
continuous distance, slope, age, strength, position, displacement and volatility variables specified in V58_FEATURE_SCHEMA.json.

Status:
REQUIRED_IMPLEMENTATION.

### Gap G6 — Regime conditioning

Problem:
V58 requires causal regime label plus confidence and regime-level ablation.

Required:
causal trend/volatility baseline and optional CUSUM/change-point challenger.

Status:
REQUIRED_IMPLEMENTATION.

### Gap G7 — Model objective

Problem:
global sign prediction does not answer setup-selection question.

Required:
event-conditional multiclass/competing-risk meta-model with strategy-only baseline.

Status:
REQUIRED_IMPLEMENTATION.

### Gap G8 — Calibration and abstention

Problem:
no V58-compliant validation-only probability calibration, uncertainty score, shift score or explicit abstention policy is frozen in the inherited audit.

Required:
calibration tournament, uncertainty fields, NO_TRADE reasons and selective-risk metrics.

Status:
REQUIRED_IMPLEMENTATION.

### Gap G9 — Economic decision policy

Problem:
probability/sign threshold is not a cost-aware expected-utility policy.

Required:
EU decomposition with validation-only penalty/safety-margin selection.

Status:
REQUIRED_IMPLEMENTATION.

### Gap G10 — Cost stress

Problem:
v0.54 uses 0/24/36 bps.

Required:
add mandatory 50-bps stress and decompose fee/spread/slippage where data support it.

Status:
REQUIRED_IMPLEMENTATION.

### Gap G11 — Risk-policy mismatch

Observed inherited RiskLimits include:
- max_drawdown 12%;
- max_gross_exposure 100%;
- max_asset_weight 35%;
- position-sizing risk_fraction default 1%.

V58 requires:
- drawdown kill switch 5%;
- gross exposure 70%;
- max asset weight 35%;
- risk per trade <= 0.25%.

Required:
new V58 research config; do not silently mutate historical v0.54 results.

Status:
CRITICAL_CONFIGURATION_CHANGE.

### Gap G12 — Portfolio simulation

Problem:
V58 promotion requires real shared-capital multi-asset simulation in addition to separate-account reporting.

Required:
cash, exposure, concentration, correlation/cluster constraints and capital competition.

Status:
REQUIRED_IMPLEMENTATION.

### Gap G13 — Statistical audit completeness

Existing:
moving-block inference exists in v0.54.

Missing from this path:
PSR, DSR, PBO/multiple-testing audit and standardized effect-size reporting.

Status:
REQUIRED_IMPLEMENTATION.

### Gap G14 — Data completeness

Recent repository commits explicitly work on partial historical coverage and checksummed source acquisition.

Required:
V58_DATA_MANIFEST.json must prove:
- exact source;
- exact asset/venue/timeframe;
- first/last timestamp;
- missing-bar statistics;
- availability semantics;
- checksum/hash;
- admissible PIT status.

Status:
BLOCKER_FOR_TRAINING.

### Gap G15 — Fundamental/on-chain/whale PIT evidence

No historically complete V58 PIT dataset was established in this audit.

Required:
mark UNAVAILABLE_PIT_DATA unless a source-traceable historical record is produced.

Status:
OPTIONAL_FEATURE_BLOCKED, must not block price/technical baseline stage.

## I. Risks and blockers

1. V57 provenance discontinuity.
2. Incomplete/heterogeneous historical coverage.
3. event stop/target/horizon rules are not yet proven identical across strategy arms.
4. correlated feature families may create apparent incremental value without true information gain.
5. regime labels can leak if fitted retrospectively.
6. same-bar TP/SL ambiguity can create optimistic bias.
7. cost assumptions can dominate weak alpha.
8. multi-asset overlap can invalidate separate-account results.
9. repeated ablation/model search can overfit selection.
10. stale canonical-status documentation can cause agents to use the wrong scientific head.
11. the inherited execution/risk defaults are not the V58 limits.
12. current PIT fundamental/on-chain/whale history is not established.

## J. Go / No-Go

### GO
- design documentation;
- event/feature contracts;
- deterministic IDs;
- data-manifest tooling;
- leakage tests;
- validation infrastructure;
- evidence ledger scaffolding;
- research-only simulators.

### NO-GO
- model training;
- hyperparameter search;
- final backtests;
- PAPER execution;
- LIVE execution;
- any claim of alpha.

Training becomes eligible only after:
1. event target rules are numerically frozen for the included arms;
2. V58_DATA_MANIFEST.json is complete and hashed;
3. leakage/causality/time tests pass;
4. final holdout identity is sealed;
5. cost/risk config is frozen;
6. Trial Registry is initialized.

**Decision:** CONDITIONAL_GO_FOR_IMPLEMENTATION_SCAFFOLDING / NO_GO_FOR_EMPIRICAL_TRAINING.

PAPER_EXECUTION = FALSE  
LIVE_EXECUTION = FALSE
