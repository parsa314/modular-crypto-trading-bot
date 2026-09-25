# V58 Protocol Amendment 001 — Barriers and Arms

- Amendment ID: `V58-AMD-001`
- Parent protocol SHA-256: `cf58fd4d6eabadfcc5e57e87e61e14ae3ad7d1c0bbb9378659dd79157d106798`
- Scientific baseline commit: `fdaee2580f24997acd8122760dbf73a9f06a3141`
- Freeze time: `2026-09-25T12:25:00Z`
- Outcome inspection before freeze: `FORBIDDEN / NOT PERFORMED`
- Model training: `FORBIDDEN`
- Paper/live execution: `FALSE / FALSE`

## Reason

The parent protocol intentionally left numerical barriers and mechanical ARM definitions unresolved. It also placed next-open-derived prices in the decision record. This amendment freezes those choices before event outcomes are inspected and separates the immutable decision event from its later modeled entry.

## Common event convention

Candle timestamps denote candle opens. An event becomes available only at the close of candle `t`; earliest entry is candle `t+1` open. All calculations use finite, closed-bar values. Chikou is excluded. Candidate events are research observations, not orders. Spot execution remains long-only; short candidates are disabled unless separately preregistered.

## ARM_A — S6 trend breakout

Long candidate iff `close[t] > max(high[t-20:t-1])` and `EMA200[t] - EMA200[t-6] > 0`. The prior-high window excludes candle `t`.

## ARM_B — causal Ichimoku

- `B1_CLOUD_BREAKOUT`: close crosses from at/below to above the contemporaneous unshifted cloud top.
- `B2_TK_TREND_CONTINUATION`: close above cloud, Tenkan above Kijun, positive Tenkan slope and nonnegative Kijun slope.
- `B3_PULLBACK_REJECTION`: close above cloud, bullish signal bar with close-location ≥0.75, and a low during `t-2..t` within 0.25 ATR of its contemporaneous Kijun.

Span A/B are computed at `t` without visual backward joins. Subtypes remain distinct.

## ARM_C — ICT/SMC measurable proxy

Long chain requires a bullish sweep/reclaim in the prior six bars, positive sweep depth, current displacement body ratio ≥0.60, displacement range ≥1.0 ATR, relative volume ≥1.0, a bullish MSS within three bars and positive MSS break distance. FVG presence/absence and exact chain states are recorded. This is a codified proxy, not discretionary ICT interpretation.

## ARM_D — Al Brooks proxies

- D1 trend continuation: trend strength ≥0.5, signal quality ≥0.5, positive follow-through.
- D2 breakout: breakout strength ≥0.25 and signal quality ≥0.5.
- D3 failed breakout: failed-breakout score ≥0.25 and bullish close.
- D4 range reversal: absolute trend strength ≤0.25, range position ≤0.20 and bullish close.
- D5 pullback continuation: trend strength ≥0.5, pullback depth 0.5–2.0 ATR and bullish close.

These are only deterministic price-action proxies.

## ARM_E — challenger confluence

ARM_E is emitted only when ARM_A, at least one ARM_B subtype, ARM_C and at least one ARM_D subtype occur at the same decision timestamp. Contributors are recorded as correlated families, never independent votes.

## Entry and primary outcome barrier

- Entry: next bar open.
- ATR: Wilder ATR14 fixed at event formation; never recomputed using future data.
- Stop distance: `1.00 × ATR_t`.
- Target distance: `1.50 × ATR_t`.
- Horizon: 12 bars including entry bar (48 hours on H4).
- Long: `SL=entry-ATR`, `TP=entry+1.5ATR`.
- Same-bar TP/SL without trusted intrabar sequence: `STOP_FIRST`; ambiguity flag true.
- No hit by bar 12: `TIMEOUT`, with terminal close return stored separately.
- Data ending before bar 12: `RIGHT_CENSORED`, excluded from the three-class target.

Labels are physical outcomes and are independent of costs. Stored economics include gross return, gross return in R, round-trip cost and net return.

## Sensitivity-only barriers

- SENS_A: stop 0.75 ATR, target 1.50 ATR, horizon 8.
- SENS_B: stop 1.00 ATR, target 2.00 ATR, horizon 12.
- SENS_C: stop 1.25 ATR, target 2.50 ATR, horizon 18.

They are `SENSITIVITY_ONLY` and cannot define or promote a winner.

## Cost contract

Round-trip stress levels remain 0/24/36/50 bps. Primary economic reporting uses 24 bps. Costs are subtracted once from gross return and never alter TP/SL/TIMEOUT.

## Data partitions and holdout

Each immutable dataset must record source, venue, symbol, timeframe, UTC bounds, row count, schema hash, file SHA-256, missing/duplicate counts, download timestamp and limitations. DEVELOPMENT, VALIDATION and FINAL_HOLDOUT boundaries must be sealed in a hashed manifest before Event Dataset construction. FINAL_HOLDOUT access by training, preprocessing, feature selection, calibration, threshold selection or hyperparameter search must fail closed.

## Prohibition

No barrier, threshold, Arm rule, split boundary or cost choice may be changed retrospectively in response to profitability, Sharpe, winning combinations or final-holdout outcomes. Any later change requires a new numbered amendment and new hashes.
