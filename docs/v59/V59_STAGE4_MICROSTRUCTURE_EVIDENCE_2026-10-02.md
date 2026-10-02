# V59 Stage 4 — Microstructure and Derivatives Evidence

Date: 2026-10-02  
Mode: STRICT SCIENTIFIC RESEARCH  
PAPER_EXECUTION = FALSE  
LIVE_EXECUTION = FALSE

## Objective

Add point-in-time market-microstructure evidence without contaminating historical decisions.

Stage 4 introduces:

- typed order-book snapshots
- strict bid/ask ordering and crossed-book rejection
- spread and depth calculations
- conservative two-sided depth conversion for execution-cost estimation
- generic point-in-time metric contract
- prospective derivatives evidence
- immutable order-book and metric manifests
- real public CCXT order-book capture
- real prospective CoinEx futures-market metric capture

## Order-book contract

A valid book requires:

```text
bids sorted descending
asks sorted ascending
best_bid < best_ask
positive price and quantity
timezone-aware observed_at
source + source_hash
```

Depth inside a 10 bps band is calculated separately for bids and asks.

The execution layer receives:

```text
depth_notional_10bps = min(bid_depth, ask_depth)
```

This is intentionally conservative.

## PIT derivative metric contract

Every metric carries:

```text
entity
metric
effective_at
available_at
observed_at
value
source
source_hash
confidence
```

A historical decision may use a metric only when:

```text
available_at <= decision_at
```

## Critical anti-backfill rule

The new CoinEx adapter records current public futures-market observations as:

```text
available_at = observed_at = collection time
```

It does not pretend that a value fetched today was historically available at an earlier decision.

Therefore Stage 4 supports prospective collection safely, while historical PIT use remains blocked unless archival availability evidence exists.

## Public read-only adapters

### CCXT order book

Allowed:

- load markets
- fetch public order book

Forbidden:

- credentials
- create order
- cancel order
- balance access
- withdrawal/transfer

### CoinEx prospective futures metrics

Endpoint:

```text
/v2/futures/market
```

Captured when present:

- open_interest_volume
- maker_fee_rate
- taker_fee_rate

These are prospective observations only.

## CLI

Deterministic integrity run:

```bash
python -m research_bot.v59 microstructure-demo \
  --output results/v59-stage4
```

Real public order book:

```bash
python -m research_bot.v59 public-orderbook \
  --exchange binance \
  --symbol BTC/USDT \
  --limit 50 \
  --run-id btc-book-001 \
  --output results/microstructure
```

Prospective CoinEx futures metrics:

```bash
python -m research_bot.v59 coinex-prospective-metrics \
  --symbol BTC/USDT \
  --run-id btc-coinex-metrics-001 \
  --output results/microstructure
```

## Think-tank review

### Data Professor
The project now distinguishes bar data from order-book and derivative evidence. Prospective collection should run long enough to characterize missingness, source outages and venue differences.

### Financial Engineering PhD
Execution stress parameters should now be calibrated against observed spread/depth and, later, simulated/prospective fills. Calibration error must be reported.

### Economics PhD
Funding/open-interest observations can capture leverage regimes but should not be treated as causal explanatory variables without availability-safe timing and robustness tests.

### Computer Engineer
Provider-specific network code remains isolated under adapters. Keep the core contracts provider-agnostic.

### Python Engineer
The current snapshot contracts are deterministic and testable. Long-running collectors should add retry/backoff, rate-limit and idempotent run identities rather than overwriting evidence.

### AI PhD
Microstructure and derivatives can become model features only after PIT joins and missing-data semantics are frozen. UNAVAILABLE_DATA must not silently become zero.

### Mathematics PhD
Execution calibration should estimate uncertainty, not only point parameters. Sensitivity bands should accompany impact/slippage assumptions.

### Al Brooks Specialist
Fast breakout and failed-breakout systems are sensitive to spread expansion. Net results should be segmented by spread/depth regime.

### Ichimoku Specialist
Ichimoku remains a slower structural signal; microstructure should influence admission/execution cost rather than rewrite the Ichimoku state.

### ICT Specialist
Liquidity sweep signals and actual executable liquidity are different concepts. The new separation is necessary.

### SMC Specialist
Order-book depth is evidence about executable liquidity, while SMC liquidity pools are structural hypotheses. These should remain separate variables.

### Research Writer
Prospective versus historical evidence must be labeled explicitly in thesis tables and artifacts.

## Joint decision

Next priority:

```text
1. EXECUTION_MODEL_CALIBRATION
2. MODEL_STRATEGY_TOURNAMENT
3. PAPER_RUNTIME
```

No profitability or live-readiness claim is authorized.
