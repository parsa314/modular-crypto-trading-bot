# V58 Amendment 002: implementation repair before market outcome inspection

Parent: remote PR86 head `3a5faf59836e00c1ad26978dd9123839f7ccc682`.
Date: 2026-09-26 UTC. This is a proposed corrective implementation amendment,
not a retroactive claim that amendment 001 was complete. No new market outcome,
model fit, paper order or live order is authorized by this document.

## Defects and corrections

1. Closed-bar decision time equals the immediately following bar's open in the
   zero-latency OHLC research model. Entry must equal this boundary, not skip to
   a later available candle. Intrabar outcomes become available at bar close;
   no exact within-bar crossing time is claimed. Missing bars fail closed.
2. ARM_C must establish a bullish sweep/reclaim, THEN displacement on a later
   candle, THEN MSS on a later candle, within six bars of the sweep. The MSS
   reference is the prior high frozen at the sweep; completed chains are consumed.
   State timestamps, event-time ATR, feature values and snapshot hashes are retained.
3. Bullish signal proxies require a bullish candle. Failed-breakout recovery
   uses the previously breached level, not a recomputed moving reference.
4. Invalid/nonfinite OHLCV, unordered/duplicate/off-grid or gapped rows are rejected,
   never silently sorted, filled or converted to usable features. Cloud warmup is
   missing, not zero. Regime availability denotes close, not candle open.
5. Scalers fit DEVELOPMENT only; calibration input selection uses VALIDATION
   only; TEST and FINAL_HOLDOUT cannot enter fitting. Unknown partition/purpose
   fails closed. Calibration fitting itself remains NOT_IMPLEMENTED, not a stub
   falsely reported as a completed statistical model.
6. Research admission must apply abstention/cost/risk/duplicate/cash gates before
   mutating an in-memory order-intent/cash ledger. This is a unit-testable research
   component, not an exchange adapter or authorization for paper execution.

## Scientific constants retained

Primary SL 1 ATR, TP 1.5 ATR, H=12; costs 0/24/36/50 bps with primary 24;
STOP_FIRST; long-only spot; risk 0.25%, asset 35%, gross 70%, kill 5%.
No parameter is selected from market performance.

## Data and evidence

Previously supplied Binance and CoinEx archives are available outside the Git
tree. Verify exact archive/member/CSV hashes, candle geometry, timestamps and
coverage without generating outcomes. Preserve them as DEVELOPMENT_ONLY because
their earlier use is documented in V55/V56 artifacts. Their historical periods
cannot be relabelled an untouched V58 final holdout. Venues are never spliced.

## Execution boundary

Real-market Event Dataset construction remains blocked until an admissible
complete split, final holdout, integration review and revised freeze are approved.
An explicitly synthetic engineering demo is permitted only to exercise actual
software connections. It is not research-market evidence or a performance trial.
Model training, PAPER and LIVE remain disabled. A green CI run is not promotion.
