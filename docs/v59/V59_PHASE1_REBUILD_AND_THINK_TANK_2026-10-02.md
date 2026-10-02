# V59 Phase 1 — Rebuild of the Trading Research Kernel

Date: 2026-10-02  
Branch: `research/v59-nextgen-architecture-20261002`  
Mode: STRICT SCIENTIFIC RESEARCH  
PAPER_EXECUTION = FALSE  
LIVE_EXECUTION = FALSE

## Why rebuild

V58 proved several useful engineering contracts, but it accumulated generation-specific modules and historical compatibility layers. V59 starts a clean kernel and migrates only behaviors that can be defended by explicit contracts.

The rebuild is not a declaration that the bot is profitable or live-ready. It is a controlled migration toward a real deployable system.

## Phase 1 scope completed

### New clean V59 package

```text
research_bot/v59/
  __init__.py
  __main__.py
  hashing.py
  config.py
  contracts.py
  decision.py
  finance.py
  evidence.py
  orchestrator.py
  registry.py
  think_tank.py
  compat_v58.py
```

### Core one-way decision architecture

```text
SignalCandidate
    ↓
ModelPrediction
    ↓
UncertaintyAssessment
    ↓
EconomicDecision
    ↓
FinancialDecision
    ↓
FinalResearchDecision
```

No downstream layer is permitted to rewrite an upstream observation.

### Temporal contract

V59 requires:

```text
decision_at < entry_time
```

The compatibility adapter deliberately rejects V58 rows where `signal_time == entry_time`, because such rows are temporally ambiguous under the stricter V59 contract. They must be regenerated or migrated with explicit signal-bar close and next-bar-open clocks.

### Financial constitution

Hard ceilings are centralized:

- risk_per_trade <= 0.25%
- max_asset_weight <= 35%
- max_gross_exposure <= 70%
- drawdown_kill <= 5%
- max_CVaR95 <= 3.5%
- minimum cash buffer defaults to 5%
- independent financial veto
- SHORT execution remains disabled in Phase 1

AI/RL cannot relax these ceilings.

### Economic gate

The kernel evaluates:

```text
EU =
P(TP) * reward
- P(SL) * loss
- P(TIMEOUT) * timeout_penalty
- transaction_cost
- uncertainty_penalty
- safety_margin
```

Mandatory abstention paths include:

- uncalibrated prediction
- low regime confidence
- high entropy
- distribution shift
- conformal/selective uncertainty

### Conformal/selective contract

V59 now has an explicit `UncertaintyAssessment` contract:

- prediction set
- conformity score
- empirical coverage
- abstention flag
- reason

This is infrastructure for a future fitted conformal layer; Phase 1 does not claim that a production conformal calibrator has already been trained.

### Immutable evidence chain

`EvidenceLedger` is append-only at the API level and hash-chained:

```text
GENESIS
  -> signal
  -> prediction
  -> uncertainty
  -> economics
  -> finance
  -> final research decision
```

Output files refuse overwrite.

### Component registry

New model families are registered as candidates, not automatically enabled:

Enabled now:
- deterministic migrated strategies
- Logistic baseline
- HistGradientBoosting baseline
- conformal/selective contract
- financial constitution

Disabled pending promotion:
- Diff-LSTM
- TCN
- PatchTST
- iTransformer
- TimesFM
- Moirai
- Chronos
- recurrent SAC
- PPO
- TD3

### V58 migration policy

V58 is not deleted.

V59 treats it as a source of tested components and evidence, but migration is explicit. The adapter:

- validates required fields
- rejects ambiguous timing
- does not invent regime confidence
- hashes source payloads
- marks unknown regime as untrusted

## Think Tank — end-of-stage review

The room contains the requested expert perspectives:

1. Data Professor
2. Financial Engineering PhD
3. Economics PhD
4. Computer Engineer
5. Python Engineer
6. Artificial Intelligence PhD
7. Mathematics PhD
8. Professional Al Brooks Trader
9. Professional Ichimoku Trader
10. Professional ICT Trader
11. Professional SMC Trader
12. Researcher / Scientific Writer

### Joint review

**Data Professor**  
Highest priority is rebuilding the real market-data plane around immutable raw bytes, exchange clock integrity, gap detection, deduplication, symbol metadata and point-in-time provenance.

**Financial Engineering PhD**  
The independent capital/risk boundary is correct. Next, spread, maker/taker fee, slippage, market impact, funding/borrow and partial fills must become explicit market evidence rather than fixed assumptions wherever data permits.

**Economics PhD**  
Regime and macro variables must be point-in-time and must not be used as hindsight labels. Structural-break handling should be separated from ordinary volatility classification.

**Computer Engineer**  
The new kernel boundaries are substantially cleaner than the inherited generation chain. Next priority is a typed data/event bus and clear adapters around exchange/data providers.

**Python Engineer**  
Phase 1 should remain dependency-light. Deep-learning frameworks should be optional extras and must not infect baseline data/decision code paths.

**AI PhD**  
Do not add LSTM/Transformer/RL before the baseline tournament infrastructure is finalized. Calibration, selective prediction and shift monitoring are higher priority than model size.

**Mathematics PhD**  
Every later strategy/model tournament must record the complete search universe. PBO/DSR/PSR and block-bootstrap evidence should be generated from the trial ledger, not added after selecting a winner.

**Al Brooks Trader**  
Trend, trading range, breakout, failed breakout, second-entry and follow-through concepts require deterministic state machines and should not be reduced to vague single-bar proxies.

**Ichimoku Trader**  
MTF Kumo/Tenkan/Kijun/Chikou states should be causal and synchronized to the decision clock. Kijun pullback and cloud breakout families should retain separate identities.

**ICT Trader**  
Sweep -> displacement -> MSS/CISD -> FVG/OB retrace must preserve order and expiry. FVG alone is insufficient evidence.

**SMC Trader**  
Liquidity pools, structure breaks, displacement and imbalance need explicit event identity and single-use/expiry semantics.

**Research Writer**  
Every performance statement must point to a run id, dataset hash, config hash, code commit and artifact hash. No narrative promotion without reproducible evidence.

## Think Tank decision

### Priority 1 for Stage 2

```text
REAL_MARKET_DATA_PIPELINE
```

The clean decision/risk kernel exists. The next highest-value task is not a new model. It is a trustworthy market-data and event-intake plane that can supply:

- authenticated OHLCV
- contiguous exchange clocks
- symbol metadata
- multi-timeframe aggregation
- spread/depth where available
- immutable source manifests
- point-in-time external evidence
- deterministic dataset versions

### Priority 2

```text
REALISTIC_EXECUTION_ECONOMICS
```

### Priority 3

```text
MODEL_AND_STRATEGY_TOURNAMENT_INFRASTRUCTURE
```

Deep/RL promotion remains blocked until those foundations exist.

## Phase 1 acceptance criteria

- clean V59 package exists
- execution cannot be enabled by configuration
- hard financial ceilings cannot be relaxed
- prediction timing is causal
- uncertain predictions can abstain
- financial layer can veto independently
- short execution remains off
- duplicate events fail closed
- evidence ledger is hash chained
- output evidence refuses overwrite
- deep/RL candidates remain disabled
- deterministic think-tank prioritization is implemented
- CI compiles V59, runs focused tests, then full repository regression

## Command

```bash
python -m research_bot.v59 integrity-demo --output results/v59-phase1
```

Expected artifacts:

```text
decision_ledger.json
component_registry.json
think_tank_phase1.json
summary.json
```

No artifact produced by this phase authorizes PAPER or LIVE execution.
