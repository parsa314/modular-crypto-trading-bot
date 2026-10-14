# v0.21 — Common-Anchor Microstructure + Historical Proxy Stress Test Protocol

Date registered: 2026-09-10

Status: **PRE-REGISTERED BEFORE FIRST v0.21 HISTORICAL RESULT**

This stage has two deliberately separated evidence tracks. Neither track can authorize real-money execution.

## Scientific motivation

The v0.20 Phase-Q pilot equalized the *length* of recent-trade windows at 60 seconds, but each venue anchored its window to that venue's own order-book timestamp. With a permitted cross-venue clock skew approaching 60 seconds, two 60-second windows can have little or no overlap. That makes a cross-venue average of reported trade imbalance weaker than the phrase "same 60-second window" implies.

v0.21 therefore introduces a stricter common point-in-time anchor and treats v0.20 as preserved preflight evidence rather than silently rewriting or backfilling it.

## Track A — prospective common-anchor measurement

Frozen protocol version: `v0.21-phase-q-common-anchor-1`

Pilot start: `2026-09-10T18:00:00Z`

Maturity not before: `2026-09-17T18:00:00Z`

Universe:

- BTC/USDT
- ETH/USDT

Venues:

- CoinEx
- OKX
- KuCoin

Measurement target cadence: 30 minutes. Trading/forecast horizon remains 4 hours.

### Common-anchor construction

For each symbol/cycle:

1. fetch order books from all configured venues as concurrently as practical;
2. retain each venue's actual book timestamp and response timestamp;
3. define the common decision anchor `T` as the latest accepted book timestamp in that cycle;
4. fetch/re-fetch public trades after the book phase using `since = T - 60s` where supported;
5. locally filter every venue to the *identical* closed interval `[T-60s, T]`;
6. reject any trade after `T` and record the count;
7. record book age `T - book_timestamp` separately from trade staleness;
8. require each accepted venue to satisfy the frozen data-quality limits below.

### Frozen quality limits

- trade window: exactly 60 seconds;
- minimum recent trades per accepted venue: 5;
- maximum trade staleness: 30 seconds;
- maximum book age at common anchor: 20 seconds;
- minimum signed-trade notional coverage: 80%;
- maximum spread: 50 bps;
- maximum cross-venue mid dispersion: 100 bps;
- minimum unique accepted venues per symbol: 2;
- duplicate observations from the same venue cannot increase venue coverage;
- all accepted venues for a symbol must report the same trade-window start/end timestamps;
- exchange `side` remains labelled `exchange_reported_side_unverified_aggressor` unless venue-native semantics are independently verified.

### Trade-window completeness control

A count-limited public REST endpoint can truncate an active 60-second tape. v0.21 therefore records raw page counts, earliest/latest retained trade timestamps and whether the fetch reached the left boundary of the target window. A venue is quality-gated when the collector cannot establish adequate left-boundary coverage and the result appears provider-capped. This is intentionally conservative.

### Prospective maturity gate

The common-anchor pilot may proceed to feature freeze only after all of the following are true:

- >=168 elapsed hours;
- >=336 nominal 30-minute opportunities;
- >=80% quality-authorized coverage for both BTC and ETH;
- no protocol/hash/provenance breach;
- quality diagnostics do not reveal a structural provider truncation problem requiring redesign.

Manual dispatches, pull-request smoke runs, duplicate hashes, pre-start observations and v0.20 artifacts do not count toward v0.21 maturity.

## Track B — historical single-venue proxy stress test

Purpose: hypothesis falsification / engineering validation only. It is **not** a substitute for Track A and cannot be described as a prospective replication of the common-anchor feature family.

Source: Binance Public Data (`data.binance.vision`) spot 5-minute klines, because the archive exposes timestamped quote volume, trade count and taker-buy quote volume with downloadable SHA-256 checksum files.

Frozen symbols:

- BTCUSDT
- ETHUSDT

Frozen archive span:

- 2025-09-01 through 2026-08-31 UTC

Frozen decision horizon:

- 4 hours, UTC aligned

A 4-hour row is retained only when it contains all 48 expected five-minute sub-bars.

### Price-only feature family

For each asset using only information available at the 4h decision close:

- 1-bar return;
- 2-bar return;
- 6-bar return;
- 18-bar return;
- 6-bar realized volatility;
- 18-bar realized volatility;
- 6-bar moving-average gap;
- 18-bar moving-average gap.

### Historical flow-proxy family

Computed from the 48 five-minute bars inside the just-completed 4h interval:

- mean taker-flow imbalance;
- standard deviation of taker-flow imbalance;
- last five-minute taker-flow imbalance;
- pre-specified linear slope of taker-flow imbalance over the 48 sub-bars;
- log total quote volume;
- coefficient of variation of five-minute quote volume;
- log total trade count;
- coefficient of variation of five-minute trade count;
- median five-minute high-low range in bps;
- 95th percentile five-minute high-low range in bps.

For a five-minute bar, taker-flow imbalance is `(2*taker_buy_quote_volume - quote_volume) / quote_volume` where quote volume is positive. This is an archive-derived flow proxy, not a reconstructed order book or a consolidated cross-venue tape.

### Target and evaluation

Target: next 4-hour close-to-close return; classification label is whether that return is positive.

Models are frozen before evaluation:

- regularized Logistic Regression (`C=1`, standardization, deterministic solver);
- HistGradientBoostingClassifier (fixed learning rate, leaf count, iterations and random state).

No hyperparameter search is permitted in v0.21-H.

Initial training domain ends before 2026-05-01 UTC. Evaluation is expanding monthly walk-forward over May, June, July and August 2026. A training row is allowed only when its target horizon ends strictly before the evaluation month begins.

Long/cash portfolio rule is frozen: each of BTC and ETH receives weight 0.5 when model probability >=0.5, otherwise weight 0.0. No shorts. Initial entry and final liquidation are charged.

Planned one-way turnover cost grid:

- 4 bps
- 8 bps
- 12 bps (**primary cost assumption**)
- 20 bps

Primary comparison for each model/cost cell:

`PRICE_PLUS_FLOW_PROXY - PRICE_ONLY`

Planned family size: 2 models x 4 cost assumptions = 8 comparisons.

### Statistical controls

For every planned comparison report:

- total return, annualized Sharpe, Sortino, maximum drawdown, turnover, explicit cost and exposure;
- monthly walk-forward fold results;
- paired moving-block bootstrap confidence interval for incremental mean net return;
- one-sided centered moving-block bootstrap p-value for positive incremental mean;
- Benjamini-Hochberg FDR across the 8 pre-registered comparisons;
- joint max-T moving-block bootstrap familywise diagnostic across the same 8 incremental return series.

No v0.21 historical proxy result can authorize PAPER strategy replacement, testnet promotion or LIVE execution even if statistically positive. It may only inform whether the prospective common-anchor feature hypothesis remains scientifically worth testing.

## Stop / redesign rules

Redesign rather than tune the same evidence when any of the following occurs:

- common-anchor book age is structurally too large for the REST design;
- trade endpoints repeatedly fail to cover the 60-second left boundary because of provider caps;
- cross-venue coverage cannot reach the pre-registered 80% threshold for structural reasons;
- the historical proxy produces unstable sign across months/assets and no cost-robust incremental evidence;
- the prospective feature family later fails its frozen out-of-sample comparison.

A WebSocket/L2 event collector is a new evidence source and requires a new protocol version; it must not be silently mixed into this pilot.

## Safety contract

`signal_authorized = false`

`paper_strategy_replacement_authorized = false`

`testnet_promotion_authorized = false`

`live_execution_authorized = false`

Negative or inconclusive results remain first-class thesis evidence.
