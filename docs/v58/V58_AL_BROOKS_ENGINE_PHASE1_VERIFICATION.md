# V58 Al Brooks Engine — Phase 1 Verification

Date: 2026-10-12

## Decision

**AL_BROOKS_ENGINE_PHASE1 = E2 PASS**

Meaning:
- deterministic implementation exists;
- causal detector tests pass;
- replay determinism passes;
- EventRecord integration passes;
- V58 regression passes;
- full repository regression passes.

This is **not** evidence of profitable alpha.

## Implemented module

`research_bot/v58/brooks.py`

Primary implementation commit:

`cc91558c266aadbeec20a26394a9c6eb19e20b23`

## Implemented setups

### BRK_B1 — Trend Pullback

Mechanical sequence:
- recent normalized trend;
- prior micro-channel;
- normalized pullback;
- prior counter-trend bar;
- qualified directional signal bar;
- continuation close.

### BRK_B2 — Breakout + Follow-Through

Mechanical sequence:
- close breaks a prior 20-bar reference by at least 0.10 ATR;
- next completed bar continues in the same direction;
- follow-through body fraction >= 0.40;
- follow-through remains beyond the broken reference.

### BRK_B3 — Failed Breakout Reversal

Mechanical sequence:
- breakout was recorded;
- failure occurs within a frozen two-bar lifetime;
- price closes back inside the exact original broken range;
- qualified opposite signal bar is required.

The broken boundary is retained and never redrawn after outcome.

### BRK_B4 — Trading Range Fade

Range state uses prior/causal data:
- normalized trend is low;
- mean overlap is sufficiently high;
- range width is bounded in ATR;
- both boundaries have repeated tests.

A fade requires a boundary probe and qualified close back inside.

### BRK_B5 — Signal Bar Quality

Study-only.

It records:
- body fraction;
- close location;
- upper/lower tail fraction;
- overlap-adjusted bullish/bearish quality.

B5 is technically prohibited from creating an executable EventRecord.

## V58 event integration

B1-B4 generate immutable `StrategyArm.ARM_D` events.

Rules:
- decision uses completed 4h bar;
- entry = next executable 4h open;
- Stop/TP = frozen common V58 barrier;
- 1.5 ATR stop floor contract;
- 3R target;
- 30 complete bars after entry timeout;
- causal regime snapshot;
- deterministic feature snapshot;
- deterministic event ID.

No outcome or TP/SL label is used to create the event.

## Causality test

The future portion of synthetic OHLCV was heavily mutated.

Brooks atomic features and setup signals before the mutation boundary remained unchanged.

Result:
**PASS**

## Synthetic semantic tests

Constructed breakout + follow-through:
**PASS**

Constructed failed-breakout reversal:
**PASS**

This verifies that the intended event semantics are actually represented by code rather than only by documentation.

## CI evidence

GitHub Actions run:

`38289766608`

Results:
- Brooks focused suite: **9 passed**
- V58 regression: **33 passed**
- full repository regression: **196 passed**
- skipped: **1**
- failed: **0**
- warnings: **1**

## Scientific status

```text
BROOKS_DEFINITION = PASS
BROOKS_CAUSALITY = PASS
BROOKS_REPLAY = PASS
BROOKS_EVENT_INTEGRATION = PASS

EVIDENCE_LEVEL = E2

HISTORICAL_OUTCOME_EVIDENCE = NOT_RUN
MULTI_ASSET_ROBUSTNESS = NOT_RUN
REGIME_ROBUSTNESS = NOT_RUN
COST_ROBUSTNESS = NOT_RUN
PROSPECTIVE_HOLDOUT = NOT_RUN

MODEL_TRAINING = FALSE
PAPER_EXECUTION = FALSE
LIVE_EXECUTION = FALSE
```

## Next Brooks-specific scientific stage

The next Brooks step is **E3 descriptive event audit** on the already frozen CoinEx H4 history:

- count B1/B2/B3/B4 events by asset;
- measure overlap between setups;
- measure regime distribution;
- label TP/SL/TIMEOUT under the frozen common barrier;
- report raw coverage and class frequencies;
- do not optimize thresholds;
- do not fit ML;
- do not use the prospective sealed holdout.

Only after that descriptive audit should Brooks enter the validation/selection firewall.
