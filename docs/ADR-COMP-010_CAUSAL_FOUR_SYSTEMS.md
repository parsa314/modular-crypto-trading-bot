# ADR-COMP-010 — Causal four-family calibrated research challenger

Status: accepted for engineering research only, 2026-10-11. Experiment namespace:
`FOUR_SYSTEMS_CAUSAL_CALIBRATION_V1`. No economic or prospective preregistration
is asserted: the synthetic fixture's outcomes have been inspected.

## Problem

The thesis needs a reproducible comparison of Ichimoku, ICT/SMC and Brooks-inspired
structure, with closed multi-timeframe context and separate probability calibration.
Informal pattern names and reported accuracy from other markets cannot justify
execution or changes to the frozen v0.51 experiment.

## Options and evidence

1. Reuse the existing frozen experiment's features and thresholds: easy integration,
   but changes scientific lineage and mixes exploratory and prospective evidence.
2. Add CNN/LSTM/PPO/VAE and optimize the combined model: flexible but increases
   search burden, optional dependencies and calibration/sample requirements.
3. Separate causal features, fixed logistic baseline, optional training-only PCA,
   chronological calibration and common-cohort ablations: transparent, cheap and
   directly testable. Chosen.

The Informatica source supports testing deterministic structure, calibrated
probabilities and independent risk constraints. It does not demonstrate alpha in
this crypto bot. Scikit-learn warns about small-sample isotonic overfitting;
historical mode therefore requires at least 1,000 calibration events. The smaller
synthetic protocol is explicitly engineering-only. Reduction is a challenger,
not a necessary cure for overfitting. See the linked literature audit.

## Decisions and trade-offs

- Features: trailing Ichimoku; visible cloud uses positive lag; confirmed swings
  become usable after confirmation; deterministic BOS/CHOCH, latest opposing
  candle OB, three-bar FVG and sweeps. Brooks features are documented proxies,
  not an authoritative encoding of his full discretionary method.
- Timeframes: UTC-aligned closed 1h/4h buckets; join by actual availability, reject
  gaps/delays beyond the next close. Incomplete buckets never enter predictors.
  This rejects rather than imputes missing data; irregular feeds need a separately
  reviewed ingestion policy.
- Validation: train -> calibration -> test with outcome-availability purging and
  one-bar embargo. All six feature variants use the same warmup/setup/test cohort.
  Training-only scaler/PCA/classifier; isotonic fits only calibration outcomes.
  Binary target is a four-bar gross return exceeding a declared 24 bps cost hurdle.
  This is outcome classification, not a simulator-backed trading return series.
- Selection: four separate families, all four, all four with PCA; raw/calibrated
  probabilities and training base-rate comparator reported. No test winner is
  selected. Class support failures remain in the report. No threshold/seed sweep.
- Integration: emit immutable `HypothesisCandidate`; pass through the existing
  abstention adapter and orchestrator, then append to the operational SQLite
  journal. No changes to `DecisionEngine`, canonical risk limits, service flags,
  existing completion execution, frozen v0.51, or scientific milestone ledger.
- Risk: Decimal single-asset empirical ES ceiling, exact fractional upper-tail
  mass, cash and existing-exposure constraints. Hard ceilings are <=10% position
  weight and <=3.5% equity ES budget; no order authorization. Overlapping historical
  setup scenarios are correlated, not independent portfolio CVaR or an LP solver.

## Acceptance criteria

Future changes and truncation leave prior feature values unchanged. Label
availability is strictly before calibration/test boundaries. Test outcomes cannot
change fitted scaler/PCA/model identity. Invalid data, inadequate class support,
untrained inference, unavailable evidence and weaker risk ceilings fail closed.
Rerunning identical input preserves journal identities; every hypothesis remains
NO_TRADE with no fill. New module statement coverage must exceed 80% each.

## Impact and rollback

New package, CLI and tests are additive. README adds navigation and CI adds one
coverage check. Remove this package, CLI, test, coverage step and documentation to
rollback; journal records stay in their independent dataset/protocol namespace.
No historical record needs alteration. A later economic promotion requires a new
frozen trial matrix, complete costed simulator backtests, DSR/PBO/search accounting,
fresh first-seen registration and explicit graduation. Nothing here satisfies it.
