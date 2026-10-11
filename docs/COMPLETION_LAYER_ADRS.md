# Section 2 completion layer — architecture decisions

Date: 2026-10-11. Engineering baseline: main
`fa31e6f5ceb09f4f14ae13fef50706f2e10e1835`, followed by reviewed Phase-1
`b66c421b154dc88f03dda75459999d2a2225ad1d` (PR #115).
These decisions establish an offline engineering implementation, not empirical
alpha or a change to any frozen experiment.

Step 3 extends execution assumptions in
[ADR-COMP-008](ADR-COMP-008_LOW_LIQUIDITY_EXECUTION.md), including corrected
supervisor ADR-003 inverse arithmetic, spot preflight and strict validation.

## ADR-COMP-001 — scope, contracts and journal

**Problem:** Section 2 requests PAPER simulation while the deployed service and
v0.51 remain locked. The asserted `complete_demo_bot`/`TradeJournal` source is
absent from the supplied files and audited GitHub trees.

**Options:** modify global execution flags; shadow existing flat modules with
packages; compose a separately scoped simulator with an additive journal.

**Evidence for/against:** `contracts.py` and `execution.py` are existing module
paths; replacing them could silently redirect imports. No inspected demo schema
supports a claim of exact compatibility. SQLite transaction and restart tests
support the independent implementation; they cannot establish unknown-schema
compatibility.

**Decision:** use `research_bot/completion/`; preserve immutable
`HypothesisCandidate` fields and add only new enum members. Store scenario
economics and plans as immutable metadata. Use Decimal for financial quantities,
the original DecisionEngine at its dimensionless float boundary, and independent
`completion_events`, `completion_checkpoints`, `completion_first_seen` tables.
Use WAL/FULL durability, nested savepoints and memory rollback around mutations.
Operational events never masquerade as the scientific milestone ledger.

**Trade-offs:** a new journal adapter must be verified against the external demo
when its source arrives. The reference implementation is single-process and
does not promise distributed exactly-once execution or load-tested throughput.

**Impact:** original execution/risk/service/DecisionEngine and frozen v0.51 are
unchanged. Enum consumers with exhaustive matches must explicitly abstain from
unrecognized new families/sides.

**Acceptance:** all new generators return only HypothesisCandidate; no financial
transport exists; event/checkpoint integrity, nested rollback and exact restart
pass. Global service firewall checks still pass.

**Rollback:** remove completion imports/package/scripts/tests and restore enum
additions and navigation/workflow changes. Keep SQLite artifacts and negative
results for audit; never silently drop user tables.

## ADR-COMP-002 — Grid proposal state

**Problem:** the course's order-id/polling logic conflates disappeared orders with
fills and can sell inventory that has never been acquired (course Python_6:14).

**Options:** port its order loop; derive stateless grid prices only; keep a pure,
checkpointable proposal machine with external authoritative inventory.

**Evidence for/against:** explicit fill events distinguish cancellation,
rejection, expiry and partial execution; proposal reservations avoid committing
the same capital repeatedly. A grid distance is not a calibrated forecast.

**Decision:** arithmetic or Decimal geometric spacing, tick/lot rounding, local
PLANNED reservations, simulator-owned SUBMITTED reservations, terminal release
only. Out-of-range observations suspend hypotheses and request simulated
cancellation through the runner. Recenter after distinct observations only,
once all old reservations are released; preserve proportional band width.
Inventory skew affects assumed mean-reversion economics, risk and confidence.

**Trade-offs:** no optimal grid spacing, queue position or adaptive alpha is
claimed. Recenter can remain suspended indefinitely if reservations persist.

**Impact:** a pure generator does no I/O and never submits/cancels orders. First
seen records are registered by the runner before generation and passed as data.

**Acceptance:** empty/full inventory, arithmetic/geometric levels, exhausted
capital, snapshot deduplication, persistent state, terminal-release recenter and
repeated polling are tested in the completion strategy suite.

**Rollback:** remove Grid from StrategyBundle; release/cancel outstanding
simulated requests through confirmed lifecycle events, retaining their journal.

## ADR-COMP-003 — depth-aware non-atomic triangular hypotheses

**Problem:** top-of-book cross rates and three immediate orders overstate the
course opportunity (Python_6:18).

**Options:** ideal mid graph; atomic exchange transaction assumption; directed
graph plus depth walk with explicit sequential failure paths.

**Evidence for/against:** book quantities bound size; fees in the received asset
change each next-leg quantity. Three REST orders do not form an atomic trade.
Snapshot rates cannot establish persistent executability.

**Decision:** two directed edges per pair, any three-distinct-asset cycle, Decimal
VWAP depth walk/lot rounding, fees every leg, slippage buffer, explicit non-atomic
and residual-risk assumptions. A bounded halving search finds a conservative
feasible size; it does not claim maximum size. Emit only positive net assumed
edge above threshold. Log sampled opening/closure duration, hypothetical
half-fill/rejection paths, and observed simulator failure counts separately.

**Trade-offs:** risk penalties are assumptions, not fitted probabilities. Lot
dust can remain; outward-rounded protection reservations can exceed VWAP input
cost and be rejected by hard capital checks. Historical backfill is context.

**Impact:** no order API; proposed sizes are recomputed from actual receipts by
the simulator before later legs. Original overlap_arbitration_v51 is untouched.

**Acceptance:** independent reference arithmetic, depth/VWAP/fees, conservative
resizing, generalized cycle, timestamp skew, stale data, threshold, opportunity
decay and failure-scenario metadata are exercised.

**Rollback:** remove the generator from the bundle; preserve non-atomic residuals
and apply only bounded simulated unwind to existing simulated cycles.

## ADR-COMP-004 — event-driven execution and residuals

**Problem:** order placement acknowledgements are not fills; latency, shared
liquidity and failed later legs materially change inventory.

**Options:** same-close fills; mutate the original simulator; compose an
independent event queue with durable state transitions.

**Evidence for/against:** unit reference books establish received-currency fees,
partial fills and capital conservation. Modeled depth cannot establish real
maker queue position or fill likelihood.

**Decision:** CREATED, APPROVED, SUBMITTED_SIMULATED, ACKNOWLEDGED,
PARTIALLY_FILLED, then FILLED/CANCELLED/REJECTED/EXPIRED. Seeded hash jitter,
configurable submit/ack/cancel latency, no decision-snapshot fill, lot/tick/minimum
notional enforcement, reservations, shared consumable snapshot depth, and strict
client-order-id payload identity. Fee/slippage costs alter actual balances.
SequentialCycleSimulator advances a leg only after FILLED and sizes it from net
receipts. Failure records residual flows; direct-book emergency unwind remains
a separate latency-bound, constrained simulation and can itself fail.

**Trade-offs:** displayed liquidity crossing the limit is the fill model; passive
queue, hidden depth, own-market impact and real exchange behavior remain untested.
The configured fee asset is a model assumption to verify per venue.

**Impact:** every state/fill is written to the shared additive TradeJournal with
balances and reason. Terminal transitions release reservations; a cancel request
alone does not. No live order endpoint is implemented.

**Acceptance:** exact lifecycle, shared-depth partial fills, no naked sells,
latency/cancel race, idempotency/revision rejection, deterministic restart,
storage rollback, sequential receipt sizing, failures/residuals/unwind pass.

**Rollback:** stop advancing the simulation and retain its journal/checkpoints;
disable the new runner without interpreting open simulations as real positions.

## ADR-COMP-005 — portfolio conflicts and existing decision boundary

**Problem:** simultaneous hypotheses compete for capital and correlated exposure;
v0.51 is frozen and is not a general portfolio engine.

**Options:** modify v0.51; independent score-only sorting; independent ranking
with hard risk/capital gates and an operational record for every decision.

**Evidence for/against:** existing DecisionEngine/RiskLimits define the concrete
available contract. No supplied RiskGate source supports exact demo integration.
Missing correlations do not prove independence.

**Decision:** net scenario edge, recent-hypothesis uniqueness, PIT regime fit,
capital efficiency and supplied correlation penalty determine rank. Hard cash,
turnover, gross/asset exposure, drawdown/CVaR, spread/slippage and position caps
are no weaker than canonical RiskLimits. Aggregate batch reservations prevent
double spending. The original DecisionEngine returns SignalEvidence; only
simulation-approved HypothesisCandidate values reach the new simulator. RSI+MA
and EMA baselines without calibrated/sized economics abstain with an explicit
reason rather than receiving invented forecasts.

**Trade-offs:** default regime contribution is zero and absent correlations have
zero score contribution; hard concentration remains enforced. Scores and grid
confidence are heuristic scenario assumptions. This is not optimal allocation.

**Impact:** independent namespace and SQLite ARBITRATION events, new rejected
reason codes, no changes to existing risk limits or frozen experiments.

**Acceptance:** ranking inputs, hard-limit rejections, sell reductions,
duplicates, PIT context, recovery and memory rollback are verified.

**Rollback:** remove the independent arbiter/runner entry point, retain operational
rejection records; never redirect its output into v0.51.

## ADR-COMP-006 — replay, statistics, graduation and restart

**Problem:** synthetic engineering results and favorable selected trials cannot
justify real-data PAPER research or a thesis alpha claim.

**Options:** auto-promote on positive Sharpe; reuse frozen experiment decisions;
independent fail-closed gate over the exact chronological costed replay paths.

**Evidence for/against:** existing ValidationHarness provides purged fixed-path
CPCV selection, CSCV/PBO and DSR diagnostics with support checks. It does not refit
stateful strategies or validate unknown fill queues. IID Sharpe alone does not
establish dependence-robust edge.

**Decision:** identical StrategyBundle, lifecycle simulator and journal schema in
BACKTEST/PAPER; causal observations and next-snapshot execution. OHLCV-only data
receives explicitly synthetic depth. Equity/MTM returns, FIFO inventory-reduction
PnLs, Sharpe with declared annual sampling, drawdown, empirical CVaR, win rate,
profit factor and duration-weighted uniqueness are reported. Undefined values
are null. Complete externally replayed trial matrices must match the selected
run exactly to produce DSR/PBO; a single short run receives no fabricated scores.

Research graduation requires frozen preregistration/data/configuration/execution
hashes, all tried paths, locked OOS and independently validated execution model,
positive net performance, clean original prospective first-seen coverage,
DSR strictly above threshold, PBO below its limit, and positive moving-block
confidence bound with one-sided p<0.05. Model/strategy changes invalidate the
certificate. Synthetic engineering demonstrations can never graduate.

**Trade-offs:** defaults require at least 120 variable observations and two unique
trial paths; this minimum is not a claim of adequate economic sample size. DSR
uses existing per-period implementation, not annualized Sharpe or a replacement
for OOS. Trial completeness and venue-verified source truth require human/source
audit; hash/flag checks cannot independently prove them. IRT/USDT cash valuation
is explicit; depeg/FX stress is a separate protocol.

**Impact:** snapshots preserve simulator/bundle/arbiter/cycles/FIFO accounting,
including seed and policy. Changed initial balances/configuration or revised
frames fail closed. Different run modes use distinct journal namespaces; a
verified gate certificate may be copied to a new PAPER namespace for the same
frozen configuration, not used to relabel the original BACKTEST events.

**Acceptance:** one-shot equals close/reopen continuation; four strategy sources
are recorded; two real synthetic replay paths support diagnostic execution;
short/incomplete/tampered/negative/historical/synthetic paths fail graduation.
Positive statistical gate tests are numerical fixtures, not a graduated strategy.

**Rollback:** revoke/replace the independent graduation checkpoint with
paper_allowed=false, stop the runner and preserve all evidence and negative runs.

## ADR-COMP-007 — Bitpin read composition

**Problem:** Section 2 prefers public/read-only wallet access without new order
code; current API hostname/schema/fees cannot be assumed from course notebooks.

**Options:** copy notebook REST calls; implement a second financial client;
compose the previously tested explicit host/method allowlist.

**Evidence for/against:** fixture tests cover Decimal data/stamps, bounded retries,
auth errors and blocked writes in BitpinAdapter. They do not prove live schema
compatibility or that api.bitpin.org always replaces api.bitpin.ir.

**Decision:** BitpinReadOnly exposes public ticker/orderbook and authenticated
balances only. Caller explicitly selects an allowed host in BitpinAdapter; there
is no automatic fallback or new place_order/cancel_order API. Receipt-only
observations do not claim EXCHANGE_VERIFIED prospective evidence.

**Trade-offs:** opt-in auth read requires keys/source contract verification. No
live network or account check is included in this offline validation.

**Impact:** reuse existing tested transport and EvidenceStamp; no credential,
wallet output, original notebook or raw exchange response is published.

**Acceptance:** fixture public reads and fail-closed authenticated read without
keys pass; no order/cancel API exists on the new facade.

**Rollback:** remove the optional read facade from callers, preserving captured
source provenance and the original adapter firewall.
