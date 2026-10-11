# Economic acceptance: basis, protocol and limits

Status: proposed course-research protocol, not a change to frozen v0.51 or a
certificate of profitability. Execution remains RESEARCH_ONLY / PAPER_OFF /
LIVE_OFF. ENGINEERING_EXTENSION; statistical methods are not attributed to the
educational notebooks. Base: `fa31e6f5ceb09f4f14ae13fef50706f2e10e1835`.

## Frozen historical pilot

`research_bot/course/configs/course_economic_pilot_v1.json` fixes the five assets,
six source candle baselines, 2025-Q1 entry window, warm-up from 2024-11-20,
unoptimized source-reference parameters, 30 comparisons, cost scenarios and
statistical choices **before** retrieving/inspecting pilot outcomes. This is a
retrospective protocol commitment, not prospective registration of old bars.
The first complete valid corpus in OKX / CoinEx / KuCoin priority is selected on
data availability alone. No best-period, best-venue or best-parameter selection.
An incomplete corpus blocks economic inference; no synthetic replacement.

The fill model V2 uses only the candle prefix available strictly before each
entry open. Provider open/close timestamps are never shifted to manufacture a
fill. Historic availability at close + 5 seconds is an explicit assumption,
not first-seen evidence. Orders are not sent. OHLC stop-before-target behavior,
unit-notional PnL and hypothetical shorts are simulation assumptions. Events
still open at the boundary remain unlabelled; report boundary censoring rather
than inventing terminal fills. Grid/arbitrage require recorded depth and
failure-path replay; this OHLC pilot cannot economically validate them.

## Acceptance parameters and their authority

| Parameter | Proposed test / interpretation | Authority and limitation |
|---|---|---|
| Net economic edge | Mean directional PnL after measured roundtrip fees, spread, slippage, impact and applicable financing; compare a stated feasible benchmark on matched windows | Accounting identity; official venue fee schedule plus observed executions, not gross accuracy. The 0/24/36/50 bps grid comes from the owner's requested protocol and is a sensitivity grid, not a verified account fee. |
| Statistical uncertainty | Paired moving-block bootstrap of event differences; positive lower 95% CI and one-sided Holm-adjusted p <= 0.05 across the full 30-family; missing comparisons conservatively p=1 | Künsch (1989), Holm (1979). Alpha=0.05 and block sizes 4/8/16 are explicit study choices, not universal acceptance laws. Dependence coverage, stationarity and sample support must be assessed; event blocks do not establish portfolio/calendar-time validity. No p-value is computable with insufficient support. |
| Cost resilience / turnover | Report all four fixed scenarios and net PnL per one-way notional turnover. No cost scenario is discarded after inspection | Project-specific protocol. Failure at a plausible measured stress cost is evidence against deployability; fee tier, currency and impact must be observed before promotion. |
| Sharpe / PSR / DSR | Only on a justified return clock; DSR needs complete search lineage, effective independent trial count and non-normal moments | Lo (2002); Bailey–López de Prado (2014). No universal Sharpe > 1 / DSR > .95 rule; do not annualize irregular trade returns by sqrt(2190). Missing assumptions yields NOT_APPLICABLE. |
| Overfitting / PBO / CPCV | CSCV PBO only with a complete comparable strategy panel and a defensible design; purged validation separates overlapping labels | Bailey et al. (2017), López de Prado (2018), chapters 4/7/12. CSCV is not full trained CPCV. PBO < .20 is not a universal law. No number fabricated from one isolated strategy. |
| Concurrency / uniqueness | Report time-weighted [entry, exit) average uniqueness and concurrency; use training-only uniqueness/bootstrap where training exists | López de Prado (2018), chapter 4. No fixed uniqueness cutoff proves economic edge. This pilot has no fitted model and no sequential-bootstrap training claim. |
| Portfolio tail risk / drawdown | Compare funded, feasible portfolio-account CVaR and drawdown to the existing approved risk constitution | Rockafellar–Uryasev (2000) provides CVaR mathematics, not the bot's risk budget. `risk.py` on the audited main defines DD 12%, gross 100%, asset 35%, turnover 50%, spread 35 bps, slippage 25 bps, CVaR95 loss 3.5%, cash 5%. These are existing policy budgets, not empirically optimal constants. Do not compare unit-notional event CVaR to account-return limits. |
| Economic promotion | Independent chronological OOS, measured frictions, sufficient tail/sample support, causal first-seen forward data, reproducible outcomes, independent risk review | Project governance / AGENTS.md. Historical pilot success cannot authorize execution or unseal the holdout. |

The no-trade benchmark is a minimal profitability hurdle; it does not prove
incremental value over the canonical strategy or buy-and-hold. Those need
aligned funded portfolio comparisons under a separately committed protocol.
There is no risk-engine rewrite or automatic scientific promotion here.

## Decision semantics

`CONDITIONAL_POSITIVE_PILOT`: positive net event estimate, primary CI lower > 0,
Holm-adjusted p <= .05 and positive CI at both sensitivity block sizes, with all
assumptions displayed. This still produces `ECONOMIC_CONFIRMATION_DEFERRED` until
independent OOS, causal availability, measured costs, capacity, funded portfolio
risk, sufficient evidence and governance approval are present.

`CONDITIONAL_NEGATIVE_PILOT`: negative net event estimate and entire primary CI
below zero, under the stated assumptions. Other outcomes are `INCONCLUSIVE`.
`DATA_UNAVAILABLE` is an infrastructure/evidence blocker, never a scientific
rejection. None of these statuses changes execution flags.

## Primary bibliography (verified 2026-10-11)

1. Künsch, H.R. (1989), *The Jackknife and the Bootstrap for General Stationary
   Observations*, Annals of Statistics 17(3), 1217–1241.
   [Author publication catalogue](https://people.math.ethz.ch/~hkuensch/papers/).
2. Holm, S. (1979), *A Simple Sequentially Rejective Multiple Test Procedure*,
   Scandinavian Journal of Statistics 6(2), 65–70.
   [Original paper scan](https://www.ime.usp.br/~abe/lista/pdf4R8xPVzCnX.pdf).
3. Lo, A.W. (2002), *The Statistics of Sharpe Ratios*, Financial Analysts Journal
   58(4), 36–52, doi:10.2469/faj.v58.n4.2453.
   [CFA Institute](https://rpc.cfainstitute.org/research/financial-analysts-journal/2002/the-statistics-of-sharpe-ratios).
4. Bailey, D.H. and López de Prado, M. (2014), *The Deflated Sharpe Ratio*, Journal
   of Portfolio Management 40(5), 94–107, doi:10.3905/jpm.2014.40.5.094.
   [Publisher](https://www.pm-research.com/content/iijpormgmt/40/5/94).
5. Bailey, D.H., Borwein, J.M., López de Prado, M., and Zhu, Q.J. (2017), *The
   Probability of Backtest Overfitting*, Journal of Computational Finance 20(4),
   39–69, doi:10.21314/JCF.2016.322.
   [University publication record](https://scholarworks.wmich.edu/math_pubs/42/).
6. López de Prado, M. (2018), *Advances in Financial Machine Learning*, Wiley,
   ISBN 9781119482086.
   [Publisher contents](https://www.wiley-vch.de/en?isbn=9781119482086&option=com_eshop&view=product).
7. Rockafellar, R.T. and Uryasev, S. (2000), *Optimization of Conditional
   Value-at-Risk*, Journal of Risk 2(3), 21–41.
   [Author publication record](https://uryasev.github.io/publications/).
8. [Official OKX candle documentation](https://www.okx.com/docs-v5/en/#rest-api-market-data-get-candlesticks-history),
   [official fee explanation](https://www.okx.com/en-gb/help/trading-fee-rules-faq),
   [CoinEx public candles](https://docs.coinex.com/api/v2/spot/market/http/list-market-kline),
   [KuCoin public candles](https://www.kucoin.com/docs-new/rest/spot-trading/market-data/get-klines).
   Documentation availability does not establish successful data collection or
   actual account fee tier.
