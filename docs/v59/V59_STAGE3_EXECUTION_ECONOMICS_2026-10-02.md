# V59 Stage 3 — Realistic Execution Economics

Date: 2026-10-02  
Mode: STRICT SCIENTIFIC RESEARCH  
PAPER_EXECUTION = FALSE  
LIVE_EXECUTION = FALSE

## Objective

Make strategy/AI decisions pay a per-event execution cost before capital is approved.

Stage 3 inserts an execution-economics layer between uncertainty and expected utility:

```text
Signal
  ↓
AI probabilities
  ↓
Uncertainty
  ↓
Observed liquidity + explicit stress assumptions
  ↓
ExecutionCostEstimate
  ↓
Expected Utility
  ↓
Independent Finance / Risk
```

## Cost components

The estimator separates:

- observed bid/ask spread
- taker fee
- slippage stress
- latency stress
- square-root participation impact stress
- depth-based fill capacity

The total round-trip cost is bound to the event and must be identical in the economic and financial gates.

A positive raw signal can therefore become NO_TRADE if execution cost destroys expected utility.

## Liquidity capacity

The reference notional is bounded by:

- risk budget
- asset-weight capacity
- gross-exposure capacity
- cash buffer
- turnover limit

Then liquidity capacity is estimated from:

```text
depth_notional_10bps × max_depth_participation
```

If the expected fill fraction is below the frozen minimum, finance vetoes the event.

## Market impact model

Current model:

```text
impact_bps_per_side =
min(
  max_impact_bps,
  coefficient × sqrt(fillable_notional / depth_notional_10bps)
)
```

This is a deterministic stress model.

It is **not** claimed to be a calibrated true market-impact model. Empirical calibration requires prospective order-book / trade / fill evidence.

## One-way orchestration

When execution-cost evidence is supplied, V59 records:

```text
SIGNAL_CANDIDATE
MODEL_PREDICTION
UNCERTAINTY_ASSESSMENT
EXECUTION_COST_ESTIMATE
ECONOMIC_DECISION
FINANCIAL_DECISION
FINAL_RESEARCH_DECISION
```

No later layer may replace the execution cost with a cheaper assumption.

## CLI

```bash
python -m research_bot.v59 execution-demo \
  --output results/v59-stage3
```

The demo is deterministic and research-only.

## Think-tank review

### Data Professor
Execution realism now depends on richer market evidence. The next data priority is point-in-time depth/trades/funding/basis with immutable provenance.

### Financial Engineering PhD
Dynamic costs and liquidity capacity are now inside sizing. The impact coefficient is still a stress assumption and must eventually be calibrated from forward evidence.

### Economics PhD
Trading frictions are state-dependent. Volatility/regime conditioning may later parameterize cost models, but only with out-of-sample evidence.

### Computer Engineer
Execution economics is isolated from exchange execution. Preserve this boundary before any paper/live adapter is introduced.

### Python Engineer
The model is deterministic and testable; external order-book ingestion should be adapter-specific and remain outside the cost kernel.

### AI PhD
AI cannot learn around or overwrite the cost veto. This is correct. Model tournaments should use the exact same execution-economics contract.

### Mathematics PhD
The square-root impact form is a stress function, not an estimator with confidence intervals. Future calibration must report uncertainty and sensitivity.

### Al Brooks Specialist
Breakout systems are particularly vulnerable to spread expansion and slippage during volatility bursts; signal quality should be reported net of state-dependent cost.

### Ichimoku Specialist
Cloud-break and Kijun-rejection strategies often differ in holding time and urgency; execution cost must remain separate from the Ichimoku signal definition.

### ICT Specialist
Liquidity sweeps can coincide with poor execution conditions. A detected liquidity event must not be interpreted as guaranteed fill liquidity.

### SMC Specialist
Structure/liquidity concepts require order-book/trade evidence if execution claims are made. Candle structure alone is insufficient for microstructure cost.

### Research Writer
All cost assumptions must be reported separately as observed inputs versus stress assumptions. Do not label stress values as measured market impact.

## Joint decision

Next priority:

```text
1. DERIVATIVES_AND_ORDER_BOOK_EVIDENCE
2. EXECUTION_MODEL_CALIBRATION
3. MODEL_AND_STRATEGY_TOURNAMENT
```

No profitability or live-execution claim is authorized.
