# V58 Phase 2 — Event Barrier & Data Freeze

Date: 2026-09-25  
Mode: STRICT SCIENTIFIC RESEARCH  
PAPER_EXECUTION: false  
LIVE_EXECUTION: false

## 1. Purpose

Phase 2 freezes the V58 primary event payoff geometry, exact historical dataset identities, chronological split boundaries, and future holdout identity before any V58 model is trained.

No V58 predictive model is fitted in this phase.

## 2. Barrier provenance

The numerical H4 barrier contract is not newly optimized for V58.

The repository already contained a frozen v0.19 multi-timeframe contract in:
- research_bot/multitimeframe_strategies_v19.py
- docs/V19_SOURCE_DERIVED_MULTITIMEFRAME_PROTOCOL.md

For 4h strategies the inherited defaults are:
- reward target: 3.0R;
- ATR stop multiplier: 1.5;
- maximum hold: 30 bars after entry;
- signal on completed bar;
- entry at next bar open;
- stop-first if stop and target are both touched in one OHLC bar;
- minimum stop distance: 0.05% of entry price.

V58 preserves these values rather than searching over them.

## 3. Frozen primary V58 barrier

Primary timeframe: 4h.

ATR:
- Wilder-style causal ATR(14);
- measured from information available at the decision bar;
- no future update is allowed after the event snapshot is frozen.

Entry:
- next executable 4h bar open after the event decision.

Stop distance:

stop_distance = max(1.5 * ATR_at_signal, 0.0005 * entry_price)

Long:
- stop = entry - stop_distance;
- target = entry + 3.0 * stop_distance.

Short:
- stop = entry + stop_distance;
- target = entry - 3.0 * stop_distance.

Timeout:
- the entry bar is eligible for immediate TP/SL resolution;
- if no barrier resolves, 30 complete bars after the entry bar are allowed;
- forced TIMEOUT occurs at the close of the entry+30 bar;
- total maximum OHLC observations including the entry bar = 31.

Intrabar ambiguity:
- TP and SL in the same bar => SL first;
- override is allowed only with valid timestamped intrabar sequence evidence.

## 4. Why the barrier is common across ARM_A ... ARM_E

The primary V58 question concerns incremental information in Ichimoku, ICT/SMC and Al Brooks feature/event families.

Using different stop/target geometry by arm would confound:
- event-quality information;
- payoff geometry;
- turnover;
- holding time;
- cost exposure.

Therefore all five primary strategy arms use the same H4 barrier contract.

A structural-stop ablation may be proposed later only through a pre-outcome amendment. It is not part of the primary baseline tournament.

## 5. Exact historical CoinEx freeze

A successful GitHub Actions artifact already exists.

Workflow run:
36042192410

Artifact:
10826344695

Artifact name:
coinex-spot-4h-real-36042192410

Artifact digest:
sha256:ce9e1863fb830885ff97e4b0cbd77de29610dd4227534dddc161ff4d31b93803

Collector head:
244ec36aa3bb3c916bf5c79fed4760a51f6dffce

Data:
- venue: CoinEx;
- market: spot;
- timeframe: 4h;
- assets: BTC, ETH, SOL, XRP, DOGE;
- rows per asset: 6572;
- first bar: 2023-09-25 08:00 UTC;
- last bar: 2026-09-24 12:00 UTC;
- gaps: 0;
- no gap filling;
- unclosed final candle excluded by collector.

Exact compressed-file, canonical-frame and schema hashes are stored in V58_DATA_MANIFEST.json.

## 6. Historical split freeze

The split inherits the pre-existing v0.19 60/20/20 chronological contract.

Development:
- 3943 rows;
- 2023-09-25 08:00 UTC through 2025-07-13 08:00 UTC.

Validation:
- 1314 rows;
- 2025-07-13 12:00 UTC through 2026-02-17 08:00 UTC.

Historical internal diagnostic test:
- 1315 rows;
- 2026-02-17 12:00 UTC through 2026-09-24 12:00 UTC.

The historical internal test is NOT pristine promotion evidence.

Reason:
prior repository research, including V54, inspected market information overlapping the 2026 tail. V58 therefore records this window as research-spent rather than calling it untouched.

## 7. Prospective final temporal holdout

To create genuinely unseen temporal evidence, V58 seals a future CoinEx holdout before collection.

Venue:
CoinEx spot.

Assets:
BTC/USDT, ETH/USDT, SOL/USDT, XRP/USDT, DOGE/USDT.

Timeframe:
4h.

First bar open:
2026-09-25 16:00 UTC.

Length:
540 fixed 4h bars.

Last bar open:
2026-12-24 12:00 UTC.

Expected last bar close:
2026-12-24 16:00 UTC.

This is a fixed-time window. It may not be extended or shortened based on performance.

Forbidden before final evaluation:
- hyperparameter selection;
- feature selection;
- calibration selection;
- utility-threshold selection;
- regime-threshold selection;
- stop/target retuning.

Status at Phase 2:
SEALED_FUTURE_NOT_AVAILABLE.

## 8. Historical Binance transfer dataset

A second exact artifact is retained only as a historical transfer diagnostic.

Workflow run:
36042637131

Artifact:
10827012617

Artifact digest:
sha256:9e805305784a93008ec56b1f5add094df2f0c805289a3255def7f439e1606dff

Venue:
Binance spot.

Window:
2022-01-01 through 2023-08-31.

Assets:
BTC, ETH, SOL, XRP, DOGE.

All provider monthly ZIPs were checked against Binance CHECKSUM files.

This dataset is research-spent and temporally earlier than the CoinEx development set. It cannot serve as the final V58 promotion holdout.

## 9. Kraken governance

Kraken remains reserved as a secondary external-venue challenge.

It is not collected or inspected in Phase 2.

A separate pre-access amendment is required to freeze exact Kraken market symbols and collection window before any bytes are opened.

## 10. Phase 2 gate

Phase 2 passes only if CI verifies:
- exact historical artifact files;
- SHA-256 identity;
- canonical frame hash;
- schema hash;
- exact row count;
- first/last timestamps;
- gapless 4h cadence;
- frozen 60/20/20 boundaries;
- frozen barrier constants;
- 31-observation timeout semantics;
- stop-first collision semantics;
- prospective holdout seal;
- PAPER=false;
- LIVE=false.

Even after Phase 2 PASS, model training remains blocked until the V58 validation/selection firewall gates are implemented and tested.

Scientific status after Phase 2:
- event barrier freeze: eligible for PASS;
- historical data identity freeze: eligible for PASS;
- pristine final temporal holdout: SEALED, not mature;
- baseline model training: NO-GO;
- PAPER execution: NO-GO;
- LIVE execution: FALSE.
