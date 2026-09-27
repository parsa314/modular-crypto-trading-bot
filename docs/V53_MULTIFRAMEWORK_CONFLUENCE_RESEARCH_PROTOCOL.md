# v0.53 — Multi-Framework Confluence Research Protocol

Status: **PREREGISTRATION / NO ECONOMIC RESULT / NO LIVE AUTHORIZATION**

This document defines a new research line and does **not** alter the approved thesis title, the v0.51 prospective protocol, or the v0.52 execution-integrity work. It is a doctoral-rigor extension of the existing MSc implementation program.

## 1. Research question

Can a causally defined, multi-timeframe feature representation combining observable constructs derived from:

- ICT terminology,
- Smart Money Concepts (SMC),
- Al Brooks price action,
- Ichimoku Kinko Hyo,

improve out-of-sample, transaction-cost-adjusted and risk-adjusted crypto trading performance relative to each framework alone, simple technical baselines, and machine-learning baselines?

The hypothesis is **not** that any practitioner framework is true. Every construct must be translated into an observable, timestamp-valid, reproducible variable and tested prospectively or on untouched out-of-sample data.

## 2. Scientific positioning

ICT/SMC and Al Brooks are practitioner-defined frameworks rather than standardized academic ontologies. Their names, labels, and causal stories must therefore be separated from measurable market events. For example:

- "liquidity sweep" -> break of a pre-declared reference high/low followed by a time-bounded reclaim/acceptance rule;
- "order block" -> reproducibly specified pre-impulse consolidation / last-opposite-candle structure with objective displacement and invalidation conditions;
- "fair value gap" -> deterministic three-candle imbalance rule;
- "BOS/ChoCH" -> swing-structure state transition under a fixed pivot definition;
- "Always-In" -> state machine estimating directional persistence from bar sequence and failed reversal logic;
- "Ichimoku" -> causal contemporaneous features only; no future-leaking Chikou construction.

Any institutional-intent language is treated as interpretation, not measured ground truth.

## 3. Primary hypotheses

### H1 — Incremental confluence hypothesis
The frozen multi-framework feature set improves net economic performance versus the best single-framework baseline on untouched OOS data.

Primary endpoint: net Sharpe and net expectancy after all explicit costs.
Secondary endpoints: Sortino, Calmar, MDD, Profit Factor, turnover, hit rate, trade count, time-in-market.

### H2 — Meta-labeling / setup-quality hypothesis
A supervised classifier trained only on pre-entry information can distinguish higher-quality from lower-quality candidate setups and improve net expectancy by abstaining from weak trades.

### H3 — Regime-conditional hypothesis
The contribution of individual framework families is state-dependent. Regimes must be learned only from training data or frozen from a preregistered economic definition.

### H4 — Microstructure confirmation hypothesis
Where reliable trade/order-book data exist, microstructure variables improve setup confirmation beyond OHLCV-only practitioner constructs.

### H5 — Adaptive weighting hypothesis
Dynamic feature-family weighting can improve robustness only if it beats a frozen equal-weight confluence baseline on untouched data. Static weights are the default benchmark.

### H6 — RL position-management hypothesis
Reinforcement learning is evaluated only after a predictive/selection signal survives OOS validation. RL may control sizing/exit/execution, but it is not allowed to manufacture alpha from an unvalidated signal family.

## 4. Null hypotheses

For each primary hypothesis the null is that the proposed extension provides no positive incremental net value after costs, uncertainty, multiple-testing control and realistic risk constraints.

Negative findings remain valid thesis outcomes.

## 5. Scope

### Markets
Primary: liquid cryptocurrency spot/perpetual markets where public historical data are sufficiently complete.

Initial assets: BTC/USDT and ETH/USDT for controlled replication; expansion to a point-in-time eligible liquid universe only after the two-asset protocol is stable.

### Timeframes
- Context: 4h / 1h
- Setup: 15m
- Execution research: 5m where data quality is adequate

Multi-timeframe features must use only bars closed before the decision timestamp.

## 6. Feature families

### ICT / SMC observable features
- swing highs/lows under frozen pivot rule;
- BOS and ChoCH state transitions;
- displacement magnitude normalized by ATR/realized volatility;
- FVG width, age, fill fraction, direction;
- objective order-block candidate geometry;
- breaker / mitigation state transitions with explicit invalidation;
- premium/discount position within a frozen dealing range;
- distance to prior day/week/session highs and lows;
- liquidity-sweep event with reclaim/acceptance label;
- inducement proxy defined as pre-break local liquidity formation, never inferred intent;
- session / killzone indicator as clock-time context only.

### Al Brooks observable features
- trend vs trading-range state;
- Always-In direction state machine;
- bar type: trend bar / doji / inside / outside;
- consecutive bull/bear bar counts;
- H1/H2/L1/L2 style second-entry proxies under deterministic rules;
- failed breakout / failed reversal flags;
- pullback depth and duration;
- micro double top/bottom candidates;
- strong/weak signal-bar quality metrics;
- EMA-distance and trend-channel geometry where explicitly defined.

### Ichimoku causal features
- Tenkan, Kijun, Senkou A/B from information available at decision time;
- price/cloud state;
- cloud thickness and slope;
- TK state and distance;
- Kijun distance;
- future-cloud orientation only when it is the standard projection computed from current/past inputs;
- Chikou may be represented only in a leakage-safe way. No future close may enter current features.

### Microstructure features
Subject to data provenance and availability:
- signed trade imbalance;
- taker buy/sell imbalance;
- top-of-book and multi-level imbalance;
- spread;
- depth and depth slope;
- short-horizon realized volatility;
- order-flow persistence;
- cancellation / replenishment proxies where available.

## 7. Labels

At least three label families are evaluated separately:

1. next-horizon signed return;
2. Triple Barrier outcome with frozen PT/SL/horizon;
3. stop-loss-aware economic label including transaction costs.

No label is allowed to leak into features or candidate construction.

## 8. Model tournament

### Non-ML baselines
- Buy & Hold;
- no-trade / cash benchmark;
- single-system deterministic ICT/SMC proxy;
- single-system Brooks proxy;
- deterministic Ichimoku;
- equal-weight confluence.

### Supervised baselines
- Logistic Regression;
- HistGradientBoosting;
- Random Forest;
- XGBoost/LightGBM only if dependency and reproducibility remain controlled.

### Sequence models
Evaluated only after tabular baselines:
- GRU/LSTM;
- PatchTST/other sequence model only with a preregistered comparison.

### RL
PPO/DDQN or other RL is gated behind survival of the upstream signal. RL reward must include costs, turnover and drawdown/risk penalties.

## 9. Validation design

### Temporal validation
- chronological split only;
- purged/embargo walk-forward;
- untouched final holdout;
- cross-asset replication;
- regime-stratified reporting;
- prospective forward PAPER stage after research gates pass.

### Search-aware statistical controls
- complete trial registry;
- paired dependence-aware bootstrap;
- FDR control for planned multiple comparisons;
- Hansen SPA / White Reality Check when a complete strategy family is available;
- Deflated Sharpe Ratio only when the number of trials and return distribution are valid inputs;
- PBO/CSCV only when all tested configurations are retained;
- minimum track-record reasoning before any stable-performance claim.

## 10. Transaction-cost model

Net returns must include, where applicable:

- commission;
- bid/ask spread;
- slippage;
- impact proxy;
- funding;
- borrow cost;
- execution latency sensitivity.

The primary result is always net of the base cost model, with stress-cost sensitivity reported separately.

## 11. Risk architecture

Risk control remains independent from the prediction model.

Hard controls:
- max gross exposure;
- max per-asset exposure;
- fixed maximum risk per trade;
- ATR/structure-aware stop distance;
- volatility scaling;
- daily-loss and drawdown kill switches;
- turnover limits;
- stale-data / stale-quote rejection;
- no order after uncertain reconciliation state.

Kelly sizing, if studied, must be fractional, capped and compared against simpler fixed-risk sizing.

## 12. Leakage rules

Forbidden:
- future swing confirmation hidden inside current features;
- future-aware pivot labels used as real-time features;
- Chikou construction using future close;
- full-sample normalization;
- universe selection using future liquidity/survival information;
- threshold tuning on final holdout;
- post-hoc regime pruning;
- removing losing trials from the registry.

## 13. Evaluation metrics

Economic:
- total return / CAGR;
- Sharpe;
- Sortino;
- Calmar;
- MDD and drawdown duration;
- Profit Factor;
- expectancy;
- turnover;
- fee/spread/slippage drag;
- trade count;
- no-trade rate;
- exposure/time-in-market.

Predictive:
- ROC-AUC / PR-AUC where appropriate;
- Brier score / calibration error;
- precision/recall by setup class;
- calibration curves.

Robustness:
- fold dispersion;
- asset dispersion;
- regime dispersion;
- cost sensitivity;
- bootstrap confidence intervals;
- multiple-testing-adjusted significance.

## 14. Advancement gates

A candidate cannot advance merely because cumulative return is positive.

Minimum promotion logic:
1. no PIT/leakage violation;
2. positive net incremental value vs the preregistered benchmark;
3. acceptable drawdown and risk concentration;
4. result not dependent on a single fold/asset/regime;
5. uncertainty interval not inconsistent with the claimed improvement;
6. survives planned multiple-testing/search-aware checks when statistically applicable;
7. then prospective PAPER replication;
8. only then TESTNET readiness review.

LIVE execution is outside this protocol and requires a separate explicit authorization gate.

## 15. Engineering roadmap

### Phase A — Research ontology and literature review
- systematic search protocol;
- practitioner-to-observable concept dictionary;
- 50+ paper evidence matrix;
- 10–15 high-quality theses/dissertations;
- research-gap synthesis.

### Phase B — Data and PIT layer
- canonical timestamps;
- multi-timeframe alignment;
- point-in-time universe;
- OHLCV + optional public trades/order-book store;
- lineage and data-quality reports.

### Phase C — Deterministic feature engine
- ICT/SMC features;
- Brooks features;
- Ichimoku features;
- property tests for no-lookahead and invariants.

### Phase D — Baseline strategy lab
- each framework alone;
- equal-weight confluence;
- cost-aware deterministic comparison.

### Phase E — ML setup-quality layer
- tabular baselines first;
- calibrated probability estimates;
- meta-labeling and abstention;
- SHAP / feature-family attribution.

### Phase F — Regime and sequence models
- regime detector;
- frozen gating comparison;
- LSTM/GRU/PatchTST only if justified by previous evidence.

### Phase G — RL risk/position layer
- sizing/exit/execution control only after upstream signal survives.

### Phase H — Robust statistical audit
- purged walk-forward;
- untouched holdout;
- search-aware audit;
- cross-asset and regime replication.

### Phase I — Forward PAPER / exchange adapter
- execution-safe paper state machine;
- real quote/slippage capture;
- reconciliation and realized-cost accounting;
- no LIVE.

### Phase J — thesis and defense package
- evidence-grounded chapter drafts;
- reproducible figures/tables;
- negative results preserved;
- defense demo and technical appendix.

## 16. Deliverables

- modular Python package;
- tests + CI;
- configs;
- notebooks;
- Docker support;
- research/evidence registry;
- literature matrix;
- experiment manifests;
- backtest / OOS / forward reports;
- monitoring dashboard;
- thesis chapters and defense materials.

## 17. Claim policy

Until evidence exists:

- `engineering_operational`: may become true through tests;
- `predictive_edge`: false;
- `profitable_strategy`: false;
- `stable_sharpe`: false;
- `live_ready`: false;
- `real_money_execution_authorized`: false.

The scientific objective is falsification-resistant evidence, not a profitable-looking backtest.
