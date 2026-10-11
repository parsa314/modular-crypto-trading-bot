# Jamalian completion layer — audit, operation and migration

Date: 2026-10-11. This implements the owner's **MASTER ENGINEERING PROMPT,
Section 2**, after the course audit and V3 correction in PR #115. Engineering
baseline is main `fa31e6f5ceb09f4f14ae13fef50706f2e10e1835`; Phase-1 parent is
`b66c421b154dc88f03dda75459999d2a2225ad1d`.

## Audit findings and evidence limits

Five supplied notebooks were statically audited, not executed. Detailed cell
locations, hashes, write-path hazards, optimistic fill assumptions and research
references remain in [course report](JAMALIAN_INTEGRATION_REPORT.md) and
[V3 review](JAMALIAN_V3_REVIEW.md). Python_1 and external notebook config/data
files were not supplied. Private notebook outputs/credentials were not published.
The later Section 2 prompt is available; the earlier original §14 is not.

The actual repository has flat `contracts.py`/`execution.py`, DecisionEngine,
RiskEngine/RiskLimits and a guarded PaperExecutionEngine. The asserted
`complete_demo_bot`, its `main.py`, RiskGate and original TradeJournal schema
were not found locally, in the attachments, on main, or in the audited candidate
branch trees. The follow-up response described a completed Grid implementation
and 80 PAPER cycles but supplied no source/branch identifying that demo. Those
claims are not substituted for inspection. Exact compatibility with that external
demo is **UNVERIFIED**; the new implementation provides an explicit migration
seam and a tested reference journal instead of rewriting an unknown schema.

The Phase-1 three-baseline economic gate remains NOT_RUN. Section 2 explicitly
authorizes new advanced **engineering simulations**; it does not declare Grid,
arbitrage, RSI or EMA scientifically graduated, profitable, university-approved
or ready as empirical thesis conclusions.

## Governance and runnable package

New layer: `RESEARCH_ONLY=true / PAPER_EXECUTION=true / LIVE_EXECUTION=false`.
It has no order transport or live mode. This scoped authorization is documented
in AGENTS.md. Original service environment flags, original hypothesis abstention,
frozen overlap_arbitration_v51, scientific milestone ledger, sealed holdouts and
deployment routing retain their gates.

The requested paths live below `research_bot/completion/` to avoid shadowing
existing flat modules:

| Requirement | Concrete module | Observable behavior |
|---|---|---|
| A | strategies/grid_hypothesis.py | Inventory-constrained BUY/SELL HypothesisCandidate; arithmetic/geometric levels; reservations; suspend/recenter |
| B | strategies/triangular_arb_hypothesis.py | Directed graph, Decimal depth walk, three-leg fees/slippage/risk, sampled decay, hypothetical failure paths |
| C | execution/event_driven_simulator.py + multileg.py | Latency/partial fills, lifecycle events, real simulated inventory, idempotency, sequential legs/residual/unwind |
| D | portfolio/conflict_arbiter.py | Ranked approved candidates, reason-coded rejects, canonical-limit ceilings, operational event logging |
| E | backtest/runner.py + metrics.py + graduation.py | Same costed simulator/schema, deterministic replay/resume, diagnostic metrics and fail-closed graduation |
| F | adapters/bitpin_readonly.py | Composition facade with public/authenticated read ports; no new order/cancel API |
| G | scripts/run_completion_paper.py | Four generator sources, arbitration and SQLite PAPER journal |

All money, book quantities, prices, scenario edge and portfolio scores use
Decimal. Existing SignalEvidence/DecisionEngine accept floats for dimensionless
forecast inputs; statistical routines use numeric arrays. This compatibility
boundary does not convert monetary balances/prices to float. HypothesisCandidate
schema stays immutable; scenario economics/plan values are immutable metadata.
Only enum families/sides are extended.

## Example and verification commands

Run from repository root with the declared core dependencies:

```bash
python -m scripts.run_completion_paper --journal /tmp/completion.sqlite --cycles 80
python -m scripts.run_completion_paper --journal /tmp/completion.sqlite --cycles 90
python -m scripts.verify_completion_coverage --output /tmp/completion-coverage.json --threshold 80
python -m unittest discover -s tests -p 'test_jamalian*.py' -q
python -m compileall -q research_bot scripts
```

The second call replays known frames idempotently and continues from the durable
checkpoint. Revision, changed initial balance/policy/bundle, backwards time and
different annual sampling/quote valuation fail closed. To start independently,
use a different SQLite file or independent experiment namespace; never erase
the earlier event trail to make a replay appear clean.

The synthetic 80-frame example records RSI+MA, EMA Cross, Grid and triangular
arbitrage hypotheses and arbitration decisions. Grid/arbitrage scenario values
are assumptions. Baseline indicator events do not contain calibrated/sized
economic forecasts, so the arbiter explicitly rejects them with
`UNCALIBRATED_OR_UNSIZED_HYPOTHESIS`; their hypotheses still appear in the journal.
This is four generators operating together, not proof of four approved strategies.

Observed engineering run: 30 simulated fills, 1,673 operational events, eight
arbitrage cycles expiring, net total return about **-0.22565%**. These synthetic
negative outcomes are retained. Annualized one-second Sharpe is only a declared
sampling diagnostic, not evidence of annual economic performance. A close/reopen
80→90 continuation matched a cold 90-frame run exactly, including balances,
metrics and records. Fresh final counts/coverage are recorded with the delivery
verification artifact; code changes require rerunning the commands above.

The completion suite checks all required Grid cases, depth/fees/cycles, full order
lifecycle, capital conservation/shared liquidity, cancel races, rollback/restart,
portfolio hard limits, first-seen immutability and graduation rejection. Coverage
uses stdlib trace executable statements without source exclusions and enforces
**strictly more than 80% in every new completion module**, not just an aggregate.
It is statement coverage, not branch coverage or economic validation. GitHub
main-integrity runs this gate alongside the entire existing pytest suite and
the existing execution-firewall checks.

## Operational SQLite records

`completion_events` has sequence, independent experiment_id, event_id, kind,
UTC timestamp, strict JSON payload and digest. Every order state transition logs
state/reason/fill/balances, PAPER or BACKTEST mode and LIVE=false. Other kinds:
FIRST_SEEN, HYPOTHESIS, ARBITRATION, OPPORTUNITY_DECAY, CANCEL_REQUESTED,
CYCLE_OUTCOME, UNWIND_REQUESTED/REJECTED/OUTCOME, TRADE_CLOSE, REPLAY_FRAME,
VALIDATION_DIAGNOSTIC and GRADUATION.

`completion_checkpoints` contains integrity-checked simulator, arbiter, cycles,
runner and graduation state. `completion_first_seen` records original source
digest/class/time; historical/synthetic events cannot be reclassified as
prospective. Nested savepoints and coordinated memory restoration prevent a
partially written frame from surviving a storage failure. This is an operational
research ledger; none of these records is a scientific milestone decision.

The reference journal is single-process. Raw/event/checkpoint hashes detect
accidental changes; they are not signatures authenticating an exchange or an
independent guarantee against a privileged database operator.

## Plugging into the real research orchestrator

The existing ResearchTradingOrchestrator offers a typed, explicit composition
entry point without changing its existing forecast or Phase-1 methods:

```python
from decimal import Decimal
from research_bot.orchestrator import ResearchTradingOrchestrator
from research_bot.completion.journal import TradeJournal
from research_bot.completion.models import CompletionPolicy
from scripts.run_completion_paper import build_bundle, synthetic_frames

journal = TradeJournal("/tmp/isolated-paper.sqlite", "my_completion_synthetic_v1")
try:
    runner = ResearchTradingOrchestrator().create_completion_runner(
        journal=journal,
        strategies=build_bundle(),
        balances={"USDT": Decimal(1000), "IRT": Decimal(1000), "BTC": Decimal(1)},
        policy=CompletionPolicy(),
        quote_currency="IRT",
    )
    result = runner.run(synthetic_frames(80))
finally:
    journal.close()
```

This runner owns an independent canonical DecisionEngine and Decimal hard gate
capped by existing RiskLimits. It does not reuse a potentially customized
orchestrator instance's thresholds or pass scenario values to process_forecast.
Do not register these strategies in frozen v0.51, relabel scenario economics as
calibrated alpha or route their metadata plans to any real exchange executor.

## Migrating an externally supplied complete_demo_bot

Once the missing demo source exists, inspect its actual dataclasses, identifiers,
fee asset, RiskGate and journal transaction semantics before claiming compatibility.
Keep its SignalEvidence/DecisionEngine/RiskGate/TradeJournal contracts in place.
Use a composition adapter or bridge to map confirmed simulated events, not a
rename of unknown columns. `JournalPort` describes append/checkpoint/restore;
the reference simulator additionally needs atomic transaction/events semantics,
and FirstSeenRegistry uses the additive SQLite tables directly. An arbitrary
three-method object is not a drop-in transactional journal.

For an existing SQLite journal file, reference TradeJournal adds only its three
namespaced tables; it does not alter/drop original tables. Map event_id,
timestamp, symbol, side, state, fill quantity/price, fee/currency, balances and
rejection reason into the external journal through an explicitly reviewed adapter
if dual recording is needed. Verify transaction boundaries and restart against
the external schema; the absent schema prevents performing that check now.

Do not infer fills from missing order IDs. Feed authoritative simulated inventory
into Grid and confirmed lifecycle events into terminal release. Feed later-leg
arbitrage sizing from **actual net receipts**, never the original theoretical size.

## BACKTEST → research PAPER protocol

1. Preregister comparators, all parameter trials, dataset/window hashes,
   exits/sizing, validated venue fee/lot/tick/minimum rules, fill-model limitations,
   dependence horizon/embargo, DSR/PBO thresholds and rejection criteria.
2. Run every tried configuration chronologically using the same bundle/simulator;
   keep costed equity-return paths and a complete trial inventory. Historical
   OHLCV depth remains labeled synthetic. No retrospective first-seen creation
   can make historical evidence prospective.
   BACKTEST refuses to create new prospective registrations; a prospective replay
   needs the original captured records already present in its independent ledger.
3. `runner.diagnose_trials(matrix, label_ends, selected_trial)` requires the selected
   column and timestamps to exactly match that runner's journaled net returns.
   It reports fixed-path purged CPCV, CSCV/PBO and per-period DSR. At least two
   variable trial paths and 120 observations are required by the existing harness;
   insufficient/ambiguous support fails rather than returning a convenient score.
4. GraduationPipeline additionally verifies net positive performance, original
   clean EXCHANGE_VERIFIED prospective coverage, locked OOS, independently
   validated fill model, frozen preregistration/data/strategy/execution hashes,
   DSR > threshold, PBO <= limit, and positive dependence-aware block inference.
   Operator declarations require source audit; booleans/hashes alone cannot prove
   complete trial disclosure or a trustworthy venue timestamp.
5. Record the decision as GRADUATION. A later PAPER namespace may receive the
   intact, audited certificate for identical frozen configuration and execution
   model, while keeping the original BACKTEST trail. Instantiate PAPER with
   purpose=`GRADUATED_RESEARCH`; unmatched certificate/configuration or unapproved
   strategy ids fail closed. No live transition exists.
   Research PAPER accepts only fresh, verified clean prospective observations;
   receipt-only, historical and synthetic source classifications fail closed.

No real strategy has passed this gate. Tests exercising the positive branch use
controlled numerical fixtures and synthetic stamps; they are not empirical
prospective evidence. The optional Bitpin adapter's receipt-only timestamps are
insufficient to claim EXCHANGE_VERIFIED source evidence. Current Bitpin API
schema, primary host, fee asset and precision rules require a separate public/read
contract check; no live account or network request was used in this verification.

## Remaining modeling limits and rollback

Displayed-depth crossing does not model passive queue position, hidden liquidity
or own market impact. Halving finds a conservative feasible arbitrage size, not
an optimum. Sampled decay does not prove continuous executability. Failures and
unwind are non-atomic; residual dust and losses may remain. IRT/USDT are explicit
cash currencies and need independent FX/depeg stress before economic promotion.
Confidence/risk penalties are assumptions. Large replay/stream throughput and
distributed recovery have not been benchmarked.

Architecture choices, acceptance and per-component rollback are in
[ADR-COMP-001 through 007](COMPLETION_LAYER_ADRS.md). Stop the independent runner,
disable its entry point and revoke its independent certificate to roll back.
Preserve journal evidence; original global service gates remain fail-closed.
