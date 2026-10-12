# V58 — Al Brooks Engine Phase 1

Status: IMPLEMENTATION SPECIFICATION  
Mode: STRICT SCIENTIFIC RESEARCH  
PAPER_EXECUTION: false  
LIVE_EXECUTION: false  
MODEL_TRAINING: false

## Objective

Convert the Al Brooks price-action family from loose proxies into deterministic,
causal, reproducible event detectors.

The engine does **not** claim that Brooks concepts are profitable. It converts
them into falsifiable hypotheses.

## Atomic geometry

The engine records:

- body fraction;
- close location within the bar;
- upper/lower tail fractions;
- overlap;
- trend strength;
- micro-channel length;
- pullback depth;
- prior-range boundaries;
- prior-range width in ATR;
- boundary-touch counts;
- breakout state;
- breakout-failure re-entry magnitude;
- bullish/bearish signal-bar quality.

All prior ranges exclude the current bar.

No centered pivot, future-confirmed swing, future-validated range, or future
reaction is used.

## BRK_B1 — Trend Pullback

A long candidate requires:

1. recent positive normalized trend strength;
2. a prior micro-channel of at least two bars in magnitude;
3. pullback depth within the frozen normalized interval;
4. evidence that the prior bar moved against the trend;
5. a bullish signal bar meeting body/close-location quality;
6. the decision close above the prior close.

Short is symmetric.

This is a mechanical research proxy for trend-pullback continuation, not a
claim that it reproduces every discretionary Brooks interpretation.

## BRK_B2 — Breakout + Follow-Through

The breakout bar must close beyond a 20-bar prior range by at least 0.10 ATR.

The next completed bar must:

- continue in breakout direction;
- have body fraction >= 0.40;
- remain beyond the broken reference;
- not depend on future bars.

The event timestamp is the follow-through bar close.

## BRK_B3 — Failed Breakout Reversal

The engine searches only a frozen two-bar lifetime after a mechanical breakout.

Upside failure:
- prior upside breakout;
- current price closes back inside the exact range boundary broken by that bar;
- bearish signal-bar quality condition.

Direction becomes SHORT.

Downside failure is symmetric and becomes LONG.

The original boundary is retained. It is not redrawn after the failure.

## BRK_B4 — Trading-Range Fade

A range is defined mechanically from prior information:

- low absolute normalized trend strength;
- sufficient mean bar overlap;
- bounded width in ATR;
- repeated upper and lower boundary tests.

A fade event requires a current probe near a frozen prior boundary and a
qualified rejection close back inside.

## BRK_B5 — Signal-Bar Quality Study

B5 is deliberately **not an executable trade setup**.

It measures whether body fraction, close location, tail asymmetry, overlap and
context have incremental information for TP/SL/TIMEOUT outcomes.

Attempting to generate an EventRecord from B5 must fail.

## Event integration

Executable Brooks setups map to:

- strategy_arm = ARM_D;
- signal on a completed 4h bar;
- entry at next executable 4h open;
- common V58 frozen 1.5 ATR / 3R / 30-bar barrier contract;
- immutable deterministic event ID;
- causal regime snapshot;
- Brooks feature snapshot.

No target label is used to create the event.

## Threshold governance

Phase-1 detector thresholds are frozen engineering research defaults.

They are **not** declared optimal and cannot be tuned on the sealed final
holdout.

If threshold sensitivity is evaluated later, it must be a registered ablation
inside validation-only governance.

## Required evidence before any alpha claim

1. detector causality tests;
2. deterministic replay;
3. multi-asset event counts;
4. regime distribution;
5. cost-aware outcome analysis;
6. atomic ablation;
7. comparison against generic price-action baseline;
8. sealed prospective holdout.

Phase 1 authorizes detector engineering only.
