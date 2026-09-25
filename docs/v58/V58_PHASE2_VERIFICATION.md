# V58 Phase 2 Verification Record

Date: 2026-09-25

## Decision

**V58_PHASE2_EVENT_BARRIER_AND_DATA_FREEZE = PASS**

This is an infrastructure/data-governance pass only. It is not evidence of predictive alpha or profitability and does not authorize model training, PAPER execution, or LIVE execution.

## Verified scientific contract

Primary H4 barrier:
- ATR period: 14
- stop distance: max(1.5 × ATR at signal, 0.05% of entry)
- target: 3R
- signal: completed decision bar
- entry: next executable 4h open
- timeout: close of entry+30 bar
- maximum path observations including entry bar: 31
- same-bar TP/SL ambiguity: STOP_FIRST
- common payoff geometry across ARM_A ... ARM_E

The numerical contract was inherited from the pre-existing frozen v0.19 H4 defaults, rather than selected after V58 outcomes.

## Exact historical artifacts

### CoinEx primary historical research dataset

- workflow run: 36042192410
- artifact ID: 10826344695
- artifact digest: sha256:ce9e1863fb830885ff97e4b0cbd77de29610dd4227534dddc161ff4d31b93803
- five assets: BTC, ETH, SOL, XRP, DOGE
- venue: CoinEx spot
- timeframe: 4h
- rows per asset: 6572
- first bar: 2023-09-25T08:00:00+00:00
- last bar: 2026-09-24T12:00:00+00:00
- verified gaps: 0

The CoinEx history is explicitly marked **research-spent** because its tail overlaps prior repository research. It is not represented as a pristine final holdout.

### Binance historical transfer diagnostic

- workflow run: 36042637131
- artifact ID: 10827012617
- artifact digest: sha256:9e805305784a93008ec56b1f5add094df2f0c805289a3255def7f439e1606dff
- five assets verified
- venue: Binance spot
- timeframe: 4h
- window: 2022-01-01 through 2023-08-31
- provider monthly CHECKSUM verification inherited from collector
- usage: historical transfer diagnostic only; no tuning or promotion

## Frozen chronological split

CoinEx 6572-row history:

- development: 3943 rows, 2023-09-25 08:00 UTC → 2025-07-13 08:00 UTC
- validation: 1314 rows, 2025-07-13 12:00 UTC → 2026-02-17 08:00 UTC
- historical internal diagnostic test: 1315 rows, 2026-02-17 12:00 UTC → 2026-09-24 12:00 UTC

The historical internal diagnostic test is research-spent and is not allowed to authorize promotion.

## Sealed prospective final temporal holdout

- venue: CoinEx spot
- timeframe: 4h
- assets: BTC, ETH, SOL, XRP, DOGE
- first bar open: 2026-09-25 16:00 UTC
- fixed length: 540 bars
- last bar open: 2026-12-24 12:00 UTC
- expected last close: 2026-12-24 16:00 UTC
- status: SEALED_FUTURE_NOT_AVAILABLE

No tuning, feature selection, calibration selection, regime-threshold selection, utility-threshold selection, or barrier retuning may use this future window.

Kraken remains sealed as a secondary external-venue challenge and was not accessed in Phase 2.

## CI evidence

Verification workflow:
- run: 36140144927
- verified head: 26eb2ffbf6dffd734d7a06755d725b0b8f94b4c8
- exact CoinEx artifact recovery: PASS
- exact Binance artifact recovery: PASS
- exact file/frame/schema/timestamp/gap verification: PASS
- split verification: PASS
- focused V58 tests: **24 passed**
- full repository regression: **187 passed, 1 skipped, 0 failed, 1 warning**

## Reproducibility defects found and corrected

The first Phase 2 CI run exposed that the old schema hash depended on raw pandas dtype strings. Pandas-version differences could therefore change the schema digest even when semantic data types were identical.

The contract was corrected to hash ordered columns plus semantic types:
- datetime_utc
- boolean
- number
- string
- other

The frozen OHLCV semantic-schema SHA-256 is now:
`7c8ff83bf7c3c4db3073fcb775822882e7847f6f4f7fb31dde0285fe98815c5a`

A second verifier issue compared policy metadata as if it were part of split identity. The verifier was corrected to compare only rows/start/end for split identity, while keeping `promotion_authorized=false` as separate governance metadata.

Neither correction changed market data, barrier values, timestamps, outcomes, or execution permissions.

## Current gate

- Research Freeze: PASS
- Phase 1 infrastructure: PASS
- Phase 2 barrier freeze: PASS
- Phase 2 exact historical data identity verification: PASS
- prospective final temporal holdout identity: SEALED
- validation/selection firewall: NOT YET COMPLETE
- baseline model training: NO_GO
- Deep Learning gate: CLOSED
- RL gate: CLOSED
- PAPER execution: FALSE
- LIVE execution: FALSE

## Next authorized engineering stage

V58 Phase 3 must implement and test the validation/selection firewall before any model is fit:

1. purged expanding walk-forward event splits using event information intervals;
2. train-only preprocessing object factory;
3. validation-only model/calibration/utility selection;
4. hard prohibition on final/holdout data entering selection APIs;
5. trial/config/dataset hash binding;
6. fold-level evidence ledger;
7. T2/T4 tests including final-holdout misuse rejection.

Only after that gate passes may B0/B1 baseline fitting be considered.
