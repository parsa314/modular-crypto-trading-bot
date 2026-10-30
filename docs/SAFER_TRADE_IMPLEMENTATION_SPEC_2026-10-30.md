# SAFER-Trade — Integration Specification for the Canonical AI/ML Trading Bot
Date: 2026-10-30 | Status: **PROPOSED / RESEARCH ONLY / NOT INTEGRATED** | Owner project: `parsa314/modular-crypto-trading-bot`

## 0. Authority, invariants, and truthful current status

This is a **non-destructive proposal** for the existing four-family bot, **not a replacement repository, executed trading system, approved strategy, or proof of financial performance**.
Read `AGENTS.md`, `docs/CANONICAL_RESEARCH_STATUS_2026-09-13.md`, issue #126, and relevant frozen scientific SHAs before work.
Default-branch snapshot observed at proposal time: `7db5643f24bb713ba1df2abb9a2989f73d56a8c6`; research and engineering PRs remain stacked and must not be blindly merged.
Owner authorization to potentially trade live is recorded in `AGENTS.md`; **technical readiness, tested execution capability, and runtime activation are separate facts**. No live or testnet orders are authorized by this proposal.

Reference modules on main:
- `research_bot/contracts.py`: `EvidenceStamp`, `SignalEvidence`, `ExecutionMode`, experiment provenance;
- `research_bot/decision.py`: net-alpha and `NO_TRADE` logic;
- `research_bot/risk.py`: independent risk veto;
- `research_bot/execution.py`: paper simulator with known limits;
- `research_bot/execution_hardening_v52.py` / `research_bot/order_reconciliation_v52.py`: quote freshness, atomic settlement, idempotency, reconciliation research;
- `research_bot/live_readiness.py`: fail-closed readiness without real-money authorization;
- `docs/V51_OVERLAP_CONFLICT_ARBITRATION_PREREGISTRATION.md`: frozen conflict hypothesis;
- `docs/RESEARCH_TRACEABILITY_MATRIX.md`: lineage and evidence map.

**Important:** Issue #126's nominal risk limits and some user-proposed academic limits conflict or differ (daily new-entry halt, drawdown, stop-loss allocation). Resolve via an explicit versioned Risk Constitution ADR and tests; **do not change production thresholds from this document**. V52 PostgreSQL failures reported outside a verified run must remain unverified until log, SHA and reproduction are captured. No economic promotion while financial invariants fail.

## 1. Scope and hypothesis

Goal: compare four causal strategy arms — Ichimoku, ICT, SMC, Al Brooks — with supervised ML (LightGBM), an experimental PPO allocation/sizing challenger (FinRL-X-inspired), and optional TradingAgents-inspired LLM commentary, **under a single independent execution/risk ledger**.

Research questions:
- Incremental after-cost utility of B1 ML versus B0 deterministic four-arm baseline;
- Incremental after-cost utility of B2 RL versus B1, after equal risk budgets and realistic execution;
- Effect of execution realism, regime transitions, selection bias and risk overrides;
- Optional B3 multi-agent text signals versus B2, only with properly timestamped news.

No copying external source code without license review. FinRL-X, TradingAgents, NautilusTrader and LEAN are **candidate integrations**, not installed or validated dependencies.

## 2. Seven-layer target architecture

1. **PIT data**: raw market events with `event_at`, `available_at`, venue, symbol, schema, immutable SHA256, quality flags; no synthetic observations misrepresented as historical.
2. **Feature and regime**: closed-bar H1 signals with H4 filter, deterministic four-school features and causal volatility/trend regimes. Every derived timestamp <= decision time.
3. **Intelligence**: B0 deterministic, B1 LightGBM, B2 PPO sizing/weights (FinRL-X-inspired). Train on frozen splits and identical market events; RL action cannot mutate brokerage state.
4. **Optional agents**: timestamp-bounded, evidenced LLM sentiment and adversarial critique, not a trading authority. Cache model, prompt, tokens, responses and as-of time; fail closed if history unavailable.
5. **Risk / portfolio veto**: check desired weights, equity, exposure, CVaR, trade risk, daily-loss policies, position counts, active orders, venue precision and stale evidence using the *canonical* risk constitution. `NO_TRADE` is first-class.
6. **Execution / OMS**: one canonical event-driven ledger; use a pinned NautilusTrader adapter if APIs, accounting and symbol semantics are validated, otherwise benchmark LEAN as an alternative. Do not operate two fill/accounting engines for the same test. Preserve current V52 reconciler and state-machine invariants.
7. **Evidence / governance**: immutable manifest (code SHA, branch, data SHA, model digest, venue, time window, cost policy, trial registry, test results, decision), test suite and research report.

### Interface boundary — portfolio intent, not executable order

`TargetAllocationIntent = {decision_id, model_version, data_as_of, decided_at, valid_until, evidence_available_at[], target_weights{symbol: signed decimal}, no_trade, research_mode}`.

The scientific adapter should validate times, finite weights, unit conventions, gross and per-asset caps, `NO_TRADE`, independent risk approval and risk evidence ID. It shall **not** create a broker order. A *separate*, future-reviewed OMS translator may create idempotent intent reservations only after durable approval. Current experimental contract in `research_bot/safer_trade_contract.py` is expressly research-only and does not replace `RiskEngine`.

Bridge requirements:
- freeze target symbol/product identity, side, contract multiplier, precision, margin mode;
- latest observable closed-bar time <= decision time; exchange quote time verified separately;
- transform target weights -> signed quantity only against reconciled equity, position and execution-side quote;
- order-intent ID deterministic; database transaction for reservation, fill ledger, net cash/position update;
- ambiguous remote submit => **reconcile**, no blind retry;
- maker/taker selection, partial fills, funding and fee currencies must be independently accounted for;
- stop-first execution semantics and venue-side stop evidence where supported;
- keep long-only spot, leveraged perpetual and MT5 contract paths distinct.

## 3. Integration stages (no mega-merge)

| Stage | Deliverable | Acceptance |
|---|---|---|
| P0 | ADR, risk-constitution conflict matrix, frozen lineage and executable inventory | exact file/commit, missing evidence identified |
| P1 | deterministic decision contract + negative tests, no network orders | PIT, nonfinite, expired, duplicated intent, NO_TRADE all fail closed |
| P2 | one engine benchmark adapter and one ledger, preserving V52 code | paired replay of same events; fill/fee/funding/reconcile invariants |
| P3 | four causal arms, B0/B1 challengers, frozen trial registry | closed-bar future-mutation tests, meaningful baselines |
| P4 | PPO sizing with frozen action/state/reward and multiple seeds | after-cost OOS significance and risk rule compliance |
| P5 | optional agent ablation, only with PIT historical text | cost/token and error-rate effect independently measured |
| P6 | shadow / paper validation and documented release decision | reproducible results; no promotion without gates |

Source provenance and blockers: issue #126; PRs #128-#132 for separate causal four-arm prototypes; PR #134 MT5-demo convergence; PR #136 isolated paper simulator; PR #113 and #124 governance/fixes. These are **not presumed merged or interchangeable**.

## 4. Preregistered reproducible evaluation

### Instruments and data
Main scoped comparison: BTCUSDT, ETHUSDT and SOLUSDT perpetuals on **one verified venue** with historical closed H1/H4 data; 1m trade/quote data when accessible for execution calibration. Funding timestamps and actual published fee schedule mandatory. If the selected venue's PIT sources are missing, flag `DATA_UNAVAILABLE`, do not fill gaps with invented microstructure. Preserve existing V51 sealed prospective evidence independently.

### Splits
Suggested, not yet frozen: initial training 2021-2023, development 2024-2025, sealed terminal test 2026-01 through 2026-09 **only where instruments have genuine data coverage**. Rolling/expanding walk-forward inside development; purge/embargo according to event horizon; no reuse of terminal test for tuning. For PBO: implement relevant CSCV with full trial registry; do not equate PBO to CPCV.

### Baselines and ablation
B0 four-school deterministic signal with identical portfolio controls;
B1 LightGBM with same signals/costs;
B2 B1 + PPO (several seeds) with independent hard risk veto;
B2a B2 with soft risk penalty only in sandbox, to quantify veto (never order-capable);
B3 B2 + TradingAgents historical, optional and predeclared.
Hold signal stream fixed while comparing simplified bar-fill versus calibrated event-based execution to isolate execution-model effect.

### Costs
Net return = gross trading PnL - venue fees - execution impact/slippage - *non-duplicated* spread cost - funding - borrowing/financing when applicable.
Execution-side ask/bid crossing already incorporates spread; do not double count. If historical maker/taker discounts or queue dynamics unknown, explicitly label scenario rather than 'observed real historical fees'. Sensitivity: 0 bps diagnostic only; 24, 36, 50 bps round-trip cost stress, with separate venue fee/funding breakdown.
Never treat paper fills as proof of market impact.

### Regimes
Causal classification using only past observations: up / down / range crossed with high / low volatility. Freeze thresholds in training. Report per-regime sample size/effective size, turnover, cost, net Sharpe, maximum drawdown, tail ES/CVaR, hit rate and changes in distribution.

### Inference
Paired daily after-cost returns; time-block bootstrap confidence interval for `Delta Net Sharpe` or mean utility; Holm correction for preregistered hypothesis family; DSR with correct trial count and PBO with real trial matrix; report seeds, effect sizes, non-significant and negative results. Small sample and dependence may prevent a definitive economic conclusion.

## 5. Dual acceptance: academic vs economic vs engineering

- **Academic success**: frozen hypotheses, real verified data, reproducible scripts and manifest, clearly reported uncertainty, independent test and negative findings retained. Does not require profits.
- **Economic promotion candidate**: net positive incremental improvement with confidence interval/statistical support, regime/cost resilience and no evidence leakage. Aspirational Sharpe>1.5, PF>1.3, PBO<.3 are not observed or sufficient alone.
- **Engineering readiness**: zero invalid/duplicate settlement, atomic rollback, persistent reconciliation, quote freshness/precision, predictable crash recovery, hard limit veto; PostgreSQL end-to-end and 90+ days paper/forward proof when required. Any failure => BLOCKED.
- **LIVE**: owner consent is acknowledged, but this research design cannot activate live trading or claim safety readiness. Separate operator/governance/promotion paths remain mandatory.

## 6. Academic capacity limit

One venue, three liquid symbols, H1/H4, one RL policy (PPO), one ML baseline (LightGBM), minimal GPU use, optional LLM only after baseline pass, no HFT or trained-from-scratch LLM, no claims of cross-market generalization. If L2 is absent, restrict to calibrated 1m/quote-side approximation and disclose capacity/queue uncertainty.

## 7. Evidence to produce (not yet produced)

`results/safer_trade/`: data-coverage.json, lineage-manifest.json, frozen-trials.jsonl, split-index.json, engine-replay.json, regime-metrics.csv, paired-statistics.json, cost-sensitivity.csv, safety-regressions.json, paper-observation-log, and final-review.md. **No such outputs are claimed here.**

## 8. Scientific sources and reference implementations
- FinRL-X: https://github.com/AI4Finance-Foundation/FinRL-Trading
- TradingAgents: https://github.com/TauricResearch/TradingAgents
- NautilusTrader: https://github.com/nautechsystems/nautilus_trader
- LEAN: https://github.com/QuantConnect/Lean
- Bailey & López de Prado, Deflated Sharpe Ratio; Bailey et al., Probability of Backtest Overfitting.
- Authoritative local issue: https://github.com/parsa314/modular-crypto-trading-bot/issues/126

**Evidence Before Opinion. No silent scientific lineage merges. No unverified performance claims.**
