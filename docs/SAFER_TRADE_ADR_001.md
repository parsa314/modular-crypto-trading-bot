# SAFER-Trade ADR 001 — research integration, version 0.1.0

Status: ENGINEERING / DRAFT_PROTOCOL. Base 8855c1bc9151f75bd5c06a01cb2bd3ea44aec74e.

SAFER-Trade is a research path inside the canonical repository, not a second bot.
No frozen v0.50/v0.51 definition, calendar, holdout or operational risk policy is
changed. main and PR #140 are not merged by this increment.

The proposed independent study is USDT-linear perpetual BTC/ETH/SOL, H1 context
with closed H4 features. Venue, usable dataset, funding history, model digests,
selection budget and an unconsumed evaluation window must be locked before any
scientific acceptance. January–September 2026 is not assumed untouched. Current
Spot/H4 runtime scope is not changed. Kraken remains sealed.

## Contracts and ownership

- Canonical EvidenceStamp, SignalEvidence, Decision, ResearchStatus and
  ExperimentManifest remain the evidence/decision contracts. A TargetAllocation
  contains proposals; it is neither a trading Decision replacement nor approval.
- The FinRL-X adapter consumes the pinned StrategyResult.weights interface.
  Calling generate_weights and training a model are not implemented here. No
  upstream trainer, broker or internal backtester owns financial state.
- NautilusTrader is a proposed optional BACKTEST reference; this increment only
  checks the installed version and does not run the engine. Its engine alone owns fills
  and NAV inside a reference run. No simultaneous canonical settlement writes
  are permitted. The PostgreSQL OMS remains the authority on its own operational
  path; adopting another operational authority requires a separate ADR/review.
- TradingAgents cached outputs require timestamped evidence and bounded inference
  cost; upstream network tools and paid LLM execution are not enabled. Self-rated
  confidence is not treated as calibrated probability.
- Target quantities are exact Decimal, use contract multiplier, tick and step.
  Risk-increasing and risk-reducing proposals are distinct. No planner submits
  orders, resets a durable halt, or mints RiskApproval.

## Testable increment

G1/G2/G6 are the primary research questions; G3/G4/G8 are acceptance infrastructure.
G5, G7, G9, G10 have explicit extension contracts and missing-data gates, not
invented results. B0/B1 run before B2. PPO training remains blocked until simpler
baseline gates and a separately locked RL protocol pass; B3 also needs PIT text.

The executable evaluation path accepts checksum-bound, complete portfolio return
panels for every registered trial, including losers. It reports daily paired
block-bootstrap inference, Holm correction, cost stress, descriptive drawdown/
CVaR/regime metrics, CSCV PBO and conditional DSR. It never imputes absent trial
returns to zero. Inferential acceptance requires fresh independent evidence.

CSCV and CPCV are distinct. Walk-forward splits purge actual label intervals and
apply a declared time embargo. CPCV alone does not produce PBO. Block-resampled
null means are centered before p-value calculation. DSR is NOT_RUN when search
dispersion, trial accounting or serial-dependence assumptions are unsupported.
No full-sample HMM smoothing, calibration fitting on test data or spent-test reuse.

Gross returns, one-way turnover, fees, spread/impact, funding and inference cost
are separate. Execution-price spread cannot also be deducted as an explicit
spread line. Fixed roundtrip 0/24/36/50 bps are declared scenarios, not measured
historical account fees. Zero-cost results never authorize promotion.

## Evidence and limitations

SQLite append-only hash chaining is tamper-evident under a trusted external head;
it is not cryptographic proof against an administrator rewriting the whole file.
Trial registration uses the existing v17 record shape. This increment does not
certify novelty, profitability, Demo connectivity, market impact or LIVE safety.
Real datasets, model artifacts, external anchors and prospective evidence may be
DATA_UNAVAILABLE/BLOCKED. Unit fixtures produce ENGINEERING evidence only.

Acceptance: typed contracts, PIT/HTF rejection, frozen-protocol digests, no leaked
split, complete trial matrix, exact allocation rounding, no short via Spot, no
direct order path, cost double-count rejection, negative results retained,
reproducible statistics and tested optional-engine version detection.

## Primary references and version observations

- FinRL-X is a workshop/preprint system paper, not evidence of Q1 profitability:
  https://arxiv.org/abs/2603.21330 ; upstream code examined at
  `4409abe925c904e570be78ebfb5e77ac3491dff8`, Apache-2.0.
- NautilusTrader bar execution synthesizes an intrabar path from OHLC and may
  fill an order on the same bar's close without an explicit latency restriction:
  https://nautilustrader.io/docs/latest/concepts/backtesting/bar-execution/ .
  Candidate release 1.231.0 resolves to commit
  `27a8e54e7ac3c57d6cbf8891f0283dfbaee97317`, LGPL-3.0; API compatibility
  with current 2.x documentation must not be assumed.
- TradingAgents is a research framework; its findings are not validation of this
  crypto system: https://arxiv.org/abs/2412.20138 ; upstream inspected commit
  `c4544913a14ed3df5f1ed7b9e8483270ce20682a`, Apache-2.0.
- CSCV/PBO: https://scholarworks.wmich.edu/math_pubs/42/ ; DSR:
  https://doi.org/10.3905/jpm.2014.40.5.094 . Neither replaces an independent test.
- Holm multiple-testing adjustment: https://www.ime.usp.br/~abe/lista/pdf4R8xPVzCnX.pdf .
- RL seed uncertainty: https://ojs.aaai.org/index.php/AAAI/article/view/11694 .
- Execution impact model motivation: https://doi.org/10.21314/JOR.2001.041 ;
  model coefficients require real market calibration.
