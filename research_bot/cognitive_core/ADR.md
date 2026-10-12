# CC_DETECTOR_STUDY_01 — ADR lock 1.0.0

Engineering freeze before bridge implementation. No economic experiment executed.
This is an extension of canonical contracts, not a replacement trading system.

## Authority and amendments
AGENTS.md governs owner permission. This increment is journal-only, with no execution.
v0.50/v0.51 definitions, predictor, first-seen calendar and outcomes are unchanged.
The attached eight prompts are subordinate to the explicit canonical task: no new
Decision enum, journal database, exchange adapter or same-close execution.

## Detector definitions (all version 1.0.0)
All OHLCV bars must be closed, finite, unique, ordered and available at decision time.
Missing warm-up is UNKNOWN, never neutral/range. UTC timestamps are mandatory.
ATR14 is Wilder smoothing seeded by mean of first 14 true ranges; TR uses previous
close (first TR = high-low). Do not switch smoothing implementations silently.

| Detector | Definition / confirmation / invalidation | Independent oracle |
|---|---|---|
| ICHI_CONTEXT_NOW | T=midrange9, K=midrange26, A=(T+K)/2, B=midrange52; LONG: close>max(A,B),T>K,A>B; SHORT inverse, otherwise NEUTRAL. Require all 52 bars. No cloud shift or Chikou. | T105 K103 A104 B102 C106 LONG; C104 not LONG |
| CONFIRMED_SWING | Strict pivot with two bars on each side, available only at pivot+2 close. Ties yield no pivot. | highs 8,9,12,10,9: 12 confirmed at fifth close |
| BOS | Close above latest unbroken confirmed swing-high known by t-1; downside inverse. One event per level. | level100 H101 C99.8 false; C100.2 true |
| OB_FORM | Bullish BOS candle: body/range>=0.6 and body>=ATR14(t-1). Last bearish candle among preceding five is origin. Full [low,high] zone; available only at BOS close. No origin => no zone. | origin[98,100], body1.2, ATR1, ratio0.7 forms at BOS |
| OB_RETEST | First intersecting candle strictly after formation, ages1..20 inclusive. Bullish close>zone_high and close>open confirms. First touch consumes zone even without confirmation. | zone[98,100], L99 H101 O99.5 C100.5 true |
| OB_INVALIDATE | low<zone_low invalidates before retest evaluation, or expiry after20 bars; bearish inverse. Preserve invalidation record. | L97.9 C100.5 invalid, never successful retest |
| FVG | At t close: L_t>H_t-2; middle bullish candle body/range>=0.6, body>=ATR before middle candle. Zone[H_t-2,L_t]. First touch ages1..20 with bullish close>upper confirms. Low<lower invalidates first. | H_t-2=100,L_t=102 zone[100,102]; later L99 invalidates |
| SWEEP | Low below latest confirmed swing-low known by t-1 and close above it. One event per level. | level100 L99 C101 true; C100 false |
| PA_SECOND_ENTRY_LONG | While LONG, lower-low starts pullback; later higher-high is attempt1; later lower-low rearms; later higher-high with bullish body and close-location>=0.75 confirms. Separate candles; expire after10 bars from start or loss of LONG. | No confirmation on attempt1 or on same candle as rearm |
| INSIDE / OUTSIDE | strict H<priorH,L>priorL / strict H>priorH,L<priorL. Equality is neither. Descriptive only. Zero range body/CLV invalid. | H10 L5 following H11 L4 is inside |

OB/ICT are candle proxies, not evidence of institutional orders. SMC and ICT are
not independent traditions. Trigger count is never calibrated confidence.
PA proxy has an explicit identifier rather than incorrectly calling a buy L2.

## Strategies (distinct from V58 confluence10)
S1: transition non-LONG to LONG, one event per continuous LONG episode.
S2: LONG + fresh bullish BOS.
S3: LONG + OB retest.
S4: LONG + FVG retest.
S5: LONG + low sweep.
S6: LONG + PA_SECOND_ENTRY_LONG.
S7: LONG + FVG retest; BOS on FVG formation bar or preceding two bars.
S8: LONG + OB retest and sweep on same decision candle; invalidation first.
S9: S2 without Ichimoku, otherwise identical (explicit ablation exception).
S10: LONG + at least two fresh triggers among BOS/OB/FVG/SWEEP/PA on same candle.
All long-only spot. Deterministic event IDs include venue, asset, strategy,
confirmation timestamp, detector version, data snapshot digest and prereg digest.

## Entry / exit / cost freeze
Historical entry at next executable open after decision availability. Never replay
an already elapsed open after late collection. Primary stop distance=max(1.5*ATR_t,
0.0005*entry), target=3R,31 path candles including entry, TIMEOUT at last close.
STOP_FIRST for ambiguous same-bar barriers; gap below stop fills worse open;
positive target gaps receive no assumed improvement. No trailing or regime exit.
This deliberately selects PR87 semantics over PR86's 1ATR/1.5ATR/12-bar contract.
CC_COST_RT_BPS_v1: total round-trip 0/24/36/50bps (per side0/12/18/25).
24 primary,36 primary stress,50 severe stress,0 diagnostic. No double fee counting.
Measured depth/impact unavailable: mark DATA_UNAVAILABLE, never measured-cost proof.

## Validation and search freeze
Historical data is research-spent; re-splitting never makes it untouched.
Five expanding folds within historical development: first50% train, next five
10% decision-time blocks evaluate sequentially; integer boundaries floor(f*n).
Common timestamps across assets. No learned challenger in this increment.
Purge overlapping decision-to-label-end intervals at every boundary; embargo32 H4
bars. Validation and internal spent test retain inherited exact calendar boundaries.
T x10 matrix uses common-calendar net MTM portfolio returns, not event rows.
Missing evidence is null, genuine flat returns zero. A0 earliest-first primary.
A1 diagnostic only with compatible frozen scorer and identical candidate sets;
no new-data use of V51 predictor without separately reviewed compatibility.
PBO CSCV uses10 contiguous blocks,5/5 splits with interval purging/32-bar embargo;
undefined ranks/support => INCONCLUSIVE. DSR requires full search accounting,
non-normal moments and dependence treatment; unknown historical search count =>
NOT_RUN_SEARCH_HISTORY_INCOMPLETE, not a fabricated count of10.
Proposed confirmation gates: n>=200 post-gate trades, >=3 assets each>=20 trades,
five time blocks each>=20; power remains unestablished, so floor alone cannot pass.
Mean net>0 at24/36bps, primary95% moving-block bootstrap lower bound>0.
Bootstrap10000 samples, block32 H4 rows, seed42; paired delta vsS1, Holm alpha.05
for S2..S10. PBO<=.10,DSR>=.95 only when applicable and validly computed.
Sensitivity +/-10% on OB/FVG lifetimes (18/22) and displacement thresholds
(.54/.66 body fraction,.9/1.1 ATR), one-at-a-time, diagnostic only; no winner swap.
Every sensitivity/model/policy tried counts in search history. No same-test rescue.
Risk governor unchanged. No model training, economic replay or promotion in bridge PR.

## Canonical bridge and evidence boundary
Reuse SignalEvidence, EvidenceStamp, DecisionEngine and PipelineOutcome.
A typed detector event is an input DTO, not a second Decision/Hypothesis hierarchy.
Unscored events produce NO_TRADE with explicit UNESTIMATED metadata; zero placeholders
are not forecasts. Scored records require finite gross expected return, nonnegative
cost/penalties, confidence in[0,1], immutable scorer identity and PIT availability.
Validate lineage, causal times and supported side before calling DecisionEngine.
Research orchestrator path returns no fill and never calls execution adapters.
Existing trial registry retains HYPOTHESIS status; BLOCKED engineering state belongs
in the study manifest and event reasons, not invented ResearchStatus enum members.

## Window and release
Reserve 2027-03-01T00:00Z to2027-07-29T00:00Z (150days,900H4bars) for future
confirmation only; disjoint from known V51/V58 windows with temporal buffer.
No evidence exists now. Collector not deployed; collection BLOCKED. Timely first-seen
<=60min after close; first_seen immutable; revisions fail closed; no backfill promotion.
Five30day blocks; all five assets require coverage. Decision/exit fully inside window.
Outcomes sealed until end and audit; collection availability alone never permits tuning.
Global untouchedness requires an access-ledger audit before release; not asserted here.
Freeze/implementation commit timestamps must precede window start. Missed collection
is BLOCKED, never a moving/restarted window without a separate pre-outcome amendment.
No guaranteed alpha; no V51 prospective result; no automatic PAPER/LIVE promotion.
