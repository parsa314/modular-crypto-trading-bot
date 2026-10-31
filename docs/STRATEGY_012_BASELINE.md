# STRATEGY-012 — baseline and evidence boundaries

Research only. Overall decision: BLOCKED. This is an initial causal baseline
adapter, not the requested complete multi-timeframe or ML/RL research system.
PAPER-011 and all broker/release gates remain independent and blocked.

## Frozen comparison

Reference commit `3f6ceca5b87bead9417172253d74278a3ccb375b`; engine and
confluence Git blob IDs are recorded below. Their code and thresholds are not
changed. Existing experimental results remain unchanged. The current v0.51
calendar, preregistration and sealed Kraken holdout are not touched.

| Component | Git blob | Existing behavior | Requested capability still missing |
| --- | --- | --- | --- |
| Ichimoku | `ef1ff4fc6964778532480f258f7970b5bc1133eb` | Visible shifted cloud, TK event and Kijun reclaim | Causal H4/H1/M15 hierarchy, full cloud/ATR context and validated invalidation |
| ICT | `50a02bc82b4230de98cd4ae4d44a47294ea453b7` | Prior-extreme sweep, subsequent MSS; displacement/session affect heuristic score | Sweep/shift/displacement/retest lifecycle and independently defined liquidity |
| SMC | `03047417a3da3ddfb7d9b3e063f27dfca913f672` | Closed-bar BOS, opposing candle proxy, three-bar FVG | Swing hierarchy, retest/lifecycle and explicit invalidation |
| Al Brooks | `e22647dca9efb31f186f3fd913bd9702c4352e22` | EMA context, body, close location and breakout/follow-through | Trend/range context, pullback variants and failed-breakout lifecycle |
| Confluence | `2dfa8558229c406eacb8077f20a2db4e305c214d` | Equal weights, threshold 7, 3 agreeing, conflicting sides veto | No evidence that ICT/SMC are independent; no calibrated, cost-aware selector |

The name `EXACT_FOUR_INDEPENDENT_STYLE_SIGNALS_REQUIRED` in the old code is
only a contract name and does not establish statistical independence.

## What is implemented

`strategy_research_012.baseline_candidates` invokes the four unchanged baseline
engines on a validated contiguous prefix of completed M15/H1/H4 bars. The caller
must provide source and availability metadata. It checks availability after bar
close, filters by as-of time, retains per-style blob IDs and consumed-input hash,
and labels the old 0–3 score as uncalibrated. A source hash is a caller-provided
identifier, not authentication of its source or proof of first-seen time.

Events have no verified expiry or invalidation, and are always
`eligible_for_execution=false`. No trade selector, calibrated probability, MTF
feature join, advanced four-style lifecycle, learned regime, meta-label, broker
connection or performance computation is claimed. The adapter has no access to
an OMS or order API. An isolated triple-barrier label primitive reports TP, SL,
TIMEOUT, PENDING or AMBIGUOUS from completed bars; same-bar dual touches are
never assigned an invented ordering. Barriers and entry are caller assumptions,
no data-derived volatility calibration or fitted meta-label model is provided.
Order book, order flow, funding and open interest are
DATA_UNAVAILABLE until separately sourced with provenance.

The optional 96-bar BTCUSDT historical probe uses data already inspected in a
prior run. It is a **non-prospective engineering/data smoke**, not OOS or a
profitability test. It counts baseline event occurrences only; it supplies no
entry/exit, return, win rate, PBO, DSR or trading-performance estimate.

## E0–E5 acceptance status

E0 economic comparison is BLOCKED: a frozen cost model, causal exits, sufficient
independent data and approved research protocol are not established. E1–E5 are
NOT_EXECUTED: advanced signals, regime model, meta-labels and controlled ablation
have not been implemented or validated. `NO_ECONOMIC_EDGE` would be a scientific
finding; insufficient evidence must not be relabeled as that finding. No final
holdout is consumed. No historical observation is relabeled as prospective.

## Methodology references

- Bailey et al., [The Probability of Backtest Overfitting](https://escholarship.org/uc/item/4w1110bb): CSCV estimates PBO under its stated assumptions; it is not interchangeable with CPCV.
- Bysik and Ślepaczuk, [Machine Learning-Based Bitcoin Trading Under Transaction Costs](https://arxiv.org/abs/2606.00060): the reported cost-aware results concern their data and setup, not this repository.
