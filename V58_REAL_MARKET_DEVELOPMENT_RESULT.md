# V58 Real-Market Development Replay — 2026-09-28

## Evidence class

`REAL_MARKET_DEVELOPMENT_EVIDENCE`

The replay used the exact byte-verified Binance and CoinEx H4 archives. It is not a final holdout, because these archives were previously inspected. No model was trained and no parameter was changed after observing these outcomes. The frozen primary barrier is 1.0 ATR stop, 1.5 ATR target, 12 bars, STOP_FIRST, next-bar-open entry, and 24 bps round-trip cost.

## Results

| Venue | Arm | Events | TP | SL | TIMEOUT | Mean event return after 24 bps |
|---|---|---:|---:|---:|---:|---:|
| binance | ARM_A | 353 | 134 | 212 | 7 | -0.432684% |
| binance | ARM_B | 3386 | 1198 | 2009 | 179 | -0.345426% |
| binance | ARM_C | 11 | 4 | 7 | 0 | -0.589242% |
| binance | ARM_D | 1389 | 472 | 826 | 91 | -0.520031% |
| coinex | ARM_A | 1108 | 471 | 601 | 36 | -0.016168% |
| coinex | ARM_B | 7274 | 2943 | 3952 | 379 | -0.140899% |
| coinex | ARM_C | 21 | 8 | 12 | 1 | -0.031842% |
| coinex | ARM_D | 3018 | 1178 | 1691 | 149 | -0.216480% |
| coinex | ARM_E | 3 | 0 | 3 | 0 | -1.629010% |

These are per-event descriptive means. Events overlap and compete for no shared capital in this replay, so the table is not a portfolio backtest and must not be interpreted as a realizable equity curve.

## Findings

- 16,563 real candidate events were resolved across 51,100 H4 bars.
- ARM_E produced zero Binance events and only three CoinEx events. It is not statistically evaluable.
- ARM_C produced very few events and is also underpowered.
- Most arm/venue combinations have negative mean return after the preregistered 24 bps cost.
- Positive cells are isolated development observations and do not pass cross-asset or independent-holdout promotion criteria.
- The two complete replays produced 32 byte-identical files. Aggregate file-map SHA-256: `089431f74b59e06b4501de2f5104e114ad71352e9492e3a983bc962ab8703991`.

Scientific decision: `NO_INCREMENTAL_ALPHA_EVIDENCE`; `FAILED_PROMOTION_GATE`; `V58_PHASE2A_NO_GO`.
