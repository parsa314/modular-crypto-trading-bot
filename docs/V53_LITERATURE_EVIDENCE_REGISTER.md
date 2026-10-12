# v0.53 — Literature & Dissertation Evidence Register (Seed)

Status: **INITIAL SYSTEMATIC-REVIEW SEED — NOT FINAL**

This register separates peer-reviewed evidence, working papers/preprints, doctoral theses, and practitioner sources. Direct claims about ICT/SMC are not upgraded to academic evidence unless independently supported.

## A. Core validation / data-snooping literature

1. **White, H. (2000). A Reality Check for Data Snooping. Econometrica, 68(5), 1097–1126.** DOI: `10.1111/1468-0262.00152`.
   - Tier: peer-reviewed / foundational.
   - Use: controls repeated strategy search and benchmark comparison.

2. **Hansen, P. R. (2005). A Test for Superior Predictive Ability. Journal of Business & Economic Statistics, 23(4), 365–380.** DOI: `10.1198/073500105000000063`.
   - Tier: peer-reviewed / foundational.
   - Use: SPA as a more powerful search-aware predictive-superiority test than standard Reality Check in many settings.

3. **Bailey, D. H. & López de Prado, M. (2014). The Deflated Sharpe Ratio. Journal of Portfolio Management, 40(5), 94–107.** DOI: `10.3905/jpm.2014.40.5.094`.
   - Tier: peer-reviewed.
   - Use: adjusts Sharpe claims for selection bias / multiple trials and non-normal returns.

4. **Bailey, D. H., Borwein, J., López de Prado, M., Zhu, Q. J. (2017). The Probability of Backtest Overfitting. Journal of Computational Finance.** DOI: `10.21314/JCF.2016.322`.
   - Tier: peer-reviewed.
   - Use: CSCV/PBO; requires retention of the full tested strategy family.

## B. Crypto order flow / microstructure / ML

5. **Anastasopoulos, A., Gradojevic, N., Liu, F., Maynard, A., Tsiakas, I. (2026). Order flow and cryptocurrency returns. Journal of Financial Markets, 79, 101047.** DOI: `10.1016/j.finmar.2026.101047`.
   - Tier: peer-reviewed / current.
   - Evidence: world order flow has explanatory and out-of-sample predictive content; non-linear ML using order flow can add economic value.
   - Relevance: strongest current academic basis for testing objective order-flow / liquidity features alongside practitioner price-action concepts.
   - Important caveat: this is not evidence that ICT/SMC labels themselves possess alpha.

6. **Bysik, A. & Ślepaczuk, R. (2026). Machine Learning-Based Bitcoin Trading Under Transaction Costs: Evidence From Walk-Forward Forecasting.** SSRN.
   - Tier: working paper / preprint.
   - Evidence: selected gross forecasting/trading results can disappear under realistic 10 bp transaction costs; walk-forward and cost-aware execution matter.
   - Use: methodological support only until publication status is stronger.

7. **Interpretable trading pattern designed for machine learning applications (2023). Machine Learning with Applications, 11, 100448.** DOI: `10.1016/j.mlwa.2023.100448`.
   - Tier: peer-reviewed.
   - Relevance: supports converting price/volume patterns into interpretable, statistically testable ML representations rather than subjective chart annotation.

## C. Ichimoku evidence

8. **Technical Analysis, Fundamental Analysis, and Ichimoku Dynamics: A Bibliometric Analysis (2023). Risks, 11(8), 142.**
   - Tier: peer-reviewed review/bibliometric study.
   - Key finding for this project: the literature directly testing Ichimoku predictive ability is limited relative to broader technical/fundamental-analysis research.
   - Research implication: Ichimoku is suitable as a testable feature family, not an assumed profitable rule.

9. **Cahyadi, Y. (2012). Ichimoku Kinko Hyo: Keunikan dan Penerapannya dalam Strategi Perdagangan Valuta Asing. Binus Business Review, 3(1), 480–492.** DOI: `10.21512/bbr.v3i1.1336`.
   - Tier: peer-reviewed journal, limited design rigor relative to modern ML/OOS standards.
   - Use: historical evidence / methodology description, not strong causal proof.

10. **Bąk, B. (2015). Effectiveness of Ichimoku Technique on the Example of Index Futures on WIG20. Annales UMCS Sectio H.** DOI: `10.17951/h.2015.49.4.35`.
    - Tier: peer-reviewed.
    - Use: historical evidence on rule-based Ichimoku signal effectiveness; must be re-tested under modern cost/OOS controls.

11. **Patel, M. Trading with Ichimoku Clouds (Wiley), including Ichimoku Backtesting / Strategies chapters.** DOI family: `10.1002/9781119200208.*`.
    - Tier: practitioner/professional book, not independent empirical proof.
    - Use: source ontology for deterministic Ichimoku rules.

## D. Al Brooks price action

12. **Brooks, A. Trading Price Action Trends / Trading Ranges / Reversals (Wiley, 2012).** Book DOI: `10.1002/9781119202592` and related volumes.
    - Tier: practitioner/professional source.
    - Observable concepts: Always-In, trend vs trading range, signal bars, second entries, failed breakouts, pullbacks, trend-from-open.
    - Use: concept specification only. Every concept must be transformed into deterministic code and independently tested.

## E. ICT / SMC research status

Initial targeted searches for the literal labels **Inner Circle Trader**, **ICT**, **Smart Money Concepts**, **Order Block**, **Fair Value Gap**, and related retail terminology produced substantially less standardized peer-reviewed research than searches for order flow, market microstructure, liquidity, price action, technical analysis and Ichimoku.

This is recorded as a **research-gap observation, not a claim that no academic paper exists**.

Scientific treatment:
- practitioner definitions are retained for ontology traceability;
- institutional-intent stories are not treated as measured truth;
- constructs are rewritten as observable market events;
- economic value is tested against microstructure and simple price-action baselines.

## F. Relevant doctoral theses / dissertations — initial verified set

13. **Spooner, T. (2021). Algorithmic Trading and Reinforcement Learning: Robust methodologies for AI in finance. PhD thesis, University of Liverpool.**
    - Focus: realistic RL methodology, partial observability, non-stationarity, robust evaluation.

14. **Sethi, M. (2016). Applied Machine Learning for Systematic Equities Trading: Trend Detection, Portfolio Construction and Order Execution. Doctoral thesis, UCL.**
    - Focus: ML across trend detection, portfolio construction and execution.

15. **Nagy, P. (2025). Learning the market: machine learning and generative modelling for limit order books. PhD thesis, University of Oxford.**
    - Focus: LOB modelling, RL, generative market simulation, standardized evaluation.

16. **Jain, K. (2026). Microstructural Financial Modelling: Point Processes and Reinforcement Learning. PhD thesis, UCL.**
    - Focus: point-process LOB models, stochastic control, RL and robustness under low signal-to-noise.

17. **Almubarak, M. (2024). Integrating Sentiment and Technical Analysis with Machine Learning for Improved Stock Market Predictions. PhD thesis, University of Dundee.** DOI/record: `10.15132/20000537`.
    - Focus: technical indicators + ML, time-series CV, statistical and financial metrics.

18. **Zhao, Q. (2026). Novel Trading Algorithms augmented by Intrinsic Time and Machine Learning. Doctoral thesis, University of Essex.** DOI: `10.5526/ERR-00043797`.
    - Focus: directional-change event time, trend/counter-trend algorithms and ML.

19. **Zhang, M. (2020). Essays on the microstructure of US equity options. PhD thesis, University of Essex.**
    - Focus: liquidity, quote-driven market microstructure, HFT and price discovery.

20. **Abdulkarim, O. (2019). Topics in Market Microstructure. PhD thesis, University of Essex.**
    - Focus: algorithmic/HFT effects, E-LOB reconstruction, directional changes.

21. **Using Modified Rainbow for Enhancing Reinforcement Learning for Stock Trading — NASDAQ Stocks as Examples (2019). Doctoral thesis, National Cheng Kung University.**
    - Focus: MDP/DQN/Rainbow variants and trading environment design.
    - Caveat: reported very large returns require independent robustness scrutiny.

22. **Development of a foreign-exchange prediction model based on Multi-Critic Reinforcement Learning with macroeconomic announcements and technical indicators (2026). Doctoral thesis, Diponegoro University.**
    - Focus: multi-critic DDPG, technical indicators, macro information, adaptive trading.

This seed contains ten doctoral-level dissertations/theses or doctoral-thesis records directly relevant to the research program. The target is **12–15 high-quality doctoral dissertations** after full screening.

## G. Provisional research gaps

1. No standardized academic ontology unifies ICT/SMC, Brooks and Ichimoku into leakage-safe measurable features.
2. Practitioner claims are rarely tested against strong microstructure baselines and realistic execution costs.
3. Multi-timeframe confluence is often tuned post hoc rather than preregistered.
4. Many trading studies optimize forecast accuracy while economic utility after costs is weak or unstable.
5. Search intensity and failed trials are frequently underreported, inflating apparent Sharpe/performance.
6. Regime-specific effects are often discovered retrospectively, creating another selection layer.
7. RL is commonly introduced before establishing that the upstream signal contains stable predictive information.
8. Realistic stale-data, reconciliation, partial-fill and restart semantics are often omitted from academic prototypes.

## H. Systematic-review expansion protocol

The full review will search and screen:
- Scopus / Web of Science indexed literature where accessible;
- Crossref/publisher records;
- arXiv;
- SSRN;
- institutional doctoral repositories;
- high-quality books only for practitioner ontology, never as substitute for empirical evidence.

Each source will receive:
- bibliographic metadata;
- evidence tier;
- market / timeframe;
- features;
- model;
- validation design;
- transaction-cost treatment;
- OOS protocol;
- metrics;
- leakage/selection-risk assessment;
- reproducibility/code status;
- direct implication for v0.53.

The final evidence table will separate **source-reported results** from **our interpretation**.
