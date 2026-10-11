# ADR-COMP-008 — Step 3 and corrected supervisor ADR-003

Date: 2026-10-11. Status: **accepted for offline engineering; empirical validation
NOT_RUN**. Parent: `34983f8d94ece4659515e83c4bb4d9a8964f1218`, PR #115.

## Problem and evidence

The previous simulator consumed all displayed, price-crossable depth and modeled
individual latency without shared submission pacing. The supervisor's ADR-003
adds impact/exit checks, but its code mixes float money, unspecified volatility
units and an incorrect inverse formula. Its BID selector routes to asks,
`max_spard` loses the spread reason, and SELL exposure assumes a short instead of
reducing a spot long. No supplied measurement supports the $50k venue-depth
claim or a universal "conservative" k=0.25.

Primary-source context:

- Almgren & Chriss, *Optimal execution of portfolio transactions* (2001),
  [doi:10.21314/JOR.2001.041](https://doi.org/10.21314/JOR.2001.041): balances
  volatility risk and permanent/temporary costs. Its simple linear model does
  not validate a universal crypto square-root law.
- Zarinelli et al., *Beyond the square root* (2014/2015),
  [arXiv:1412.2152](https://arxiv.org/abs/1412.2152), abstract: US equity
  metaorder evidence finds square-root fit over a limited size range. These
  observations do not establish Bitpin/Nobitex parameters or fill probabilities.

## Options, decision and trade-offs

1. Fixed buffer + 100% visible depth: simple but optimistic; retained only as an
   explicit reference setting for fee-conservation comparisons.
2. Full queue reconstruction/calibrated impact: requires unavailable message
   data, own execution outcomes and a verified venue contract.
3. Shared depth haircut + simulated pacing, with a separate Decimal cost and
   liquidity preflight: selected as a testable engineering stress model.

The default simulator uses **10% of each displayed level**, shared once across
all eligible orders per snapshot, and **100ms minimum simulated submission
spacing**. These are assumptions, not measured rates/probabilities. ACK latency
starts after actual paced submission. A snapshot observed before ACK/not-before
cannot fill when delivered later. Duplicate books progress timers without
replenishing depth. Checkpoints persist pacing slots and ACK times; rollback
restores both. This models neither maker queues nor public-feed HTTP limits.

Pydantic validates external stress settings, forbids unknown fields, and rejects
float/boolean/nonfinite financial input. Domain dataclasses remain unchanged.
Transition events include policy hash/settings; SIMULATED_RATE_LIMIT records
delayed submission and retry time.

## Correct cost and liquidity preflight

`execution/impact_estimator.py` uses Decimal, daily-return standard deviation
sigma24h, quote notional Vquote over the same trailing 24h, and explicit k:

```text
impact_bps = 10000*k*sigma24h*sqrt(Qbase*Pquote_per_base/Vquote)
Qmax_base = (Vquote/Pquote_per_base)*(budget_bps/(10000*k*sigma24h))^2
```

V/P occurs once. P=100, V=10000, sigma=.02, k=1 and target=20bps permits
**1 base unit**, not the pasted code's 100. Lot/minimum notional, participation
ceiling and positive post-cost edge are rechecked. `remaining_edge_bps` already
deducts fees/spread/other costs. Undefined estimates are null, never infinity;
zero statistics abstain. Incorrect annualized input masquerading as daily data
cannot be detected numerically; callers must verify units/source window.

`execution/liquidity_gate.py` reuses immutable stamped OrderBook. BUY consumes
ASK; SELL consumes BID. Top-N depth is bounded by a 1% mid-price band and optional
entry protection. Base units determine consumption/coverage; actual quote depth
is logged. BUY checks BID coverage of existing plus acquired inventory. Spot
SELL requires holdings and reduces them; it does not invent ASK-cover exposure.
SELL still enforces entry/spread limits. A buffer cannot guarantee future exit.
The 25bps impact and 35bps spread ceilings never override a stricter RiskGate.

`execution/cost_preflight.py` records full book, stamps, statistics, parameters,
lot rules, edge/size and both results as EXECUTION_COST_PREFLIGHT in the shared
journal. Only OK + APPROVED passes. REDUCE requires a new sized
HypothesisCandidate and risk arbitration; it never mutates or executes an order.
Independent risk approval remains required. No automatic calibration or limits
inflation is implemented.

**Integration boundary:** the four-generator runner actively uses depth/pacing.
It does not invent missing daily statistics to activate square-root preflight.
Fills still use book walk, received-currency fees and configured slippage. Do not
double-count VWAP/spread and parametric impact without a validated residual-cost
model. Explicit callers can compose preflight before simulator submission:

```python
from decimal import Decimal
from research_bot.jamalian_data import currencies
from research_bot.completion.execution.impact_estimator import SquareRootImpactEstimator
from research_bot.completion.execution.liquidity_gate import DepthAwareLiquidityGate
from research_bot.completion.execution.cost_preflight import ExecutionCostPreflight

preflight = ExecutionCostPreflight(
    SquareRootImpactEstimator(k_coefficient=Decimal("1")),  # Explicit SCENARIO.
    DepthAwareLiquidityGate(), journal,
)
base, _ = currencies(request.symbol)
result = preflight.assess(
    assessment_id, market_snapshot, stamped_book, request.side, request.quantity,
    remaining_edge_bps, simulator.balances().get(base, Decimal(0)), request.created_at,
    request.rules, request.protection_price,
)
simulator.submit(request, approved=(existing_risk_approved is True and result.passed))
```

Inputs and approval must come from the existing hypothesis/risk flow. This seam
does not change DecisionEngine, RiskGate or hypothesis schemas.
Exit coverage uses total held inventory, including reserved holdings. The
simulator's independent free-balance check prevents selling reserved inventory.

## Impact, acceptance and rollback

- Source stays under `research_bot/completion/execution/`. Existing service,
  frozen v0.51, risk limits and deployment routing retain their gates. Pydantic 2
  is a direct dependency; Python minimum remains 3.10.
- Acceptance: independent inverse reference, currency unit scaling, positive
  edge after reduction, strict input, BID/ASK, spot exposure, stale evidence,
  shared partial fills, pacing/ACK/cancel/expiry, duplicate books, storage
  rollback and exact restart pass. Every completion module must retain >80%
  executable statement coverage; branch coverage is not claimed.
- Changed policy hash invalidates old checkpoints/graduation certificates.
  Use a new SQLite file/namespace; retain the old negative evidence. No automatic
  schema downgrade or retrospective relabeling.
- Rollback: restore reviewed parent 34983f8 on an isolated branch and its original
  configuration/journal; retain new records. Ablation uses fraction=1, spacing=0
  in a separate namespace; it does not imply calibrated realistic execution.
- Follow-up: preregister parameter sensitivity, obtain verified venue data,
  compare fill/cost errors on held-out prospective observations, and repeat
  costed validation/graduation. Stress tests are not alpha evidence or proof
  Sharpe must fall. There is no LIVE route.

Example (new journal):

```bash
python -m scripts.run_completion_paper --journal /tmp/step3-paper.sqlite --cycles 80 \
  --displayed-depth-fraction 0.10 --min-submission-interval-ms 100
python -m scripts.run_completion_paper --journal /tmp/step3-paper.sqlite --cycles 90
```
