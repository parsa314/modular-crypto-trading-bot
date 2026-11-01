# MASTER-GATE-QUANT-001: pre-implementation protocol

Status: RESEARCH ONLY; no economic, broker, release or execution approval.
Research reference `1674cf967f4970066e4b442dca65b9499e8467c5` (PR #146);
financial reference `81071995fd7c5a27830d92780801cd0783e108d9` (PR #145).
The research head is descended from `3f6ceca5b87bead9417172253d74278a3ccb375b`,
not from the financial head. PR #146 targets the financial branch; this task's
branch is based on the research head, so no financial delta is silently imported.
Open PR branch and SHA snapshot: `MASTER_QUANT_001_OPEN_PR_SNAPSHOT.json`.

## Setup-selection criteria, fixed before implementation or measurement

Criteria are qualitative gates, not optimized scores: (1) observable with the
available real OHLCV tier, (2) event and invalidation have causal definitions,
(3) independent timestamps support the chosen contexts, (4) no unobserved
queue/order-book/funding required, and (5) execution cost can be isolated later.
Historical return, win rate, frequency and model performance are not inputs to
this selection. On these criteria select **Ichimoku H4-trend/H1 Kijun pullback,
long-only spot**, version `ICH_KIJUN_PULLBACK_V1`. ICT/SMC liquidity ordering
requires more proxy choices; Brooks higher-order patterns need additional
definitions. This ordering asserts neither stronger alpha nor unique novelty.

### Hypothesis and complete research contract

- H4 trend: completed H4 close above both visible cloud lines and Tenkan > Kijun;
  visible cloud uses a 26-bar historical shift. H1 trigger: previous close above
  previous Kijun; current low touches/crosses current Kijun, current close above
  current Kijun and above open. All observations must be available by decision.
- `trigger` identifies the completed H1 bar; `confirmation` and `market_regime`
  describe the H4 conditions; these are observable proxies, not a trained regime.
- The intended hypothetical entry is the first H1 open **strictly after** decision,
  provided it is no more than two hours later and exceeds the trigger-bar low.
  The trigger low is the structural invalidation and hypothetical stop level.
- Target is the actual hypothetical entry plus twice (entry minus stop). Exit
  timeout is eight completed H1 bars from the entry bar. Cancel if the entry
  opportunity is absent/late, entry <= stop, stale data, a hard risk veto or an
  unavailable verified execution policy. Intrabar stop/target ordering is unknown.
- Decision freshness: last H1 availability <=15 minutes old and last H4
  availability <=4 hours 15 minutes old. Missing, noncontiguous, misaligned,
  incomplete or invalid bars fail closed. At least 90 bars per timeframe are
  needed by the existing causal validator. A setup is research-only and MUST NOT
  become an order; costs, size, tick/step and target evaluation remain unverified.
- Source identifier, raw-file SHA256 and availability semantics are inputs and
  recorded separately for each timeframe. Hashes establish content consistency,
  not source authenticity or genuine first-seen evidence. The code computes an
  additional hash of the exact normalized PIT prefix used by each timeframe.

## Existing capability and gaps at research reference

| Component | Code and test | Actual evidence | Gap/risk | Priority |
| --- | --- | --- | --- | --- |
| Four families | `research_bot/strategies/{ichimoku_strategy,ict_strategy,smc_strategy,al_brooks_strategy}.py`; `tests/test_four_independent_strategies.py` | Deterministic OHLC proxies and PIT tests | Not complete discretionary frameworks; no independent economic evidence | P0 |
| Confluence | `research_bot/strategies/confluence.py` | Equal weights/score threshold and conflict veto | ICT/SMC correlation unmeasured; score is not probability | P0 |
| PIT contract | `research_bot/strategy_research_012.py`; `tests/test_strategy_research_012.py` | Closed/available, alignment, gaps and synthetic mutation tests | Caller first-seen claim/source identity not independently authenticated | P0 |
| Regime/labels | `research_bot/strategy_regime_012.py`, `strategy_labels_012.py` | Fixed parameters and ambiguous-label handling, synthetic tests | No fitted regime, calibration, cost evaluation or independent OOS | P1 |
| Decision/risk | `research_bot/decision.py`, `risk.py` | Research decision and risk veto exist | Research reference retains float boundary; financial improvements live on separate branch | P0 |
| Paper | `research_bot/paper_replay_008.py`; `tests/test_paper_replay_008.py` | Cash baseline, historical nonprospective replay and isolated tests | No operational release; PAPER-011 is BLOCKED | P0 |
| Data | `data/btc_usdt_m15_2022_2024.manifest.json` at source SHA below | Spot historical 15m, checksum manifest | 5 missing bars in 2022–24, first-seen and historical trading filters unverified | P0 |

Existing PR #146 CI: run `41243904692`, merge checkout
`f7aa97c764cb6000ba95c8b9eefc887bc127205d`, 36 passed; this is not a test
of this new setup. Historical result on 96 already-seen bars is a data smoke,
not untouched OOS. No new historical/economic test is authorized in this first PR.

## Data sufficiency and provenance for this setup

- Tier A: Binance Spot BTCUSDT M15, manifest at commit
  `cda9e5ff47d0f40cb62b6734c784317d2f15e405`; parquet SHA256
  `f7bf84ccaff343dbb627c17e6245ae6f2488f940a3ed6867efd262115ea9d401`.
  Manifest reports 105211/105216 M15 rows, 5 missing, zero duplicate. This has
  not been independently re-downloaded in this phase. BTC/USDT continuous UTC
  24/7 is a model of the venue calendar, not a guarantee of venue availability.
- H1 and H4 must be derived from exactly 4 and 16 contiguous M15 bars,
  respectively; each derived bar is available no earlier than its latest source
  availability and its own close. Missing constituents veto that derived bar.
  Historical bar close is only an availability assumption, not a first-seen log.
- Price tick, quantity step, min notional, fee tier and their historical changes:
  `TO_BE_VERIFIED`; no execution compatibility or real costs claimed.
- Tier B trades/bid-ask and Tier C depth/funding/OI: `DATA_UNAVAILABLE` in this
  phase; no synthetic order book, spread, liquidity, or queue data may be imputed.
- Binance Vision's [published data terms](https://github.com/binance/binance-public-data/blob/master/TERMS_AND_CONDITIONS.md)
  describe a noncommercial license and restrictions. The exact version and
  applicability to any later use require review; no commercial authorization is inferred.

## Phase-4 reference test design, not an implementation

1. Information at decision must precede first eligible execution; reject incomplete
   bars, stale/duplicate events and conflicting source identities.
2. L1 OHLC ambiguity: if stop and target are both touched in one bar, return
   AMBIGUOUS or a separately preregistered conservative scenario; never invent ordering.
3. Next-open gaps, stop gap-through, cancellation/expiry and no-fill each retain
   explicit state; no same-close fill or silently guaranteed stop price.
4. L2 bid/ask and L3 book/queue tests require recorded data of those tiers; absent
   data produces DATA_UNAVAILABLE. L1 results cannot be represented as L2 or L3.
5. Fees, spread, slippage and latency must be separated and not double counted;
   verify Decimal, exact position accounting, idempotency and restart in the
   existing OMS before any operational integration.
6. Compare NautilusTrader [LGPL-3.0](https://github.com/nautechsystems/nautilus_trader/blob/develop/LICENSE)
   and LEAN [Apache-2.0](https://github.com/QuantConnect/Lean/blob/master/LICENSE)
   on compatibility, legal obligations, performance and behavioral parity before
   choosing either. No external engine or OMS is installed or selected here.

Scientific results: NOT_EXECUTED. Order execution, broker access, ML/RL training,
holdout selection, cost calibration and Phase 4 simulation are outside this PR.
