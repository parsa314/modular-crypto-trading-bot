# V58 Event Dataset Leakage Audit

Event Dataset status: `NOT_BUILT`.

| Check | Status | Evidence |
|---|---|---|
| Future-price feature causality | PASS | Future-mutation feature tests pass. |
| Current-bar breakout boundary | PASS | ARM_A prior-high excludes current bar. |
| Ichimoku cloud shift | PASS | Contemporaneous Span A/B; no backward visual join; Chikou excluded. |
| Event ID/replay | PASS | Determinism, uniqueness and replay tests pass. |
| Train-only scaling | PASS | Audited scaler rejects final holdout. |
| Calibration isolation | PASS | Audited calibrator rejects final holdout. |
| Target/barrier causality | PASS | ATR frozen at decision; entry materialized at next open. |
| Timestamp/PIT contracts | PASS | Dedicated Phase-1/2A tests. |
| Overlapping-label contamination | WARNING | Must be purged after real event timestamps exist. |
| Partition contamination | WARNING | Guard passes, but exact split is not yet sealed. |
| Dataset duplication/gaps | WARNING | Cannot audit absent raw files. |
| Fundamental PIT | WARNING | No fundamental PIT dataset admitted. |

No critical failure exists in code-level prebuild checks. Dataset-level audit is incomplete; therefore Phase 2A cannot pass.
