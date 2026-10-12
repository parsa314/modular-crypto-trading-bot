# R1 audit of the supplied attachment and engineering prompt

Audit date 2026-10-12; attachment reference date 2026-10-11; ENGINEERING_ONLY; engineering prompt verdict: FAIL_AS_EXECUTABLE_SPECIFICATION.
The attachment SHA256 is 12501858a2354e37992742465ef11d9d6e3fb4ded42464fdccbb95c288ada74d. Its contents reproduce the existing literature audit, not the newly described 2000-2026 comprehensive review. Existing current source is a54c94795d29663b043f126920dbb7d6627c1007. No new historical CSV/report/source package accompanied the claims of 26,280 bars, 5,871 hypotheses or negative mean_net. Those numbers are not independently verified.

## Confirmed defects and consequences

| Severity | Supplied location | Evidence / consequence | R1 direction |
|---|---|---|---|
| Critical | get_ict_features_safe, bullish_ob_flag | shift(-1) remains in returned DataFrame. In a 25-row fixture, row 11 is false in a 12-row prefix, true after an impulse at future row 12. Adding a separate causal column does not remove the leaking column. | Remove it from any future feature contract; record event only at confirmation, never backdate. |
| High | triple_barrier_labeling, lower_barrier / upper_barrier | Return standard deviation is dimensionless; adding it to price is inconsistent. Price=1000, sigma=.01, multiplier=2 gives 1000.02 rather than multiplicative simple-return barrier 1020. | Declare relative vs log-return units, entry price and frozen volatility estimator. |
| High | triple_barrier_labeling, hits_lower | Boolean idxmin selects the first false, not the first true. [False,False,True,False] returns first row rather than third. | First hit uses first true position, with explicit no-hit handling. |
| High | triple_barrier_labeling, times[hits_upper/lower] | idxmax/idxmin return index labels. Timestamp indexing a DatetimeIndex as a positional array raises IndexError in the fixture. | Preserve original row identity, distinguish positions and timestamp labels. |
| High | triple_barrier_labeling, result index | Warmup rows are skipped but outputs are assigned prices.index[:len(labels)], moving valid labels to earlier feature rows. | One output per original event ID, with excluded/censored reason. |
| High | triple_barrier_labeling, max_steps | Tail events use a shortened horizon silently; close-only prices cannot diagnose intra-bar double touches. | Incomplete horizon -> CENSORED; OHLC double touch -> ambiguous, report a pessimistic loss scenario without pretending path order is known. |
| High | ensemble / CV / risk prompt | No chronological OOF meta-training definition, multiple contradictory embargo rules (1 bar, 5 bars, 5% data), and newly invented Kelly/drawdown limits. | Baseline first; exact interval purge and fixed time gap; no altered risk limits. |

Focused read-only Python 3.12/pandas reproduction returned exit 0 with assertions on the negative-shift prefix violation, lower-hit mismatch, DatetimeIndex IndexError and unit mismatch. Alignment/censoring findings additionally follow directly from the supplied function; they were not covered by a full published-function regression suite. Existing production code does not contain these supplied new snippets and was not modified.

## Corrections to the supervisor claims

A for-loop can implement a causal state machine correctly; vectorization is a performance choice, not a scientific necessity. Stateful lifecycle and availability matter more than loop syntax. avg_body including the current closed bar is not future leakage, although the proposed previous-ten-bar threshold excludes it by definition.
Future observations are legitimate for retrospective labels. They become leakage when labels enter feature fitting, models train before those outcomes are available, or label intervals overlap an evaluation boundary.
A bidirectional recurrent network is not inherently noncausal if its entire input window ends before the decision time; future-reaching windows or full-stream smoothing are noncausal. It is nevertheless outside R1.
Stacking is an untested challenger, not a necessary fusion layer or guaranteed improvement. Any later meta-learner must train on chronologically out-of-fold base predictions; calibration requires a further independent block.
Purging overlap and an embargo reduce specified leakage routes; neither eliminates autocorrelation. Five percent is not a universal financial-ML constant. Walk-forward answers chronological deployment questions; CSCV/PBO and CPCV answer different diagnostic questions and cannot be renamed interchangeably.
Normalized cloud width is thickness, not by itself breakout strength. A tie policy is an OHLC modeling assumption; it cannot reveal an unknown intrabar path. vectorbt fees/slippage alone do not supply the requested depth/latency/non-atomic lifecycle model.
No finite audit certifies Bug-Free, Q1 level, institutional intent, first-of-kind novelty, or production readiness.

## Primary-source mapping checked in this audit

| Source | Locator / checked scope | Permitted use / limitation |
|---|---|---|
| Bailey & Lopez de Prado, DSR | [Author PDF](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf), July 31 2014 version; section The Deflated Sharpe Ratio, Eq. (2), PDF pp. 8-9; Appendix A.3 pp. 14-15. Journal DOI [10.3905/jpm.2014.40.5.094](https://doi.org/10.3905/jpm.2014.40.5.094). | Selection-adjusted return statistic, including sample moments and trial accounting. Dependent trials require declared handling; not a classifier or guaranteed discovery. |
| Bailey et al., PBO | [Author PDF](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf), section 2 framework/CSCV; DOI [10.21314/JCF.2016.322](https://doi.org/10.21314/JCF.2016.322), journal identity confirmed through author institution repository. | Probability of selection overfit on an aligned strategy-performance matrix. Not chronological predictive validation or a profitable-execution certificate. |
| Hassan et al., Informatica | [Official 14-page PDF](https://www.informatica.si/index.php/informatica/article/download/12899/6712), DOI [10.31449/inf.v50i13.12899](https://doi.org/10.31449/inf.v50i13.12899), sections 3.4, 3.6-3.7, PDF pp. 5-6. | Structural definitions, independent calibration and constrained sizing comparator on XAUUSD; project OB definition differs and LP is not implemented. No transferred performance. |
| Deng et al., IJFE 2021 | Publisher abstract at [10.1002/ijfe.2067](https://onlinelibrary.wiley.com/doi/abs/10.1002/ijfe.2067); full endpoint redirected to abstract. | Abstract supports lack of consistent value across subperiods in the studied stock/FX setting. Full-method replication NOT_VERIFIED; do not generalize to every regime or crypto. |
| Deng et al., ESWA 2023 | Publisher indexed abstract and DOI [10.1016/j.eswa.2022.119326](https://doi.org/10.1016/j.eswa.2022.119326), volume 215, article 119326. Full-page retrieval failed. | Ichimoku/FRS/NSGA-II/sliding-window crude-oil methodology lead. PRIMARY_ABSTRACT_ONLY; not a verified full PDF, DRL paper or crypto result. |
| Tangirala 2026 | Primary SSRN indexed abstract, [10.2139/ssrn.7483658](https://doi.org/10.2139/ssrn.7483658), title Do Smart Money Concepts Predict Returns? Evidence from 44 Formalised Detectors on S&P 500 Daily Bars, 2010-2026. Direct full-page fetch failed. | Preprint research lead. FULL_TEXT_NOT_VERIFIED; exclude blanket claims that SMC has been disproved or that all detector results survive/fail correction. |
| scikit-learn stacking | [Official StackingClassifier docs](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.StackingClassifier.html), cv/prefit warning. | Engineering source for independent meta-training predictions; not trading performance evidence. |

No systematic review of 2000-2026 or journal-quartile ranking was completed. An unavailable full PDF is an explicit remaining source gap, not silently upgraded evidence. Full papers were read through the web tool; no local PDF files were downloaded in the read-only workspace.

## Rewritten engineering instruction: R1 only

Prepare a documentation-only design snapshot for BTC_USDT closed 1h bars and derived complete 4h context. Define each detector mathematically with confirmation availability, state transitions, equality, missingness, expiration, invalidation, deterministic IDs and falsifiable fixtures. Call discretionary-derived features project operational definitions. Preserve immutable legacy outputs and canonical contracts. Keep RESEARCH_ONLY=true, PAPER_EXECUTION=false, LIVE_EXECUTION=false; no orders, models, training, new limits or claim of alpha.

Provide the independent baseline protocol, cost/label distinction, all-variant search accounting, prospective binding requirements and fail reason codes. Never call an already-inspected dataset preregistered. Keep new horizon/barrier/cost/model changes under an explicit experiment amendment. Separate Brier/ECE, predictive targets and executable strategy equity returns. DSR/PBO remain NOT_RUN until the net-return matrix, trial history and statistical prerequisites exist.
Deliver the four ADR definitions, a parseable protocol JSON, a content-hash manifest and this review. Do not execute R2-R4 or claim their acceptance tests have passed.

## Next gate

The R1 design snapshot is reviewable; experimental preregistration is incomplete. Missing bindings: auditable data/hash, inspected-window inventory, demonstrably uninspected holdout or future first-seen plan, future implementation identity, frozen execution assumptions and inference/dependence policy. Old synthetic and reported historical data are diagnostic only. The public Binance run still ended in HTTP 451 and is not evidence of a scientific rejection. Review/bind those inputs before R2 comparison or R4 statistics; no new API fetch or trade is performed by this R1 package.
