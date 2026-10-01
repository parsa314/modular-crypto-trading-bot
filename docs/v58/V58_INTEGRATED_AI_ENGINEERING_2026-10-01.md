# V58 integrated AI engineering runbook — 2026-10-01

The integrated runner connects causal event construction, multiclass learning, calibrated probabilities, cost-aware abstention and a shared-capital portfolio on generated fixtures. Its classification is **`SYNTHETIC_ENGINEERING_ONLY`**. There is zero empirical model validation in this run. `empirical_training`, `paper_execution`, `live_execution` and promotion authorization remain false.

The governing repository is `parsa314/modular-crypto-trading-bot`. Read [AGENTS.md](../../AGENTS.md) and the [canonical research status](../CANONICAL_RESEARCH_STATUS_2026-09-13.md) for scientific lineage and execution permissions. This engineering baseline does not supersede frozen empirical results, reopen Kraken, authorize empirical fitting, or alter prospective evidence rules.

## اجرای فارسی

از ریشهٔ همین مخزن، دستور زیر مسیر یکپارچهٔ آزمایشی را با پنج دارایی اجرا و خروجی‌ها را ذخیره می‌کند:

```bash
python -m research_bot.v58 synthetic-ai-demo --output results/v58-ai-new --bars 1600 --seed 58
```

داده‌ها ساختگی‌اند؛ خروجی این اجرا شاهد سودآوری در بازار واقعی نیست. خلاصه در [summary.json](../../evidence/v58_integrated_ai_engineering/summary.json) و دفتر تصمیم‌ها، معامله‌ها و سرمایه در فایل‌های `portfolio_*.json` ثبت می‌شود. محدودیت افت سرمایه پس از مشاهده فعال می‌شود و سقف تضمین‌شدهٔ زیان نیست. اجرای مجدد فقط خروجی کاملاً یکسان را در همان پوشه می‌پذیرد؛ برای کد، داده یا نسخهٔ کتابخانهٔ متفاوت، نام پوشهٔ تازه انتخاب کنید تا شواهد قبلی حفظ شوند.

## Components and fixed design

| Component | Responsibility |
| --- | --- |
| [integrated.py](../../research_bot/v58/integrated.py) | Generate fixtures, connect actual event/barrier labels to learning, calculate prior-only utility, run fixed cost scenarios and publish evidence. |
| [learning.py](../../research_bot/v58/learning.py) | Chronological folds, information-time purging, training-only preprocessing, calibration-only temperature fitting, test predictions and frozen JSON inference. |
| [portfolio.py](../../research_bot/v58/portfolio.py) | Shared cash, fee-aware position sizing, independent risk vetoes, causal admissions, exits and equity accounting. |
| [barriers.py](../../research_bot/v58/barriers.py) | Frozen entry geometry and TP/SL/TIMEOUT information times. |

Defaults generate 1,600 contiguous UTC four-hour OHLCV bars for each of `BTC/USDT`, `ETH/USDT`, `SOL/USDT`, `XRP/USDT` and `DOGE/USDT`. Seed 58 is the base seed; each asset receives its deterministic index offset. These names identify synthetic fixtures rather than exchange observations. The fixture includes quiet and volatile intervals; labels are produced by barrier resolution, without assigning or balancing outcomes.

The barrier policy is one ATR stop distance, 1.5R target, 12-bar holding horizon and `STOP_FIRST` ambiguity handling. A closed signal bar decides at the exact immediate next open. Missing opens cannot be shifted to a later candle. Direction is LONG and market type is spot. Terminal learning events are excluded by the availability of a complete 12-bar path, independently of whether their labels happen to resolve earlier.

The eight selected continuous features are one-bar returns, ATR percentage, past realized volatility, trend strength, Tenkan/Kijun distance, displacement body ratio, relative volume and trading-range position. The dataset uses explicit `feature_*` names. Names associated with labels, future outcomes and exits are rejected by the learning seam; causal construction remains the producer's responsibility.

## Chronology, calibration and frozen scoring

The default walk-forward schedule has three expanding training windows, sliding calibration windows and disjoint test windows. Calendar budgets are fixed before inspecting labels: 20% of unique decision clocks per calibration window and 30% in total across tests. Integer remainder clocks enter the initial training window. All events at the same decision clock stay together. Later folds may learn from earlier test periods only after their labels satisfy the next fold's availability cutoffs.

Before fitting, training rows are retained only when their `label_available_at` is strictly earlier than calibration start minus the four-hour embargo. Calibration rows obey the corresponding test-start cutoff. Every fold is required; insufficient post-purge support or missing training classes fails the run rather than silently dropping a fold.

Median imputation and standard scaling are fitted on training rows only. A fixed multinomial logistic model (`C=1`, `lbfgs`) predicts the explicit class axis `TP`, `SL`, `TIMEOUT`. A scalar temperature in `[0.25, 4.0]` is fitted by calibration log loss on calibration rows only. Temperature is frozen before transforming or scoring test rows. Test labels contribute only to reported test metrics. Multiclass Brier loss is the mean sum of squared class errors, with range 0–2; log loss is also recorded.

Each fold serializes coefficients, intercepts, feature columns, training medians/means/scales, temperature and `model_available_at`. `inference_state_sha256` binds those inference values. `score_frozen_synthetic_baseline(bundle, events)` scores synthetic rows from this JSON state without estimator fitting or pickle loading, rejects changed inference state, and rejects decision clocks before model availability. Extra label columns do not affect scoring. The hash detects integrity changes; it does not authenticate a model or grant trading permissions.

For an existing evidence bundle, frozen scoring can be exercised from the repository root:

```python
import json
from pathlib import Path
import pandas as pd
from research_bot.v58.learning import score_frozen_synthetic_baseline

root = Path("results/v58-ai-new")
fold = json.loads((root / "learning.json").read_text())["folds"][-1]
rows = pd.read_csv(root / "event_dataset.csv")
available = pd.Timestamp(fold["model_available_at"])
rows = rows.loc[pd.to_datetime(rows["decision_at"], utc=True) >= available]
predictions = score_frozen_synthetic_baseline(
    fold, rows.drop(columns=["label", "label_available_at"])
)
```

## Utility and fixed cost comparisons

Let `r = (entry - stop) / entry`, `g = (target - entry) / entry`, and `H` be normalized predictive entropy. The default decision utility is:

```text
U = p(TP) * g - p(SL) * r - p(TIMEOUT) * 0.25 * r
    - round_trip_cost_bps / 10000 - 0.05 * r * H
```

Only entry geometry, probabilities and diagnostics already available at the decision clock enter this expression. Prediction identity and UTC decision/entry clocks must match the synthetic event. The model abstains when regime confidence is below 0.20, entropy exceeds 0.98, the standardized shift score exceeds 8.0, or utility is nonpositive. These are fixed engineering defaults, without tuning to this run's test outcomes.

Cost scenarios are fixed at 0, 24, 36 and 50 basis points per round trip. The model and temperature are not refitted for a cost scenario. Each scenario compares:

- `ML_UTILITY`: calibrated utility and abstention followed by portfolio risk admission.
- `STRATEGY_ONLY`: the same underlying test candidates with zero forecast utility and no forecast gate, followed by identical portfolio risk admission.
- `NO_TRADE`: zero return, zero drawdown and zero transaction costs.

The strategy-only baseline supplies no fabricated positive forecast. Its same-time ties use event IDs. ML candidates at exactly the same open rank by descending prior-only expected utility, then event ID; future outcomes and later arrivals cannot enter the priority key.

## Shared capital and independent risk

| Default | Value |
| --- | --- |
| Initial equity | 10,000 account units |
| Stop-loss risk budget per trade, including both fill costs | At most 0.25% of current equity |
| Asset weight at admission | At most 35% |
| Gross exposure at admission | At most 70% |
| Drawdown kill threshold | 5% from observed equity peak |
| Historical CVaR95 veto threshold | 3.5% |
| Cash buffer | 5% |

The configurable risk, asset, gross, drawdown and CVaR thresholds may be tightened, and cannot exceed these frozen values. `RiskEngine` and `DrawdownRiskGate` independently veto new exposure. A model or strategy-only policy cannot bypass them. There is one active position per asset and no asset preemption.

Half of the effective round-trip cost is charged on entry filled notional and half on exit filled notional. Stop sizing includes both costs. Asset capacity reserves the possible remaining entry fees so another admission's fee cannot itself push an existing allocation beyond its admission cap. Cash and exposure budgets are shared across all symbols. Net returns are derived from actual cash flows rather than subtracting an additional round-trip cost from an already cost-adjusted result.

At each open, resting gap exits settle first, then remaining positions are marked and risk is observed. A gap stop fills at that open; the conservative gap-target convention fills at its target limit, preventing an unrealizable overshoot from creating a phantom equity peak. All same-open admissions follow. Only then does the simulator inspect high/low values for `STOP_FIRST`, target or timeout exits. Intrabar outcomes receive the bar-close timestamp and cannot finance an earlier same-open admission. Remaining positions are liquidated at final close with exit costs.

Exposure and concentration limits are **pre-trade admission caps**. Price changes may move marked exposure or weights above those caps between admissions. The 5% drawdown threshold latches when an observed open/close equity state reaches it; gaps and discrete observation can produce a larger realized drawdown. It is not a guaranteed loss ceiling. Once latched, new risk is rejected while risk-reducing and terminal exits remain permitted.

The recorded default run reported negative synthetic net returns at 24 bps: approximately −1.6951% for ML and −2.2807% for strategy-only, with both drawdown gates triggered. ML drawdown reached approximately 5.0713%, and marked gross exposure reached approximately 70.0911%. These observations illustrate the admission/observation distinction above. They remain synthetic descriptive results and were preserved without retuning. Exact values and provenance belong to [summary.json](../../evidence/v58_integrated_ai_engineering/summary.json) and the corresponding portfolio ledgers.

## Evidence and reproduction

| Artifact | Contents |
| --- | --- |
| [summary.json](../../evidence/v58_integrated_ai_engineering/summary.json) | Classification, parameters, dependency versions, source/dataset identities, class support and fixed cost comparisons. |
| [learning_report.json](../../evidence/v58_integrated_ai_engineering/learning_report.json) | Fold metrics, split support, purge counts and row-identity digests. The full generated `learning.json` retains row IDs and predictions. |
| [frozen_models.json](../../evidence/v58_integrated_ai_engineering/frozen_models.json) | Portable fold inference state and availability bounds, without fitting or unsafe deserialization. |
| Generated `event_dataset.csv` | Synthetic events, prior features, labels and label information clocks. |
| Generated `portfolio_ml_24bps.json` | ML decision ledger, fills, fees, shared cash, positions, equity curve and risk summary. |
| Generated `portfolio_strategy_24bps.json` | Strategy-only ledger under the same capital and risk rules. |
| [artifact_manifest.json](../../evidence/v58_integrated_ai_engineering/artifact_manifest.json) | SHA-256 for every payload artifact and the canonical artifact-map hash. |

Git stores the compact summary, learning report, frozen model states and complete artifact manifest. Full raw bundles are generated in the named `results/` directory; the dedicated [CI workflow](../../.github/workflows/v58-integrated-ai-engineering.yml) uploads them when it completes successfully. The recorded local complete bundle is `/workspace/results/v58-ai-20261001-final`. The manifest describes that full bundle, so verify it against the full generated directory, not the compact Git evidence directory.

The compact model export replaces training row-ID lists with their digest and
records its own `inference_state_sha256` for that exported metadata.
`source_inference_state_sha256` retains the original full-bundle identity.
Coefficients, numerical preprocessing, temperature and availability bounds are
unchanged. Reloading the committed compact export reproduces all 1,701 test
predictions across its three folds, including entropy and shift diagnostics.

Equivalent portfolio files are emitted for the other three fixed costs. Per-asset `events/` directories retain event records and their engineering evidence ledgers. Source bytes are fingerprinted across V58 Python modules and checked again before publication. Run metadata records dependency versions. Identical source, parameters and software environment are required when comparing byte-level reproducibility; a matching seed alone is insufficient.

Publication stages a complete bundle and atomically renames it into place. An identical existing bundle is accepted. A partial or differing bundle is rejected without overwriting its bytes. Use a fresh output directory for a changed run and retain earlier results.

Manifest verification from the repository root:

```python
import json
from hashlib import sha256
from pathlib import Path
from research_bot.v58.events import stable_hash

root = Path("results/v58-ai-new")
manifest = json.loads((root / "artifact_manifest.json").read_text())
actual = {
    name: sha256((root / name).read_bytes()).hexdigest()
    for name in manifest["files"]
}
assert actual == manifest["files"]
assert stable_hash(actual) == manifest["artifact_map_sha256"]
```

Focused checks, without asserting an undocumented test count:

```bash
python -m pytest tests/v58/test_learning_v58.py tests/v58/test_portfolio_v58.py tests/v58/test_integrated_v58.py
```

In the managed workspace where dependencies are installed separately, prepend `PYTHONPATH=/workspace/test-deps` to the runner or test command. Persist fresh command results separately from economic evidence; a passing engineering check cannot authorize promotion.

## Limits of the evidence

- There is no empirical model validation, profitability finding, calibrated market probability guarantee or promotion evidence. The real-data governance gates remain in force.
- Normalized entropy and maximum absolute training-standardized feature displacement are uncertainty/shift diagnostics. They are not certified uncertainty bounds or a validated distribution-shift test.
- Historical CVaR95 uses completed close-to-close portfolio returns. Fewer than 20 observations yields `null`; even 20 observations offer very weak tail support. An available synthetic estimate does not establish market tail-risk adequacy.
- There is no microstructure, latency, spread, slippage, market-impact or empirical correlation estimator. Effective fill costs are explicit assumptions; correlation and impact remain unavailable rather than fabricated. Immediate next-open fills are an idealized engineering convention.
- Only LONG spot positions are implemented. No leverage, shorting, exchange orders, paper execution or live execution is enabled.
- The baseline is a small fixed logistic model. It does not claim to complete a deep-learning or reinforcement-learning research program, an architecture tournament, multiple-testing correction or the project's full empirical validation plan.

## Scientific basis and source coverage

The implementation adopts established safeguards against leakage and retrospective validation. The resources below support those safeguards or motivate further validation; none establishes that this synthetic baseline is profitable. This is a bounded methodological reference set, not an exhaustive claim about the latest trading literature.

| Source | Supported use and inspection scope |
| --- | --- |
| Official scikit-learn 1.8, [Common pitfalls: data leakage](https://scikit-learn.org/1.8/common_pitfalls.html#data-leakage) | Training-only fitting of preprocessing, then transformation of independent evaluation data. Primary software documentation. |
| Official scikit-learn 1.8, [Probability calibration](https://scikit-learn.org/1.8/modules/calibration.html) | Calibration data must be disjoint from data used to fit an already fitted classifier. The local implementation uses a separate scalar temperature fitted on its calibration role. Primary software documentation. |
| Official scikit-learn 1.8, [Cross-validation](https://scikit-learn.org/1.8/modules/cross_validation.html#time-series-split) | Temporal evaluation preserves chronology. The local grouped-clock and label-information purge is custom; it is not a claim that `TimeSeriesSplit` itself accounts for overlapping label horizons. Primary software documentation. |
| Yae and Tian (2022), [DOI:10.1016/j.physa.2022.127379](https://doi.org/10.1016/j.physa.2022.127379) | Indexed abstract only. Context for financial ML research; no full-text reproduction, numerical benchmark or adopted profitability guarantee is claimed. |
| 2025 chapter, [DOI:10.1007/978-981-96-6839-7_10](https://doi.org/10.1007/978-981-96-6839-7_10) | Indexed abstract only. Context for automated trading/AI research; its particular methods and results are not validated by this engineering fixture. |
| [arXiv:2209.05559](https://arxiv.org/abs/2209.05559), financial DRL/backtest-overfitting research | Abstract only. Motivation to preserve independent evaluation and avoid selecting a trading method from favorable backtests. No DRL implementation or reproduction is claimed here. |

Paper references are deliberately limited to abstract-level coverage. Full-text method checks, empirical replication, prospective support and the frozen scientific gates are still necessary before any stronger research or execution claim.
