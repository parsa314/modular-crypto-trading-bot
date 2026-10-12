# Exchange Provider Decision — 2026-09-14

## Status

**Decision: CoinEx is the primary exchange/data provider for the trading-bot research stack. Bitpin remains a secondary/fallback public market-data provider.**

This decision is architectural and research-facing only. It does **not** authorize live trading.

## Safety state

- `COINEX_MARKET_DATA = ENABLED`
- `COINEX_PRIMARY = TRUE`
- `BITPIN_MARKET_DATA = ENABLED`
- `BITPIN_SECONDARY = TRUE`
- `BITPIN_LIVE_EXECUTION = FALSE`
- `COINEX_LIVE_EXECUTION = FALSE`
- `PAPER_EXECUTION = FALSE`
- `LIVE_EXECUTION = FALSE`

No wallet, balance, order-create, order-cancel, or other authenticated execution endpoint is enabled by this decision.

## Rationale

### Why CoinEx is primary

CoinEx is already integrated more deeply into the canonical research repository and currently supports the data families required by the scientific and engineering stack, including:

- 4h and other OHLCV/kline retrieval
- Spot and futures market data
- Public recent trades/deals
- Spot order-book depth
- Funding-rate history
- Futures basis history
- Open-interest snapshots
- Exchange-side depth timestamps when supplied
- Exact notional depth and order-book imbalance calculations

This makes CoinEx the more suitable source for reproducible research, feature engineering, backtests, ML/RL inputs, Ichimoku/SMC/ICT studies, derivatives features, and market-microstructure work.

CoinEx is also already part of the currently frozen research data protocol, so keeping it primary avoids unnecessary protocol drift and protects reproducibility.

### Why Bitpin remains secondary

Bitpin public REST connectivity has been verified successfully and the adapter now supports:

- markets
- tickers
- order book
- recent trades
- `DepthSnapshot` compatibility

A live 30-snapshot BTC/USDT public-API probe completed successfully with 30/30 successful samples, zero crossed books, and zero API failures.

However, the live evaluation also showed limitations that make Bitpin less suitable than CoinEx as the current primary provider:

- provider-wide ticker feed contains many zero/unusable rows that require quarantine
- short-window BTC/USDT spread widened materially during the probe
- REST ticker latency showed high-tail observations on the GitHub runner
- venue-side order-book timestamp is not always exposed, so receipt time may be used as fallback
- authenticated execution and WebSocket contracts have not yet been approved or integrated

These findings do not make Bitpin unusable. They support using it as a secondary venue for redundancy, cross-venue validation, Iranian-market context, future failover, and later execution research after separate validation.

## Target architecture

```text
                    Trading Bot
                         |
                 Market Data Router
                  /             \
                 /               \
        CoinEx PRIMARY       Bitpin SECONDARY
             |                    |
        Research data         Backup / cross-check
        OHLCV                 Public market data
        Order book            Iranian venue context
        Trades                Future failover candidate
        Funding               No live execution approval
        Basis
        Open interest
```

## Operational policy

1. Scientific datasets and model-training pipelines use CoinEx as the primary source unless a study explicitly defines another venue.
2. Bitpin data may be collected in parallel for cross-venue consistency checks and provider-health monitoring.
3. A provider-router layer should fail closed when data is stale, malformed, crossed, or outside configured spread/latency thresholds.
4. Bitpin must not silently replace CoinEx inside the frozen v0.51 protocol.
5. Any future Bitpin WebSocket, authenticated account, paper execution, or live execution integration requires a separate review and explicit governance decision.
6. Live execution remains disabled across all venues.

## Evidence used for this decision

### Bitpin live public probe

Observed on 2026-09-14 for BTC/USDT:

- 30 requested snapshots
- 30 successful snapshots
- 0 failed samples
- 0 crossed books
- median spread approximately `0.2801 bps`
- p95/max spread approximately `7.6355 bps`
- median absolute ticker-vs-mid deviation approximately `0.1400 bps`
- p95/max absolute ticker-vs-mid deviation approximately `4.0977 bps`
- median ticker REST latency approximately `415.4 ms`
- p95 ticker REST latency approximately `1446.7 ms`
- median order-book REST latency approximately `305.0 ms`
- p95 order-book REST latency approximately `659.2 ms`

These timings are end-to-end public REST request timings from a GitHub-hosted runner and must not be interpreted as matching-engine latency.

### Current CoinEx integration

The canonical codebase already contains public CoinEx modules for klines, funding history, basis history, open interest, trades, and depth. Its depth adapter computes executable best bid/ask, midpoint, spread, base-unit depth, exact notional depth, imbalance, and uses exchange-provided `updated_at` when present.

## Final decision

**Primary:** CoinEx

**Secondary / fallback / cross-check:** Bitpin

**Live execution:** disabled

**Paper execution:** disabled

This decision should remain in force until a controlled cross-venue benchmark, WebSocket validation, and execution-safety review justify a change.
