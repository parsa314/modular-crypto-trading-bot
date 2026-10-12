# Advanced, executable upgrade prompt for this repository

Target: parsa314/modular-crypto-trading-bot; active research PR115. Version TEN-UPGRADE-1, 2026-10-12.
Objective: test ten frozen rules and build an auditable path from causal market events to calibrated, cost-aware hypotheses. Maximize reproducible evidence, not model size or backtest Sharpe. Ten profitable strategies are not an acceptance condition; ten correctly tested hypotheses are.

## Role and binding scope

Act as quantitative scientist, Python architect, statistical auditor and risk engineer. Apply repository AGENTS.md and current canonical navigation. First inventory the exact branch/main SHAs, source paths, dependencies, tests, frozen preregistrations, datasets, trial history and existing execution transports. Distinguish the canonical repo from miladchicomobot public shell. Read the supplied complete_demo_bot migration report and current R1/ten ADRs. Do not claim complete manual review based only on directory names.

Governance: RESEARCH_ONLY=true, PAPER_EXECUTION=false, LIVE_EXECUTION=false for this challenger. No real orders, endpoint activation, secret handling, larger risk ceilings, implicit IRT funding, deployment migration, or frozen v0.51 changes. Hypothesis generation stays uncalibrated until validated; preserve HypothesisCandidate, SignalEvidence, DecisionEngine, RiskGate and TradeJournal contracts. Existing TEN_REPLAY_* is hypothetical OHLC accounting, not actual paper execution or a depth/queue simulator.

Each decision record must include problem, options, direct evidence, chosen design, trade-offs, affected interfaces, financial/security implications, acceptance tests and rollback. A reference title/DOI is not verified full-method evidence. Negative findings are deliverables.

## M0 — Audit and bind evidence before changing an experiment

Map every requirement to observable behavior, test and current result. Hash protocol, complete dataset, code/runner and search ledger before evaluation. Record already inspected data windows and informal/discarded searches. Do not retroactively preregister the earlier BTC history or synthetic results.
Outputs require data_class, evidence_state, available_at_basis, input/source/protocol hashes, complete trial IDs, cost_model_id and exact DSR/PBO status/reason.
Completion: no unknown binding is silently defaulted to clean/true; no blocked historical invocation becomes synthetic.

## M1 — Make structural events first-class

Use immutable event IDs and two clocks: origin/confirmation/availability. Retain OB/FVG creation, touch, mitigation, invalidation and expiry for all active bands. Model views have age, causal distance, missing flag and version. Keep a trade's zone reference fixed at entry; never rebind it to a later winning zone.
Tests: whole-stream vs each prefix, perturb future suffix, chunk/restart replay, tied pivots, delayed swing confirmation, zero ATR/range, gap-created-now, partially/fully mitigated FVG and deterministic same-time conflicts. Verify higher context only from complete UTC4h buckets.
Innovation proposal: represent structural history as an event graph whose nodes carry availability and lifecycle, then use its deterministic numeric snapshot as a tabular baseline. Sequence/event embeddings are later comparators, not mandatory infrastructure.

## M2 — Data and execution-cost observability

Keep BTC_USDT/1h/Binance source fixed initially. Official public archive ingestion must verify provider checksum, unit switch, full monthly continuity, canonical close and explicitly modelled backfill latency. Separately preregister a future first-seen read-only collector with reception timestamps; never merge its status with historical backfill.
Develop cost feasibility surfaces from observed spread/depth, fee tier, latency and requested size. Parametric square-root impact is a declared approximation until independently calibrated. Calibrate on development only; frozen stress scenarios include worse costs and lower fill fractions.
Innovation proposal: an opportunity is admissible only if its lower confidence edge exceeds an upper confidence cost estimate across a declared size range. This contracts trade sizes or abstains; it never raises existing limits.
Acceptance: measured input lineage and out-of-support abstention; no future depth or fee tier inferred from a successful backtest.

## M3 — One probabilistic baseline before an ensemble

Fit train-only transformations and LogisticRegression(C=1), then independent chronological calibration, then untouched test after outcome availability and embargo. Keep raw probabilities/base rate and fixed isotonic comparisons. Do not select a calibrator or PCA setting from the final test.
Forecast gross conditional return distribution and event resolution probability separately; estimate mean/uncertainty from prior outcomes, not score times a made-up coefficient. ECE/Brier/log loss evaluate prediction; economic equity returns evaluate strategy.
Innovation proposal: competing-risk outcomes for continuation, adverse invalidation and timeout, with prediction available only after fit/calibration outcomes. Compare this to the binary baseline before additional complexity.
If sequence model is justified, compare one small causal GRU/TCN on windows ending before decision; LSTM variance head uses positive variance plus a floor and correctly named outputs. Stacking meta-learner uses chronological OOF base predictions and a separate calibration block. No same-test model/threshold/feature selection.

## M4 — Economic replay and valid selection inference

Implement exactly S1-S10 from ADR-TEN-01. Resolve any parameter change as a new registered trial, retaining old outcomes. Produce hourly synchronized net-equity T x10 with idle periods, bounded funding, one position per isolated account and terminal residuals. Baseline comparisons must match capital/exposure/costs; a fully invested BTC benchmark is not a fair cash-drag comparison to a 10%-exposure rule.
Improve the scenario replay through composition with the existing event simulator only after synchronizing book availability, latency, consumed depth, partial fills and non-atomic residual/unwind assumptions. Signal timing and accounting must match historical and future read-only paths.
Statistics: paper-referenced CSCV/PBO ranks and ties; sample support and block-boundary guards; DSR on net returns with all search trials/dispersion and declared dependent-trial/temporal-dependence treatment. Family-only diagnostics must not masquerade as full-search significance. Degenerate/sparse columns remain in the inventory and block unsupported inference.
Innovation proposal: paired block inference against an exposure/cost-matched baseline, with a separately reviewed Reality Check/SPA-style family screen. Keep this distinct from PBO and DSR; no arbitrary p-value renamed DSR. Freeze family correction, block length, support and thresholds before results.
Completion: independently hand-calculated cashflow fixtures, double-touch ambiguity, stressed cost/depth runs, no-profit negative control and synchronized return hashes.

## M5 — Portfolio arbitration as a separate experiment

Rank validated hypothesis distributions by conservative net edge, incremental diversification, capital efficiency and regime support. Estimate dependence from prior data only; keep shared triggers/overlapping intervals visible. Independent columns are not a combined portfolio.
Innovation proposal: select incremental risk-adjusted hypotheses conditional on existing exposure, with uncertainty widening when detector families agree because they share the same price information. Compare against frozen no-preemption/earliest-first control only in a new experiment; never reopen or silently alter overlap_arbitration_v51.
Acceptance: allocations remain within the stricter existing limits, exact Decimal funding/reservations, explicit rejection reasons, deterministic journal/replay and no automatic limit inflation.

## M6 — Prospective evaluation and promotion

Predefine a genuinely future calendar, admissible reception delay, read-only source, complete strategy/code identities and stopping rules. Register all missed/late bars as such; no retrospective rescue. Evaluate on a separate ledger; sealed Kraken remains sealed.
Innovation proposal: a reviewed sequential monitoring protocol with alpha-spending/always-valid inference to prevent repeated-peeking bias; it needs explicit assumptions and calibration before use. OOD detection and uncertainty may only abstain/reduce exposure. A drift alarm does not authorize automated retraining on the holdout.
PAPER graduation requires positive net economics, adequate search-adjusted inference, first-seen integrity, risk/microstructure validation and an explicitly recorded governance decision. Passing a synthetic test or family diagnostic cannot promote. LIVE remains out of scope.

## Deliverables and acceptance

Provide source diff, frozen variant/protocol table, actual T x10 CSV, JSON/Markdown reports, detector/hypothesis/transaction journal, hash manifest, primary-source matrix and thesis mapping. Run focused causal/accounting tests, persistence/restart, repository regression and CI. Report count/coverage honestly; coverage is not scientific evidence.
Maintain a status table CONFIRMED/PARTIAL/UNVERIFIED/FAILED and preserve all negative results. Every blocked data/inference path has a reason code. Return a practical next command and the exact remaining gate. No profit guarantee, production-ready label, claimed institutional edge, or unverified first-of-kind novelty.
