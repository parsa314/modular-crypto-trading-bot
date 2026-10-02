# V58 market-audit firewall — 2026-10-02

## Purpose

This step continues the integrated V58 path after the first fixed-threshold market-model check reported no advantage over a simple class-prevalence baseline and no admitted trades. That result must be preserved; the same test observations must not be used to rescue thresholds, costs, features or model hyperparameters.

The new `research_bot.v58.market_audit` module therefore creates a separate **development-only** real-market learning path. It does not weaken the synthetic-only guard in `learning.py`, does not alter the historical real replay, and does not authorize PAPER or LIVE execution.

## Evidence boundary

Accepted market bytes must already be frozen in `research_bot/v58/development_sources.py` and must pass the exact archive/CSV hashes and structural checks used by the V58 development replay. At this revision the repository-verifiable sources are:

- Binance: BTC/ETH/SOL/XRP/DOGE, H4, 2022-01 through 2023-08;
- CoinEx: BTC/ETH/SOL/XRP/DOGE, H4, 2023-09-25 through 2026-09-24.

There is **no frozen Nobitex market-data source in the current V58 registry**. A local or Codex-session Nobitex dataset cannot be promoted into this audit by a caller-provided hash or status string. It needs a separately audited intake with original bytes, source identity and immutable manifest first.

All accepted archives remain `PREVIOUSLY_INSPECTED_DEVELOPMENT_ONLY`. They are not final holdouts.

## Frozen no-retuning protocol

The market audit uses the same basic engineering defaults already frozen by the integrated V58 path:

- classes: `TP`, `SL`, `TIMEOUT`;
- three chronological expanding folds;
- 20% calibration-clock budget per fold and 30% total disjoint test-clock budget;
- four-hour information-time embargo;
- train-only median imputation and standardization;
- fixed multinomial logistic regression, `C=1`, `lbfgs`, seed 58;
- scalar temperature fitted on calibration rows only, bounded to `[0.25, 4.0]`;
- eight fixed causal feature columns used by the integrated engineering baseline;
- 24 bps round-trip cost for the primary market audit;
- fixed abstention: regime confidence >= 0.20, entropy <= 0.98, standardized shift <= 8, and expected utility > 0;
- no same-test threshold, cost, feature or hyperparameter rescue.

The comparator is a **class-prevalence probability baseline estimated from the purged training rows only**. The test labels never determine that baseline, preprocessing, temperature or model coefficients.

## Output semantics

The report is classified as `REAL_MARKET_DEVELOPMENT_MODEL_AUDIT` and records:

- per-fold raw/calibrated Brier and log loss;
- class-prevalence Brier and log loss;
- improvement deltas versus prevalence;
- fixed-gate admitted-event count and coverage;
- mean admitted event return after 24 bps when at least one event is admitted;
- compact frozen JSON model state and row-identity digests;
- source archive/dataset identities;
- immutable artifact hashes.

Event returns are descriptive. Overlapping events are **not** converted into a realizable portfolio equity curve in this audit. Shared-capital portfolio validation remains a later, separately evidenced stage.

Even when development metrics are positive, the report keeps:

```text
final_holdout = false
profitability_confirmed = false
paper_execution = false
live_execution = false
promotion_authorized = false
```

A positive development gate means only that the frozen candidate may proceed to an independent holdout review; it is not a trading authorization.

## Run

For the exact frozen CoinEx archive:

```bash
python -m research_bot.v58 verified-market-audit \
  --venue coinex \
  --archive /path/to/coinex-source.zip \
  --output results/v58-market-audit-coinex-NEW
```

For Binance, replace `coinex` and the archive path with the exact registered Binance artifact.

Use a fresh output directory after any source/code/environment change. Existing evidence is immutable and will not be overwritten with different bytes.

## Scientific next step

1. Reproduce the fixed audit on the exact registered CoinEx bytes without retuning.
2. Preserve the result even if the model again fails prevalence or admits zero trades.
3. If Nobitex is required, first freeze an authenticated Nobitex data intake; do not reuse a claimed local hash as provenance.
4. Only a candidate that survives development comparison should be evaluated on a genuinely independent, untouched holdout and then prospective evidence.
5. PAPER/LIVE remain disabled throughout this sequence.
