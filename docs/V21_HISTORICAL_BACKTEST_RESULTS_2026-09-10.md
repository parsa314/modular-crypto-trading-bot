# v0.21-H Historical Backtest — Formal Results

Generated from GitHub Actions run `34498116730` on 2026-09-10.

Status: **HISTORICAL STRESS TEST ONLY — NON-PROMOTIONAL**

Decision: **`NO_COST_ROBUST_HISTORICAL_PROXY_CONFIRMATION`**

This document records the numerical backtest calculation that must remain part of v0.21. It is deliberately separated from the prospective v0.21 common-anchor evidence stream. The historical test cannot authorize PAPER strategy replacement, testnet promotion, or LIVE execution.

## 1. Evidence identity

- Workflow: `v21-common-anchor-proxy`
- GitHub Actions run: `34498116730`
- Branch head: `98666794f99dc080044c781bc1f7b64c40574f8a`
- Artifact ID: `10160708732`
- Artifact digest: `sha256:8538273db86ac7ac4648040142de6d5bce003de9c71be616f21f5c32b6a107d6`
- Dataset SHA-256: `166b67dc2a6da91aa62a0e90f540638ad5b3ebae93e4571789112ff732973618`
- Checksum-verified monthly archive files: 24
- Raw 5-minute rows: 210,240
- Final 4-hour panel rows: 4,342
- Assets: BTCUSDT, ETHUSDT
- Panel coverage: 2025-09-04 04:00 UTC → 2026-08-31 20:00 UTC
- Initial training cutoff: 2026-05-01 00:00 UTC
- Walk-forward evaluation months: May, June, July, August 2026
- Independent portfolio evaluation timestamps: 738

## 2. Historical feature definition

The historical proxy uses Binance Vision **spot 5-minute kline archives**, not the v0.21 prospective multi-venue REST common-anchor observations.

Each complete 4-hour decision block requires exactly 48 contiguous five-minute bars. The price-only feature family contains:

- 1, 2, 6 and 18-bar returns;
- 6 and 18-bar realized volatility;
- 6 and 18-bar moving-average gaps.

The historical flow-proxy extension contains pre-specified summaries of the 5-minute kline fields available by the 4-hour decision close:

- mean, standard deviation, last value and slope of the taker-buy quote imbalance proxy;
- quote-volume level and coefficient of variation;
- trade-count level and coefficient of variation;
- median and p95 five-minute high-low range in basis points.

For each five-minute bar the reported flow proxy is computed as

`(2 × taker_buy_quote - quote_volume) / quote_volume`, clipped to [-1, 1].

This is a **single-venue historical kline flow proxy**. It is not exchange-native aggressor reconstruction, not L2/L3 order flow and not the same object as the prospective multi-venue common-anchor measurement.

## 3. Evaluation design

Two frozen model families were used:

1. regularized Logistic Regression;
2. HistGradientBoosting (`HGB`).

Each model is evaluated with:

- `PRICE_ONLY`;
- `PRICE_PLUS_FLOW_PROXY`.

The test is expanding monthly walk-forward. A training observation can enter a month only if its target horizon ends strictly before that evaluation month. The portfolio uses a frozen probability threshold of 0.50 and maximum weight of 0.50 per asset. Transaction cost is charged per unit turnover, including initial entry and forced terminal flattening.

The pre-registered one-way cost grid is 4, 8, 12 and 20 bps, with 12 bps as the primary thesis stress assumption.

Inference uses 1,000-repetition paired moving-block bootstrap with block length 18 four-hour observations, Benjamini-Hochberg FDR across all 8 planned incremental comparisons, and a joint studentized max-T familywise-error diagnostic.

## 4. Full cost-sensitivity results

| Model | Cost | Price-only net return | +Flow net return | Incremental return | Mean incremental / 4h | 95% block-bootstrap CI | One-sided p | FDR q | max-T FWER p |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|
| Logistic | 4 bps | -0.06% | -5.71% | -5.65 pp | -0.00007921 | [-0.00029766, +0.00014955] | 0.7443 | 0.9940 | 0.9111 |
| Logistic | 8 bps | -5.39% | -14.21% | -8.82 pp | -0.00013287 | [-0.00034290, +0.00009247] | 0.8911 | 0.9940 | 0.9700 |
| Logistic | **12 bps** | **-10.43%** | **-21.94%** | **-11.51 pp** | **-0.00018653** | **[-0.00038889, +0.00004783]** | **0.9660** | **0.9940** | **0.9910** |
| Logistic | 20 bps | -19.73% | -35.38% | -15.65 pp | -0.00029385 | [-0.00051440, -0.00006348] | 0.9940 | 0.9940 | 0.9990 |
| HGB | 4 bps | -6.93% | -0.25% | +6.68 pp | +0.00009619 | [-0.00023040, +0.00040035] | 0.2757 | 0.7692 | 0.4186 |
| HGB | 8 bps | -16.96% | -11.88% | +5.08 pp | +0.00008264 | [-0.00019769, +0.00040556] | 0.2827 | 0.7692 | 0.4725 |
| HGB | **12 bps** | **-25.92%** | **-22.16%** | **+3.76 pp** | **+0.00006909** | **[-0.00022383, +0.00036561]** | **0.3127** | **0.7692** | **0.5205** |
| HGB | 20 bps | -41.04% | -39.27% | +1.77 pp | +0.00004199 | [-0.00027625, +0.00035462] | 0.3846 | 0.7692 | 0.6084 |

## 5. Primary 12-bp performance calculation

| Model / feature family | Net return | Sharpe | Sortino | MDD | Profit factor | Turnover | Explicit modeled cost | Mean gross exposure |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Logistic / Price only | -10.43% | -1.039 | -1.154 | -26.10% | 0.922 | 137 | 0.1644 | 0.560 |
| Logistic / Price + flow proxy | -21.94% | -2.523 | -3.002 | -27.23% | 0.811 | 236 | 0.2832 | 0.480 |
| HGB / Price only | -25.92% | -3.391 | -4.290 | -31.35% | 0.776 | 285 | 0.3420 | 0.494 |
| HGB / Price + flow proxy | -22.16% | -2.607 | -3.512 | -34.10% | 0.817 | 310 | 0.3720 | 0.479 |

### Logistic interpretation

Adding the historical flow proxy **made the Logistic strategy worse at every cost level**. At the primary 12-bp cost, the incremental total return was -11.51 percentage points and turnover increased from 137 to 236. Although the 20-bp bootstrap interval is entirely below zero for the flow-minus-price mean difference, the pre-registered question was whether flow provides a *positive* incremental edge. It clearly does not.

### HGB interpretation

Adding flow improved HGB total return relative to HGB price-only at all four cost levels. At 12 bps the loss improved from -25.92% to -22.16%, a +3.76 percentage-point relative change. However, the paired 95% confidence interval includes zero, `q=0.7692`, and max-T FWER `p=0.5205`. Moreover, the resulting strategy itself remains strongly net negative. Therefore this is neither statistical confirmation nor economic promotion evidence.

## 6. Cost robustness

The most important v0.21-H result is not the sign of one comparison. It is the lack of a **cost-robust positive trading result**:

- none of the 12-bp primary strategies has positive net return;
- increasing cost systematically degrades all strategies;
- the flow extension raises turnover materially for both Logistic and HGB;
- no positive incremental HGB result survives dependence-aware CI, FDR or max-T familywise control;
- the Logistic flow extension is consistently harmful in this frozen specification.

This supports the existing thesis finding that richer features do not automatically translate into tradable alpha once turnover and friction are included.

## 7. Formal decision

The v0.21-H historical calculation is therefore recorded as:

`NO_COST_ROBUST_HISTORICAL_PROXY_CONFIRMATION`

This result **does not falsify all microstructure/order-flow hypotheses**. It falsifies promotion of this particular single-venue 5-minute kline proxy under the frozen v0.21-H models, walk-forward design and cost grid.

The genuinely new scientific test remains the prospective v0.21 common-anchor pipeline, in which multiple venues share one point-in-time anchor and the same `[T-60s, T]` trade window. Its future data must not be replaced by this historical stress test.

## 8. Safety and thesis claim boundary

- `signal_authorized = false`
- `paper_strategy_replacement_authorized = false`
- `testnet_promotion_authorized = false`
- `live_execution_authorized = false`

The correct thesis statement is: **v0.21-H provides a reproducible and search-aware historical stress test, but it does not establish cost-robust incremental alpha from the tested flow proxy.**
