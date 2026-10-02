# V59 Stage 2 — Real Market Data Plane

Date: 2026-10-02  
Mode: STRICT SCIENTIFIC RESEARCH  
PAPER_EXECUTION = FALSE  
LIVE_EXECUTION = FALSE

## Objective

Rebuild the market-data foundation before adding larger AI models or reinforcement learning.

Stage 2 creates a read-only, fail-closed path from a public exchange source to a versioned dataset:

```text
Public Exchange Data
      ↓
Provider Identity
      ↓
Immutable Provider Evidence
      ↓
UTC / Bar-Open Clock Contract
      ↓
Closed-Bar Filter
      ↓
Duplicate / Gap / Grid / Candle Audit
      ↓
Verified OHLCV Dataset
      ↓
Dataset Hash + Quality Hash
      ↓
Causal Complete-Bucket MTF Views
```

No data-source component can create, cancel or modify an order.

## New modules

```text
research_bot/v59/
  market_data.py
  artifacts.py
  data_plane.py
  source_registry.py
  multitimeframe.py
  fixtures.py
  adapters/
    __init__.py
    ccxt_public.py
```

## Market-data contract

Required OHLCV schema:

```text
timestamp,open,high,low,close,volume
```

Timestamp semantics are explicit:

```text
timestamp = bar OPEN time in UTC
```

A bar can enter the verified dataset only when:

```text
bar_open + timeframe <= as_of
```

Therefore the current partial candle is excluded.

## Fail-closed quality gate

The following conditions reject dataset promotion:

- duplicate timestamps
- missing bars / clock gaps
- off-grid timestamps
- null values
- non-finite values
- invalid OHLC geometry
- no closed bars
- provider identity mismatch
- exchange/symbol/timeframe/market-type relabeling

Rejected ingestion preserves the provider evidence and writes a rejection manifest, but it does not create a normalized promoted dataset.

## Immutable artifact policy

Accepted run:

```text
<run_id>/
  raw/provider_payload.bin
  normalized/ohlcv.csv
  dataset_manifest.json
```

Rejected run:

```text
<run_id>/
  raw/provider_payload.bin
  rejection_manifest.json
```

Existing artifacts are never overwritten.

## Public provider boundary

The first generic real-data adapter uses CCXT public OHLCV.

It is intentionally limited to:

- load markets
- fetch public OHLCV

It rejects credentials and contains no order-management API.

The stored provider payload is labeled:

```text
CCXT_PARSED_PUBLIC_RESPONSE
```

It is not falsely represented as raw exchange HTTP wire bytes.

## Multi-timeframe policy

V59 builds higher timeframes only from complete source buckets.

Example:

```text
1m -> 5m
```

requires exactly five source bars for a 5m candle.

If one constituent 1m bar is missing, that 5m bucket is not synthesized.

This prevents hidden interpolation and accidental fabrication of price history.

## CLI

Deterministic Stage-2 integrity run:

```bash
python -m research_bot.v59 data-fixture-demo \
  --output results/v59-stage2-fixture
```

Real public OHLCV example:

```bash
python -m research_bot.v59 public-ohlcv \
  --exchange binance \
  --symbol BTC/USDT \
  --timeframe 1m \
  --since 2026-10-01T00:00:00+00:00 \
  --until 2026-10-01T06:00:00+00:00 \
  --as-of 2026-10-01T06:05:00+00:00 \
  --limit 1000 \
  --run-id btc-binance-20261001-0000-0600 \
  --output results/market-data
```

## Stage 2 limitations

Stage 2 does not yet claim:

- order-book replay
- historical bid/ask spread
- stochastic slippage
- nonlinear market impact
- latency simulation
- partial fills
- funding / borrow integration
- liquidation modeling
- live order execution

Those belong to the next engineering stage.

## Think-tank end-of-stage review

The requested expert room evaluates the completed Stage-2 scope.

### Data Professor

The highest-value improvement is that missing/duplicate/off-grid bars are now first-class evidence failures rather than silently cleaned. Next, venue-specific depth/trade/funding feeds need the same PIT and immutable-source contracts.

### Financial Engineering PhD

OHLCV integrity is necessary but insufficient for realistic PnL. The next blocker is execution economics: spread, fees, slippage, market impact, liquidity capacity and partial-fill behavior.

### Economics PhD

Macro/regime evidence should remain separate from raw market microstructure. No later regime label may be backfilled into earlier decisions.

### Computer Engineer

Provider adapters are isolated from the V59 decision kernel. Continue this pattern: public read-only adapters, typed normalized contracts, then downstream consumers.

### Python Engineer

The data plane is deterministic and dependency-light. Exchange/network behavior is kept outside the core validation logic so CI can use deterministic fixtures.

### AI PhD

Do not promote LSTM, Transformer, foundation models or RL before the execution-cost layer exists. Otherwise the optimizer can learn unrealistic fill assumptions.

### Mathematics PhD

The data-quality gate must remain deterministic and pre-model. Any later imputation experiment must be explicitly separated and compared against the strict no-imputation baseline.

### Al Brooks Specialist

Reliable lower-timeframe clocks are essential for breakout/follow-through and failed-breakout state machines. Missing bars must not be silently bridged.

### Ichimoku Specialist

MTF Ichimoku requires exact completed higher-timeframe candles. Incomplete-bucket resampling would contaminate Tenkan/Kijun/Kumo states.

### ICT Specialist

Sweep, displacement, MSS/CISD and FVG sequences depend on event order. Gaps and timestamp shifts can fabricate sequence logic, so strict clock integrity is mandatory.

### SMC Specialist

Liquidity/structure events require deterministic candle identity. The single-source dataset hash is an important prerequisite for event reproducibility.

### Research Writer

Every later empirical claim should cite dataset hash, quality hash, source registry hash, config hash and code commit.

## Joint decision

The room prioritizes:

```text
1. REALISTIC_EXECUTION_ECONOMICS
2. DERIVATIVES_AND_ORDER_BOOK_EVIDENCE
3. MODEL_AND_STRATEGY_TOURNAMENT
```

Deep/RL work remains blocked until the first two are sufficiently credible.

No profitability or live-readiness claim is authorized by Stage 2.
