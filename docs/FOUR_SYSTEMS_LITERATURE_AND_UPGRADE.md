# Literature audit and four-system research upgrade

Audit date: 2026-10-11. Evidence state: HYPOTHESIS / ENGINEERING-ONLY.
The supplied 31-entry list is a research lead, not a verified bibliography.
`FOUR_SYSTEMS_SOURCE_CATALOG.csv` separates supplied claims from verified scope.
No journal ranking, novelty priority, university affiliation or performance in
this bot is inferred from a title or search result.

## Primary evidence checked

- [Informatica paper, official PDF](https://www.informatica.si/index.php/informatica/article/download/12899/6712),
  DOI 10.31449/inf.v50i13.12899, pp. 1, 6, 10–12: deterministic structural
  features, chronological calibration and CVaR-constrained sizing on hourly
  XAUUSD. The 46.7% return / 1.18 Sharpe / 6.8% drawdown describe its development
  evaluation, not our crypto system. Its later held-out results are separate.
  Relevance: methodology comparator; not an independent reproduction or guarantee.
- [Hugging Face whitepaper](https://huggingface.co/JonusNattapong/xauusd-trading-ai-smc-v2/blob/main/XAUUSD_Trading_AI_Technical_Whitepaper.md):
  the 85.4% / 1,247-trade claim appears in a self-published technical document.
  Peer review, auditable underlying trades and independent replication were not
  established. Exclude it from evidence of achieved thesis performance.
- [Scikit-learn calibration guide](https://scikit-learn.org/stable/modules/calibration.html):
  calibration data must be independent of estimator fitting; isotonic can
  overfit small samples. A better calibration score does not remove model/search
  overfitting. We report test Brier, log loss, ECE/reliability and raw probabilities.
- [Agentic quantitative trading survey](https://arxiv.org/abs/2608.31041):
  an arXiv preprint, useful for system/evaluation framing; not proof of production
  readiness or the claimed uniqueness of a four-family combination.

## Metadata only / unresolved

The [dual-timeframe Ichimoku/CNN title](https://www.magiran.com/volume/245422)
was located in a secondary journal index; full primary text and claimed university
provenance were not verified. The
[Ichimoku VAE title](https://www.sciencedirect.com/science/article/pii/S2773186324000653)
has publisher metadata, but full text was unavailable during this audit. The
[KTH PPO/Ichimoku thesis](https://kth.diva-portal.org/smash/get/diva2%3A1986675/FULLTEXT01.pdf)
was located through repository metadata; full-text performance was not checked.
QUANTA's exact supplied title/publication attribution remains unresolved.
Commercial TenSurf pages support company/product descriptions, not academic
performance claims. No private “100 Keivan papers” collection was supplied.
MQL5/Medium/TradingView/GitHub/forum material is implementation inspiration, not
automatically peer-reviewed evidence. The remaining titles require primary URLs,
DOIs and protocol checks before academic citation or reproducing their metrics.

## Corrections to the supplied analysis

The audited current `ichimoku_advanced.py` and `features.py` already used causal
trailing features; an existing negative-shift feature leak was not confirmed.
The new package tests future perturbation and prefix invariance rather than
claiming to have fixed an unobserved bug. Negative shifts appear only in **target
creation**, excluded from feature whitelists and purged by label availability.
Combining features does not imply certain overfitting; PCA does not guarantee its
absence. “No prior work combines all four” and numerical similarity percentages
are unverified survey assertions, not findings of a systematic literature search.

## Implemented definitions

`research_bot/four_systems/features.py` produces 38 features (19 per timeframe).
Ichimoku: Tenkan/Kijun, current cloud width, trailing visible cloud distance and
causal lagged-price comparison. SMC: confirmed swing break/change and latest
opposing-candle OB. ICT: confirmed-level sweep, three-bar FVG/active gap distance
and UTC hour encoding. Brooks-inspired proxies: body fraction, close location,
inside/outside bars, two pullback legs with a directional close breakout relative
to EMA20. These definitions are reproducible operational choices, not claims of
institutional intent or equivalence to discretionary teaching. OB/FVG state tracks
only the latest zone and invalidates it on specified breaches.

Higher bars are UTC-aligned complete buckets ending at 04:00, 08:00, etc.
`timestamp` is the **bar close**, never the exchange's candle-open timestamp.
`available_at` is timezone-aware reception/availability; no future higher bar or
backdated swing confirmation is admitted. Input permits exactly one explicit
base_quote pair, regularly spaced bars, valid OHLC geometry and bounded delay.
Convert open timestamps at ingestion only with known interval/source semantics.

The frozen baseline is StandardScaler -> optional PCA(95% training variance) ->
logistic regression C=1 -> isotonic calibration. Scaler, reduction and classifier
fit training only; calibration has its own subsequent block; tests follow all
calibration outcome availability plus an embargo. Configurations are fixed before
each run; the inspected synthetic fixture is not prospective evidence. PCA,
isotonic and adding families are compared, never assumed improvements. This does
not implement VAE/CNN/PPO/HMM, Gaussian LSTM training or the paper's LP allocation.

## Run and integration

```bash
python -m scripts.run_four_systems_research \
  --journal /tmp/four-system.sqlite --output /tmp/four-system-report.json
python -m scripts.verify_four_systems_coverage --output /tmp/four-system-coverage.json
# Actual historical data, with explicit close-time and availability columns:
python -m scripts.run_four_systems_research --csv closed_bars.csv \
  --journal /tmp/historical-four-system.sqlite --output /tmp/historical-report.json
```

Synthetic mode is explicit ENGINEERING_SYNTHETIC: 3,200 closed hourly bars, seed
2026, train >=150 events, calibration 100, test 80, class minimum 10. Historical
mode requires >=300 training and >=1,000 calibration events, class minimum 30,
200 test events per fold; inadequate data fails instead of becoming synthetic.
Four-bar horizon, one-bar embargo and declared 0.0024 cost hurdle are frozen;
the latter is a scenario threshold, **not measured Bitpin/Nobitex round-trip cost**.

The CLI composes `FourSystemHypothesisGenerator` with canonical
`ResearchTradingOrchestrator.process_candidate()` and additive `TradeJournal`.
Outputs are immutable hypotheses with model/causal-row lineage. Gross return
proxy is training conditional context only; no economic forecast is promoted.
The canonical adapter produces zero-confidence NO_TRADE; journal entries record
hypotheses, independent scenario risk ceilings and the report, with no order/fill.
Existing demo PAPER execution still uses its prior permitted strategies; the new
challenger is not silently inserted into its active execution bundle.

Risk ceilings use Decimal, exact fractional empirical ES, declared funding and
existing exposure. They never raise the existing 10% position / 3.5% ES ceilings.
Historical setup returns overlap; this size ceiling is not a calibrated portfolio
CVaR estimate or execution approval. A separate portfolio experiment is required.

## Actual engineering result and remaining gates

The synthetic run evaluated six variants over two chronological folds, logged
160 hypotheses and two risk-ceiling events plus one report (163 events). All
canonical decisions were NO_TRADE. Repeat replay inserts no duplicates.
For ALL_FOUR, test Brier raw/calibrated was 0.2104/0.2138 and 0.1592/0.1624.
For ALL_FOUR_PCA, it was 0.2198/0.2161 and 0.1962/0.2164. Isotonic and PCA were
therefore not uniformly beneficial; no winner or profitability claim is made.
Synthetic outcomes do not estimate real-market predictive performance.

No executable economic backtest return matrix exists for this new challenger;
DSR/PBO are explicitly NOT_RUN, not substituted with classifier scores. Existing
completion statistics and graduation infrastructure remain available, but this
challenger has not passed them. New costed backtests with point-in-time historical
sources, preregistered search accounting, first-seen forward collection and risk
validation are required before PAPER graduation. RESEARCH_ONLY / PAPER_OFF /
LIVE_OFF applies to this experiment. Frozen v0.51 and deployment remain unchanged.

Thesis mapping: Chapter 2 uses verified-source tiers; Chapter 3 documents causal
definitions and the validation protocol; Chapter 4 can report this engineering
ablation and negative evidence, clearly labelled synthetic. It cannot report
trading alpha or production readiness from these tests.
