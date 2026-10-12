# v0.53 Ontology Engineering — Causal Feature Contract

## Status

`IMPLEMENTED_FOR_RESEARCH / NO_ALPHA_CLAIM / NO_LIVE_PERMISSION`

This document converts practitioner concepts from ICT/SMC, Al Brooks price action and Ichimoku into deterministic, point-in-time research variables. It is an engineering and preregistration layer; it does **not** claim that any feature is profitable.

## Design principles

1. Every concept must have an explicit operational definition.
2. Every feature must be computable from information available at or before its decision timestamp.
3. Subjective practitioner language is translated into measurable geometry/state without inferring hidden intent.
4. Future plotting conventions are separated from information availability.
5. Higher-timeframe data joins use `available_at`, not merely bar timestamps.
6. Raw OHLCV is never silently deduplicated, interpolated or repaired.
7. Optional microstructure inputs are accepted only if point-in-time aligned and valid.
8. Prefix invariance is the core no-lookahead test: appending or perturbing future data must not alter already-computed past features.

## ICT / SMC ontology

| Concept | Operational form | Main output |
|---|---|---|
| Confirmed swing | Pivot `c=t-r` confirmed only at `t` after right-side bars close | prior swing levels |
| BOS | Current close crosses a previously confirmed structural level | bull/bear BOS |
| ChoCH | First opposite BOS relative to carried structure state | bull/bear ChoCH |
| FVG | `low[t] > high[t-2]` or `high[t] < low[t-2]` | FVG event + size |
| Displacement | Large ATR-normalized body closing near directional extreme | displacement flags |
| Order-block proxy | BOS + displacement + immediately prior opposite candle | zone boundaries |
| Mitigation | Later bar overlaps a stored OB zone | mitigation/rejection |
| Breaker proxy | Failed opposite OB zone later retested after invalidation | breaker retest |
| Liquidity pool | Latest confirmed structural high/low | ATR distance |
| Liquidity sweep | Excursion through prior swing followed by close back through level | sweep flags |
| Premium/Discount | Position inside latest valid confirmed dealing range | normalized position |
| Inducement proxy | Minor pullback swing inside major dealing range and aligned with structure | inducement proxy |
| Liquidity engineering proxy | recent sweep → displacement → BOS/ChoCH sequence | composite proxy |
| Killzones | DST-aware New-York-local research windows | London/NY flags |

Terms such as *order block*, *inducement*, *liquidity engineering* and *sweep* are formalized practitioner terminology. The engine does not infer manipulation, institutional intent or unobserved order placement.

## Al Brooks ontology

The engine formalizes trend bars/dojis, inside/outside bars, bar counts, signal-bar quality, Always-In direction, trend-vs-range state, H1/H2 and L1/L2 proxies, failed breakouts and micro double tops/bottoms. The H1/H2 and Always-In features are explicitly treated as algorithmic proxies rather than claims of perfect equivalence to discretionary chart reading.

## Ichimoku ontology

For decision-time modelling:

`Tenkan_t = (HH9(t)+LL9(t))/2`

`Kijun_t = (HH26(t)+LL26(t))/2`

`SpanA_t = (Tenkan_t+Kijun_t)/2`

`SpanB_t = (HH52(t)+LL52(t))/2`

Senkou plotting displacement is **not** implemented as future-aligned model data. The leakage-safe Chikou context is:

`ChikouContext_t = (Close_t - Close_(t-26)) / Close_t`

No `close.shift(-26)` or equivalent future leakage is permitted.

## Multi-timeframe PIT contract

`asof_join_available_features_v53()` joins a higher-timeframe feature only when:

`available_at_HTF <= decision_time_LTF`

Therefore a 1h feature with bar timestamp 10:00 but availability 11:00 is invisible to a 10:30 lower-timeframe decision.

## Software outputs

- `research_bot/ontology_v53.py`: machine-readable concept definitions, inputs, outputs, timeframes, causal delays, invalidation and interpretation boundaries.
- `research_bot/multiframework_features_v53.py`: strict OHLCV validation, causal ICT/SMC, Brooks, leakage-safe Ichimoku, optional microstructure, and PIT higher-timeframe joins.
- `tests/test_multiframework_features_v53.py`: fail-closed, prefix-invariance, future-perturbation, Chikou, FVG, microstructure, DST killzone and PIT-join tests.

## No-lookahead acceptance criteria

1. Full-history and prefix-only execution must produce identical features at the same decision row.
2. Arbitrarily perturbing every future OHLCV row must leave all past features unchanged.
3. Confirmed swings are stamped at confirmation time and never backfilled to candidate time.
4. Chikou context uses past close only.
5. A same-bar newly confirmed swing cannot define its own BOS threshold.
6. Higher-timeframe features remain invisible before `available_at`.

## Local verification before GitHub CI

The new v0.53 test suite was run independently before repository upload: **15 tests passed**. This is engineering verification only; canonical GitHub CI remains the repository gate.

## Next empirical stage: v0.54 Feature Audit

After v0.53 is frozen, no model should be fitted immediately. The next stage should quantify:

1. coverage and missingness by feature/asset;
2. event frequency and class imbalance;
3. redundancy and correlation clustering;
4. stability by asset/year/regime;
5. explicit feature-label leakage probes;
6. single-family cost-aware baselines: Price/OHLCV, ICT/SMC, Brooks, Ichimoku, Microstructure;
7. equal-weight confluence baseline;
8. only then supervised ML meta-labelling.

No family is promoted because of practitioner popularity. Negative or null incremental evidence remains a valid result.
