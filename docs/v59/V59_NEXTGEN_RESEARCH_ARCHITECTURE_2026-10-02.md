# V59 Next-Generation Trading Research Architecture — 2026-10-02

Status: RESEARCH-ONLY  
PAPER_EXECUTION = FALSE  
LIVE_EXECUTION = FALSE

## Purpose

V59 updates the V58 research architecture using the strongest 2025-2026 directions in financial AI without treating newer model classes as proven alpha generators.

The architectural principle is:

```text
Rules / Market Structure
        +
Probabilistic ML / Time-Series Models
        +
Evidence-Grounded Agentic Intelligence
        ↓
Calibrated Ensemble + Conformal Abstention
        ↓
Economic Utility
        ↓
Independent Financial / Risk Control
        ↓
Shared-Capital Portfolio
        ↓
Optional RL allocator only after promotion
```

Newer models are candidates. They do not bypass baseline comparisons, cost realism, no-lookahead controls, multi-seed testing, or independent holdout promotion.

## Research frontier integrated into V59

### 1. Time-series foundation models are priors, not automatic alpha

Recent financial TSFM benchmarking suggests pretrained models such as TimesFM, Moirai and Chronos can rank strongly in some return-forecasting tasks, but gains over random-walk benchmarks can be sparse and asset-dependent. Therefore V59 treats TSFMs as candidate feature/forecast experts rather than trusted standalone signal generators.

Candidate family:

- TimesFM
- Moirai
- Chronos / Chronos-2
- PatchTST
- iTransformer
- causal LSTM / GRU / TCN

Promotion requires same-data, same-fold comparison against:

- Logistic / linear baselines
- HistGradientBoosting
- XGBoost / LightGBM when available
- LSTM/GRU
- PatchTST/iTransformer
- no-trade and strategy-only economic baselines

### 2. Agentic / LLM systems are restricted to evidence intelligence

2026 surveys of LLM trading agents report rapid architectural experimentation but weak protocol comparability, sparse explicit transaction-cost modeling and limited reproducibility.

Therefore V59 does NOT permit an LLM agent to directly place or authorize an order.

Agent roles are restricted to:

- fundamental research
- macro/news extraction
- on-chain / whale evidence synthesis
- source-quality assessment
- contradiction detection
- event deduplication
- scenario generation
- explanation and audit

Every claim must preserve:

- source
- effective_at
- available_at
- observed_at
- source hash
- confidence

LLM output becomes a feature/evidence object, never an execution command.

### 3. Multi-agent architecture is role-specialized, not majority-vote execution

V59 may use specialized research agents:

- Technical / Market Structure Agent
- On-chain / Whale Agent
- Fundamental Agent
- Macro / News Agent
- Risk Agent
- Model-Risk / Leakage Auditor
- Portfolio Analyst
- Reproducibility Auditor

A coordinator may aggregate evidence, but:

```text
multi-agent consensus != permission to trade
```

The final path remains:

```text
signal
→ calibrated ML
→ uncertainty / abstention
→ economic utility
→ independent financial veto
```

### 4. Regime-aware routing

Static model selection is replaced by a research-only regime router.

Regimes remain:

- TREND_UP
- TREND_DOWN
- RANGE
- HIGH_VOL
- LOW_VOL
- TRANSITION

The router may choose which already-trained expert contributes to an ensemble.

It may NOT:

- train on the current test window
- alter risk limits
- rescue rejected trades
- use future regime labels

Candidate experts:

- rule-based confluence
- FVG/ICT/TSI MTF
- gradient boosting
- Diff-LSTM / causal LSTM
- TCN
- PatchTST / iTransformer
- TSFM adapter
- multimodal evidence model

### 5. Conformal / selective prediction

V59 upgrades uncertainty from heuristic confidence only to a candidate conformal/selective layer.

Research outputs may include:

- prediction set
- conformity score
- empirical coverage
- selective coverage
- error-at-coverage
- abstention rate
- VaR / tail-risk interval coverage

The system must be able to return:

```text
NO_TRADE_UNCERTAIN
```

instead of forcing a class decision.

### 6. Realistic execution economics

Modern RL research shows model rankings can materially change when market impact and realistic costs are introduced.

V59 execution simulation should progressively support:

- maker/taker fee
- bid-ask spread
- stochastic slippage
- nonlinear market impact
- latency
- partial fills
- funding / borrow cost
- minimum quantity / lot size
- liquidity capacity
- gap risk

Primary historical tests must remain conservative when these data are unavailable.

Missing microstructure evidence must be marked:

```text
UNAVAILABLE_DATA
```

rather than replaced with invented precision.

### 7. Memory-aware portfolio RL

Recent portfolio research supports memory-augmented RL under path-dependent transaction costs.

V59 therefore places recurrent SAC / PPO / TD3 only in a LATE promotion stage.

Permitted RL state may include:

- current positions
- prior positions
- cash
- realized / unrealized PnL
- turnover
- recent costs
- calibrated probabilities
- uncertainty
- regime probabilities
- correlation / cluster exposure
- drawdown state
- CVaR state

RL is not permitted to invent a signal family.

Initial RL action scope:

- sizing
- allocation
- rebalance / hold
- execution timing

not raw unconstrained BUY/SELL generation.

### 8. Constrained financial control remains outside AI

The financial constitution remains independent:

- risk_per_trade <= 0.25%
- max_asset_weight <= 35%
- max_gross_exposure <= 70%
- drawdown kill <= 5%
- CVaR95 <= 3.5%

AI / LLM / RL cannot relax these ceilings.

RL action is projected into the feasible set before simulation.

Any invalid action becomes:

```text
RISK_VETO
```

### 9. Multi-seed and multiplicity-aware DRL evaluation

Single-seed DRL backtests are not accepted as evidence.

Minimum DRL protocol:

- >= 10 independent training seeds for development comparison
- fixed hyperparameters before final holdout
- report median and dispersion
- report worst-seed / lower-quantile behavior
- account for all attempted algorithms / rewards / architectures
- bootstrap confidence intervals where valid
- PBO / PSR / DSR or another explicit selection-bias diagnostic
- no best-seed reporting

### 10. Leakage-resistant agent evaluation

LLM-based financial research can contain hidden historical-memory contamination.

For agentic experiments V59 should optionally support:

- anonymized asset IDs
- masked dates / calendar identifiers
- point-in-time news access
- explicit model knowledge-cutoff disclosure
- factor / beta return attribution
- alpha-vs-market decomposition

Agent return alone is insufficient.

### 11. Dynamic sparse asset ranking

For larger universes, a separate ranking layer may reduce the action space before portfolio allocation.

The ranking layer must use only information available at decision time.

Candidate ranking features:

- expected utility
- uncertainty
- regime confidence
- liquidity
- transaction cost
- marginal CVaR
- correlation contribution
- turnover impact

Prediction accuracy alone must not determine ranking.

### 12. Reward learning is experimental

Recent inverse-RL work suggests portfolio reward functions can be inferred from multiple expert demonstrations.

V59 records this as an experimental research track only.

Possible expert policies:

- minimum variance
- CVaR minimization
- risk parity
- strategy-only portfolio
- utility-gated portfolio
- human-defined defensive allocation

Any learned reward must be evaluated against a fixed hand-written utility and cannot silently replace the financial constitution.

## V59 model hierarchy

```text
TIER 0 — Baselines
  Strategy-only
  No-trade
  Logistic
  HistGradientBoosting
  XGBoost / LightGBM

TIER 1 — Temporal supervised
  Diff-LSTM
  GRU
  TCN
  CNN-LSTM

TIER 2 — Attention / foundation candidates
  PatchTST
  iTransformer
  TimesFM
  Moirai
  Chronos

TIER 3 — Multimodal ensemble
  Technical + structure
  on-chain
  whales
  fundamentals
  macro
  source-grounded news/sentiment

TIER 4 — Selective / uncertainty layer
  calibration
  conformal prediction
  abstention
  shift detection

TIER 5 — Financial controller
  expected utility
  cost
  liquidity
  exposure
  drawdown
  VaR / CVaR
  shared capital

TIER 6 — Optional promoted RL
  recurrent SAC
  PPO
  TD3
  constrained RL
  offline / imitation assisted RL

TIER 7 — Execution research
  market impact
  partial fill
  latency
  order-book / microstructure
```

## Current strategy families preserved

V59 does not discard V58 strategy research:

- S6 trend breakout
- Ichimoku
- ICT / SMC
- Al Brooks-inspired price action
- four-framework confluence
- C10_01 .. C10_10
- FVG_ICT_TSI_MTF

These remain deterministic candidate generators and are compared against learned models.

## New V59 decision contract

```text
Candidate Strategy Event
        ↓
Feature / Evidence Snapshot
        ↓
Expert Models
        ↓
Regime-aware Ensemble
        ↓
Calibration
        ↓
Conformal / Uncertainty Gate
        ↓
P(TP), P(SL), P(TIMEOUT)
        ↓
Expected Utility
        ↓
Financial Constitution
        ↓
Portfolio Competition
        ↓
OPTIONAL promoted RL allocator
        ↓
Research Simulation
```

Required decision outputs:

- event_id
- strategy_id
- symbol
- decision_at
- model_versions
- regime_probabilities
- P_TP
- P_SL
- P_TIMEOUT
- calibration_state
- conformity / uncertainty state
- expected_utility
- estimated costs
- requested_notional
- approved_notional
- risk veto reason
- final research action

## Promotion ladder

No new-generation component is promoted because it is newer.

### Gate A — software integrity
- deterministic replay
- no-lookahead tests
- train-only preprocessing
- point-in-time joins
- hash-bound configs

### Gate B — predictive value
- beats simple baseline out-of-sample
- calibrated probabilities
- useful selective coverage
- stable across folds/assets

### Gate C — economic value
- positive net result after costs
- acceptable turnover
- drawdown / CVaR compliance
- improvement over strategy-only and no-trade

### Gate D — robustness
- regime stability
- asset stability
- multi-seed stability for neural/RL models
- multiplicity-aware evidence
- stress costs / slippage / latency

### Gate E — independent holdout
- untouched data
- frozen model / thresholds
- no retuning

### Gate F — prospective paper simulation
Only after A-E.

LIVE remains a separate later decision.

## Priority order for implementation

### Priority 1
- conformal/selective uncertainty
- stronger calibration diagnostics
- model/strategy tournament registry
- multi-seed evaluation ledger

### Priority 2
- Diff-LSTM / TCN supervised baselines
- PatchTST / iTransformer
- TSFM adapters used as priors

### Priority 3
- multimodal PIT evidence fusion
- role-specialized LLM research agents
- factor/return attribution

### Priority 4
- realistic market impact / partial-fill simulator
- order-book / liquidity evidence

### Priority 5
- recurrent SAC / PPO / TD3 allocation research
- constrained action projection
- imitation / offline-RL experiments

### Not prioritized yet
- unconstrained autonomous LLM trading
- direct LLM order generation
- same-test auto-tuning
- opaque world-model trading without baseline evidence
- live self-modifying policies

## Key 2025-2026 research references

- Agentic Trading: When LLM Agents Meet Financial Markets. arXiv:2605.19337.
- Beyond Agent Architecture: Execution Assumptions and Reproducibility in LLM-Based Trading Systems. arXiv:2606.08285.
- When Agents Trade: Live Multi-Market Trading Benchmark for LLM Agents. arXiv:2510.11695.
- From Knowing to Doing: A Memory-Controlled Benchmark for LLM Trading Agents on Stock Markets. arXiv:2605.28359.
- Pretrained Time-Series Foundation Models for Financial Return Forecasting. arXiv:2606.27100.
- Memory-augmented deep reinforcement learning framework for portfolio optimization with path-dependent transaction costs. Array 31 (2026) 100991. DOI: 10.1016/j.array.2026.100991.
- Realistic Market Impact Modeling for Reinforcement Learning Trading Environments. arXiv:2603.29086.
- High-dimensional multi-period portfolio allocation using deep reinforcement learning. International Review of Economics & Finance 98 (2025) 103996. DOI: 10.1016/j.iref.2025.103996.
- Algorithmic trading by reinforcement learning in a collaborative manner. Applied Soft Computing (2026) 115168. DOI: 10.1016/j.asoc.2026.115168.
- Reward inference for portfolio optimization via multi-expert inverse reinforcement learning. Neurocomputing (2026) 135052. DOI: 10.1016/j.neucom.2026.135052.
- Reliable value at risk estimation with conformal prediction. Risk Management 28 (2026) 59. DOI: 10.1057/s41283-026-00243-6.
- Classification with reject option: Distribution-free error guarantees via conformal prediction. Machine Learning with Applications 20 (2025) 100664. DOI: 10.1016/j.mlwa.2025.100664.

## Final doctrine

V59 is not “more AI everywhere.”

It is:

```text
better evidence
+ better uncertainty
+ better cost realism
+ better model comparison
+ better risk control
+ optional adaptive intelligence
```

The newest model never outranks a simpler model without empirical evidence.
