# B. Repository compatibility and architecture gap report

Audit date: 2026-10-10 UTC. Repository: `parsa314/modular-crypto-trading-bot`.
This is a read-only source/governance audit. It neither edits frozen research records nor authorizes execution. File fingerprints, class/function locations and fetched PR identities are in `../evidence/compatibility.json`.

## 1. Exact source and branch identities

The audited default-branch snapshot is **main at `fa31e6f5ceb09f4f14ae13fef50706f2e10e1835`**, locally preserved at `/workspace/source-snapshots/fa31e6f5ceb09f4f14ae13fef50706f2e10e1835`. It is a connector-fetched source snapshot, not a Git working tree with a local HEAD. No existing working tree was switched or merged by this audit.

| PR / purpose | Observed state | Moving head SHA | Source branch | PR base SHA |
|---|---|---|---|---|
| #62 prospective v0.51 | OPEN, non-draft, unmerged | `cd897d9ac367e1f3cbead94e720efcaf949093c1` | `research/v51-overlap-arbitration-empirical` | `3f1a7a1a7bb6f612c734295a988b1c2d5c86e9aa` |
| #80 Bitpin read-only | OPEN, DRAFT, unmerged | `3ef544788066b4f6851f8c0261681563c5c95c35` | `feature/bitpin-readonly-2026-09-14` | `0ef14c03b034cc5b08a67bef43057fb375d5f6f0` |
| #110 production convergence | OPEN, DRAFT, unmerged | `fde6e758bfab244e40055778aa84c42cf454f295` | `integration/production-convergence-20261005` | `8d94096d8d9a1d066a30ecabb8134b55581a539f` |
| #111 PostgreSQL journal | OPEN, DRAFT, unmerged | `6234a5d9d80dadab20a3bbd7a5e729b7a3e1fe62` | `infrastructure/postgres-phase2a-20261005` | `fde6e758bfab244e40055778aa84c42cf454f295` |

PR metadata was freshly fetched from GitHub; a non-null `merge_commit_sha` on an open PR is a synthetic/test merge identity and does not establish merge completion. All four observed PRs have `merged=false`. Their PR base SHAs are lineage facts, not the current main SHA. #110 changes 52 files and #111 adds/changes 20 files on #110; neither should be blindly merged for course integration.

## 2. Immutable scientific identities

`AGENTS.md`, `README.md` and `docs/CANONICAL_RESEARCH_STATUS_2026-09-13.md` agree on `RESEARCH_ONLY / PAPER_OFF / LIVE_OFF / KRAKEN_SEALED`. The dated engineering decision `docs/THINK_TANK_DECISION_2026-10-05.md` adds a sandbox-only TESTNET gateway; it does not override scientific promotion or real-money authorization. Historical evidence documents and static v0.17 milestone policy are not current authorization records.

Latest completed result is `V50_NONOVERLAP_FAILURE_SUPPORTED`, a mechanistic failure attribution result:

- Scientific SHA: `1dd0b1fe506fc51ceec4ff8934b77090f86b6cc2`.
- Preregistration SHA: `9b3f8d1bda650cc93af7b6cc69f162ae30d64cac`.
- Workflow run: `34707823108`; artifact ID: `10302830689`.
- Artifact digest: `sha256:da5813a8c031f9da6cc940fda942efc846ca536dbd952f4930584c30732373e9`.

Active v0.51 remains `PROSPECTIVE_EVIDENCE_NOT_YET_CANONICALLY_COLLECTED / SCIENTIFIC_DECISION_DEFERRED` in the canonical navigation record. This audit did not consume or authenticate a newer prospective outcome artifact and therefore does not upgrade that status.

- Original preregistration: `d8ee4576aaf55750dd5910cc0d3b2efcbba3f5b2`.
- Calendar freeze: `6a8fa49de2d1befa9c17049aa61e13da20a028eb`.
- Predictor amendment: `4838c0d98c408d358929b0767676a5ac024bd8dd`.
- Integrity addendum: `da5dcf5cd3568a92c75871016f000917c9380955`.
- Repaired prospective start: `2026-09-13T12:00:00Z`.

Its public closed-4h-OHLCV contract is CoinEx/OKX/KuCoin, BTC/ETH/SOL/XRP/DOGE, five immutable 30-day blocks, and first observed at most 60 minutes after close. Bitpin, depth-driven grid/arbitrage and course strategies must be new, separately registered research candidates; they cannot be added to this frozen experiment. Missing bars cannot become prospective by backfill.

## 3. Actual main architecture and available interfaces

| Actual file / symbol | Observed responsibility | Compatible course seam / limitation |
|---|---|---|
| `research_bot/contracts.py:EvidenceStamp` | Aware observed/available timestamps and `is_available` | Reuse for point-in-time lineage; it lacks full order-book sequence/received-at schema |
| `research_bot/contracts.py:SignalEvidence` | Frozen forecast, costs, penalties, confidence and decision | Translate course hypotheses to it; nested `metadata` remains a mutable mapping unless callers freeze/canonicalize it |
| `research_bot/contracts.py:ExperimentManifest` | Git/data/model/cost/seed reproducibility | Reuse; mode enum has BACKTEST/PAPER/TESTNET/LIVE but no RESEARCH_ONLY member |
| `research_bot/decision.py:DecisionEngine` | Net alpha, confidence and PIT admission | Preserve thresholds; its state model is long-only BUY/HOLD/EXIT, not arbitrary short entry or a multi-leg atomic order |
| `research_bot/orchestrator.py:ResearchTradingOrchestrator` | Forecast → decision → independent RiskEngine → simulator | Extend this existing boundary; no batch conflict/portfolio reservation or runtime ledger is built into it |
| `research_bot/risk.py:RiskEngine` | Deterministic scalar risk veto, historic CVaR and sizing | Keep one risk engine; strategy output is data, never risk configuration |
| `research_bot/execution.py:PaperExecutionEngine` | Secret-free market-fill research simulator | It cannot submit real orders; however it defaults to PAPER simulation and immediate execution, not NoOrderTransport |
| `research_bot/execution_hardening_v52.py` | BBO validation, persistent fill lookup, atomic cash/position/fill settlement | Reuse actual executable-side/cost/settlement concepts; avoid a second financial settlement engine |
| `research_bot/persistence.py:MemoryPaperStore/PostgresPaperStore` | Observations, account, positions, fills and equity | Existing persistence seam; observation uniqueness is `(bar_timestamp,symbol,strategy_version)` |
| `research_bot/trial_registry_v17.py:TrialRecord/append_trial` | Fingerprinted append-only research trial JSONL | Existing experiment registry; course candidate status can map to HYPOTHESIS. It is not a concurrency-safe event journal |
| `research_bot/evidence_ledger_v17.py:MILESTONES` | Static historical v0.10–v0.16 claim registry | It is not a per-decision append ledger. Do not call it a full runtime Evidence Ledger |
| `research_bot/live_readiness.py` | Explicit missing gates and permanently false LIVE authorization | Reuse without treating checklist success as execution authorization |
| `research_bot/service.py` | FastAPI status/dashboard; import-time flag firewall | Extend its existing read-only monitoring projection rather than deploy a second Gradio control plane |
| `research_bot/point_in_time.py` | Availability-time backward joins, grouped panel correctness | Reuse and add stricter course source validation before joins |
| `research_bot/order_reconciliation_v52.py` | Deterministic identity, unknown-submit state and no-blind-retry rules | Reuse for event-driven simulation semantics; do not infer venue idempotency from an internal ID |
| `research_bot/testnet_execution.py` | Sandbox-only Decimal precision/minimums, durable store and reconciliation | Exists and stays disconnected from course research; no Bitpin private transport should be inferred |

The actual main orchestration does **not** implement the full conceptual chain `ResearchDataPort → HypothesisGenerator → DecisionEngine → ConflictArbiter → PortfolioGovernor → RiskGate → ExecutionAuthorization → ExecutionPort`. Its implemented sequence is smaller. The public service firewall remains fail-closed; a direct offline `ResearchTradingOrchestrator` call is not the public service and can produce a simulated paper fill. These are distinct facts, not evidence of a real order endpoint.

## 4. Requested names versus implementation

An AST inventory and text search of main `research_bot/**/*.py` found no implemented class/function named `CognitiveOrchestrator`, `RiskSovereignGate`, `ValidationHarness`, `MetaCognition` or `NoOrderTransport`. No CSM-DAP implementation was found. Earlier local design/package material does not establish that these are merged canonical components.

`research_bot/overlap_arbitration_v51.py:arbitrate_overlaps_v51` **does exist**. It implements `SIMULTANEOUS_MAX_EXPECTED_R_NO_PREEMPTION` for same-entry cohorts using the frozen expected-R admission. It is a specific scientific policy, not a universal strategy arbitration layer. In this main snapshot its `PROSPECTIVE_START_V51` is `08:00Z`, while the canonical integrity addendum records the repaired `12:00Z` start. Treat the earlier main implementation as incomplete navigation for canonical prospective evidence. Do not repair this frozen-science discrepancy as part of course engineering or silently consume its earlier window.

## 5. Existing strategy, ML and validation functionality

`strategy_lab.py` already supplies causal Ichimoku state, rule-based strategies, ATR sizing, next-open research assumptions, logistic meta-labeling with train-fitted imputer/scaler, and expanding temporal evaluations. `multitimeframe_strategies_v19.py` already has a source-derived strategy registry, technical/ICT-style causal research features, direction generation, bracket-event simulation and moving-block uncertainty. Neither source establishes that Jamalian's notebooks implemented those existing strategies.

Reuse actual `features.py`, `ichimoku_advanced.py`, `regime.py`, `backtest.py`, `oos_tournament_v10.py`, `robustness_v11.py` and `audit_v19.py` where their mathematical definitions match the new specification. Preserve old result versions and parameters. Indicators with different smoothing or initialization must get explicitly distinct, tested definitions rather than be called equivalent.

Advanced statistical primitives **exist** in `regime_robustness.py`:

- `deflated_sharpe_probability`: non-annualized sample Sharpe with skew/kurtosis and an expected-max benchmark when trial dispersion is supplied; too little history returns NaN.
- `probability_backtest_overfitting`: combinatorial symmetric train/test group selection on a precomputed synchronized return matrix. It is a CSCV-style diagnostic, not event-interval-purged CPCV model retraining.
- `cpcv_sanity_panel`: model refit on group combinations with a row-neighborhood purge of `horizon+2`; emits split summaries. It does not reconstruct every full CPCV backtest path or prove information-interval purging.

No complete class named ValidationHarness, average-uniqueness/sequential-bootstrap stack or universal applicability gate was found in main. New validation should wrap and qualify existing primitives, make unsupported/insufficient cases machine-readable, and preregister actual trial counts; it must not manufacture PBO/DSR numbers for small samples.

## 6. Risk policy differences — preserve lineage

All rates below are fractions of equity, unless marked bps. The proposal column does not authorize modification.

| Field | Current main RiskEngine source/value | Other implemented source/value | Proposed handling / governance |
|---|---|---|---|
| Per-trade sizing | `RiskEngine.position_size_from_risk` default `0.01` | `StrategyLabConfig` / v19 tournament `0.0025`; v14 forward `0.005`; #110 constitution `0.0025` | Preserve each historical experiment; course configuration must name its governing profile, not silently overwrite main |
| Drawdown kill | `RiskLimits.max_drawdown=0.12` | StrategyLab/v19 `0.05`; v14 `0.12`; #110 `0.05` | Reconciliation requires explicit reviewed constitution; no course retuning of v0.51 |
| Gross exposure | `RiskLimits.max_gross_exposure=1.0` | StrategyLab `0.70`; v14 `1.0`; #110 `0.70` | Record difference; do not present 70% as already unified main |
| Single-asset weight | `RiskLimits.max_asset_weight=0.35` | v14 `0.25`; StrategyLab/#110 `0.35` | Preserve scope and state units |
| Per-step turnover | `RiskLimits.max_turnover_per_step=0.50` | #110 constitution `0.70` | 70% is less strict for this field; importing only preferred constants is unsafe |
| Spread / slippage | `RiskLimits`: `35` / `25` bps | Forward runner binds its own configured limits | Preserve; observed execution costs require explicit units |
| CVaR95 | `RiskLimits.max_cvar_95=0.035` | v14 and #110 `0.035` | Missing/insufficient CVaR history is not proof of low tail risk |
| Cash buffer | `RiskLimits.min_cash_buffer=0.05` declared | #110 constitution `0.05` | Main `RiskSnapshot` lacks cash and `evaluate` does not enforce this field; report this gap |
| Daily loss | No daily-loss field/gate in main RiskEngine | #110 `max_daily_loss=0.02` | Do not claim main already enforces it |
| Correlation / net exposure / grid / unhedged legs | No complete unified gate in main RiskEngine | #110 documentation explicitly leaves full correlation allocation for later work | Integration remains gated until actual implemented scope is tested |

#110's `RiskConstitution` is immutable, hashed, refuses relaxed bounds and unknown/duplicate/non-finite JSON values. It belongs to an **open unmerged convergence branch**, not current-main's risk source of truth. Its `docs/RISK_CONSTITUTION_V1.md` confirms the 0.25%/35%/70%/5% profile. A future selective integration may reuse this exact profile on that lineage; this course audit does not approve flattening it into frozen scientific results.

## 7. Reproduced gaps and source-inspected limitations

Two controlled offline calls against pinned main were executed; no notebooks, APIs or trading transport were called:

1. `DecisionEngine.evaluate(expected_return=float('inf'), expected_cost=0, risk_penalty=0, uncertainty_penalty=0, confidence=.99, ...)` returns **BUY_CANDIDATE**. Confidence is checked but return/cost/penalty finiteness is not uniformly guarded. Course input validation must fail closed; any shared-engine correction needs focused regression tests and explicit engineering scope.
2. `RiskEngine.evaluate(RiskSnapshot(...valid scalars..., recent_returns=(float('nan'),)))` returns **approved=True, cvar_95=None**. The existing historic-tail function drops invalid observations and allows insufficient history. A new research path must not interpret this as verified tail safety. A universal change to this historical missing-tail policy needs separate review.

Other findings are source-inspected rather than reproduced network faults: metadata is not deep immutable; main orchestration has no portfolio reservation; main PaperExecutionEngine duplicate memory does not survive restart alone; LIMIT explicitly raises `LIMIT_ORDER_SIMULATION_UNSUPPORTED`; no exchange clocks or full-depth sequence contract in DepthSnapshot; no authenticated Bitpin endpoints in the audited read-only branch. None of these findings proves a real-money order occurred.

## 8. Selective branch interfaces and dependency collision risks

**Bitpin #80** implements `research_bot/bitpin_public.py` (`fetch_bitpin_markets/tickers/orderbook/recent_trades`) and `bitpin_depth.py:fetch_bitpin_depth` mapped to existing `coinex_depth.DepthSnapshot`. Source endpoints are implementation evidence, not verified current official API documentation. Timestamp parsing falls back to receipt-time for missing source timestamps; this cannot be mislabeled exchange event time or prove freshness. Book amounts/prices are floats, it lacks Decimal venue precision/network/auth capabilities, and no historical depth backtest follows from one live snapshot. Reuse parsing concepts after current API validation; do not silently merge the draft adapter or infer authenticated order APIs.

**Convergence #110** changes `research_bot/execution.py` into an `execution/` package with legacy exports. Existing import paths are deliberately preserved, but a file→package migration is a material compatibility change. It adds `LiveLedger` with immutable `claim_decision`, durable reservation, identity-pinned WAL/FULL SQLite, exclusive session locking and atomic compare-and-swap account settlement; `ProductionDecisionEnvelope` and `ExecutionReceipt` require `ENGINEERING_REPLAY`. These are not LIVE authorization merely because the journal is named LiveLedger.

**PostgreSQL #111** adds compatible `PostgresLiveLedger`, account-scoped advisory locks, transactional settlement and offline SQLite migration. It is stacked on #110. Journal integration should use the shared `claim_decision`/state contracts on the appropriate branch, not a second incompatible event database. Current-main research artifacts may use the existing TrialRecord registry and existing observation metadata without pretending that the convergence journal was merged.

Main `pyproject.toml` governs runtime dependencies: CCXT, NumPy, pandas, sklearn, FastAPI, uvicorn and psycopg; dev extras contain pytest/httpx. Large ML/deep/RL frameworks and Gradio are not mandatory course-integration dependencies. Avoid changing deployment-shell dependency ownership. Authenticated Bitpin session and unsupported order capabilities should explicitly remain unavailable until verified official contracts exist.

## 9. Minimal extension decision and rollback

Choose a focused, disabled-by-default course research extension on the pinned main lineage. Generate immutable candidate data, preserve notebook provenance/config identity, translate to `SignalEvidence` through **the existing ResearchTradingOrchestrator**, and retain **one RiskEngine**. Add a no-order capability boundary and deterministic course batch arbitration as separately versioned engineering additions; never substitute it for frozen v0.51 A0/A1. Unsupported short/hedged/multi-leg execution must yield explicit research-only limitations rather than misusing EXIT as a short entry.

Use existing research registry and persistence/journal interfaces. Introduce missing snapshot/candidate contracts only where existing contracts cannot express Decimal units, availability and immutable identities. Existing public FastAPI monitoring is the preferred dashboard integration seam. Offline stateful simulator extensions can reuse order-reconciliation/settlement semantics while exposing their evidence as engineering simulation, never paper/forward/live performance.

Acceptance requires deterministic fixtures, PIT/finiteness/bypass tests, configuration/provenance hashes, existing-module regression, and explicit blocked results where API documentation, historical book data or scientific samples are absent. No profitable strategy or completed prospective evidence follows from architecture compatibility.

Rollback is removal/disablement of the new course configuration and orchestration hook, retaining original contracts/risk defaults and append-only evidence. Never delete negative artifacts, alter frozen SHAs, reset a live order journal to enable retry, or change public execution flags to make tests pass.

## 10. Audit conclusion

**Compatible extension seam exists; full integration/promotion is not established by this report.** Mandatory gaps are the explicit no-order path, robust course input contracts, batch/plan reservation, evidence interface adaptation, verified Bitpin current capabilities, and scope-appropriate scientific validation. Treat the overall integration as blocked wherever those critical acceptance criteria remain unverified. The audit itself is source/provenance and controlled engineering evidence only.

## 11. Local engineering follow-up — distinct from pinned baseline

Subsequent local work in `/workspace/course-integration/work` corrected shared-engine input handling. A fresh controlled call with the same infinite forecast now raises `ValueError("NONFINITE_FORECAST")`; the same non-finite tail-risk input now yields `approved=False` and `INVALID_RISK_SNAPSHOT`. Exact local source hashes and observations are appended under `local_regression_observations` in compatibility.json. These observations do not rewrite the original baseline findings or claim a GitHub merge. Insufficient but finite tail history remains a separate legacy-policy issue requiring explicit course admission controls.

The local existing FastAPI service also gains a read-only course-report projection and dashboard panels, documented in `H_MONITORING_MIGRATION.md`. All frozen scientific identities above remain unchanged; current local implementation/test evidence is recorded separately in the final delivery package.
