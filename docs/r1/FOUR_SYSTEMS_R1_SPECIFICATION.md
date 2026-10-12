# Four-systems R1: operational specification snapshot 2.0.0

Date: 2026-10-11. Evidence state: ENGINEERING_ONLY. Status: DESIGN_SNAPSHOT / PREREGISTRATION_INCOMPLETE.
Baseline inspected: a54c94795d29663b043f126920dbb7d6627c1007.
This document specifies a future R2 implementation; it does not describe new implemented code or certify thesis evidence. Existing v1 code, models, evidence, contracts, risk ceilings and execution flags remain unchanged. The four definitions below are project operational definitions, not universal ICT/SMC/Brooks standards.

## Shared event and data contract

Use one BTC_USDT public spot dataset of closed, continuous UTC 1h bars. Internal 4h context uses exactly four consecutive hourly bars in a UTC bucket (T-4h,T], available only at the maximum component available_at. No open bucket or indefinitely stale higher context. At decision time d, every used bar and event must have available_at <= d. Reject duplicate/out-of-order/missing bars, nonfinite OHLCV, invalid OHLC, naive times and negative/one-hour-or-longer availability lag. Historical close-plus-lag remains HISTORICAL_BACKFILL / DECLARED_NOT_OBSERVED; a CSV hash does not prove point-in-time availability.

For each event store deterministic event_id, detector_version, symbol, timeframe, origin_bar_close, confirmed_bar_close, available_at, side, boundaries, status, last_update_available_at, invalidation_reason and source-row lineage. ID hashes detector/config identity plus origin and confirmation row identities. State updates are append-only, idempotent and usable only after their own availability. Old snapshots must not mutate. Origin time is descriptive; confirmation time is when the event becomes knowable. Store all active zones and subsequent invalidations, not just a flag at an earlier candle. Numeric model views are derived snapshots of this event history. Model projections may encode absent-event distance as 0 only together with missing_flag=1; an actual zero-distance event has missing_flag=0. Incomplete indicator warmup is excluded, not imputed. Exact numeric column names/enum encodings must be reviewed before R2; IDs and future outcome columns are never model inputs. Use missing flag plus age/distance; never confuse no event with distance zero.

Notation: O,H,L,C are closed OHLC, T is bar close, A is available_at. M_n(t)=(max H + min L)/2 over the last n complete bars. TR_t=max(H_t-L_t, abs(H_t-C_(t-1)), abs(L_t-C_(t-1))). ATR14 is the mean of the first 14 valid TRs followed by Wilder recursion (13*previous+TR)/14. Until windows are complete, output missing; ATR<=0 invalidates ATR-normalized features. Monetary quantities/boundaries use Decimal at decision/journal boundaries; float feature transforms require finite validation and have no execution authority.

## ADR-R1-01 — Ichimoku as a causal regime context

Problem: chart displacement can obscure when a cloud value became observable, and price-normalized thickness differs from ATR-normalized thickness.
Options: plotted future/backward lines; unshifted current computations only; separate current and visible/trailing quantities. Choose separate causal quantities.
Evidence: existing v1 uses trailing positive shifts, not a demonstrated negative-shift feature defect. Deng sources motivate investigation, not a known crypto edge.

Decision: Tenkan=M9, Kijun=M26, A0=(Tenkan+Kijun)/2, B0=M52. The cloud visible at t is A0_(t-26), B0_(t-26); these are past computations. Regime=+1 if C exceeds both visible bounds, -1 if below both, else 0, with equality neutral. Current thickness=abs(A0-B0)/ATR14; visible thickness=abs(A0_(t-26)-B0_(t-26))/ATR14, separate names. TK distance=(Tenkan-Kijun)/ATR14; signed visible distance=(C-visible_top)/ATR14 above, (C-visible_bottom)/ATR14 below, else 0. Lag confirmation=C_t/C_(t-26)-1. Never copy C_(t+26) into a feature or backdate a current close into the historical feature row. Thickness is not itself breakout strength.

Pseudocode: consume available closed bar -> update trailing windows -> compute current lines -> read saved t-26 lines -> classify regime -> append snapshot at A_t.
Trade-off: fixed windows and threshold equality are reproducible but not optimized; warmup is long at 4h.
Impact/security: proposed names/formulas differ from v1, require a new feature schema/model ID in R2; no risk/admission change.
Acceptance: hand-computed flat/above/below/equality fixtures; ATR zero/warmup missing; incomplete 4h rejection; full-vs-prefix equality and future perturbation invariance.
Rollback: retain v1 artifacts; disable this new schema rather than relabel old outputs.

## ADR-R1-02 — Confirmed swings, BOS/CHoCH and Order Block

Problem: arbitrary swing repainting and an OB timestamp placed at its origin candle can hide confirmation delay.
Options: visual/repainting ZigZag; confirmed finite-radius pivot; percentage directional-change detector. Choose finite-radius pivot k=2; do not add a ZigZag tuning dimension.
At bar t, candidate j=t-2 is a high pivot only when H_j is strictly greater than every other high in [j-2,j+2]; low pivot is strictly smaller. Tied extrema are not pivots. The pivot is confirmed at A_t and usable for breakout tests starting with the next bar. Breakout at t uses the most recent previously confirmed, unconsumed high/low, testing C_(t-1)<=high<C_t or C_t<low<=C_(t-1). Mark a pivot consumed at its first qualifying break; do not repeatedly emit for that pivot. Prior nonzero break direction is persistent: same direction gives BOS, opposite direction gives CHoCH, initial break gives INITIAL_BREAK. Crossing both conditions or inconsistent levels yields AMBIGUOUS_STRUCTURE, no event.

For OB at a qualifying break t, require same-direction candle and body abs(C_t-O_t)>2*mean(abs(C-O)) over the ten bars strictly preceding t. Zero prior mean or incomplete window fails confirmation. Search backwards in [t-12,t-1] for the last strict opposing-color candle j; doji does not qualify. Zone=[min(O_j,C_j),max(O_j,C_j)], not the full wick. Require a positive-width band; bullish C_t>zone_high or bearish C_t<zone_low. Register only at A_t, with origin=T_j, never at A_j. This is a chosen BOS-plus-impulse variant, not a replication of Informatica or a claim of institutional orders.

Zone lifecycle: ACTIVE -> TOUCHED on a later candle with interval intersection; bullish close strictly below zone_low / bearish close strictly above zone_high -> INVALIDATED; at age>50 timeframe bars -> EXPIRED. Invalidation takes precedence over touch; a touched but uninvalidated zone remains tracked. All changes have current-bar availability. Nearest active/touched same-side band is selected by minimum abs(C-band_mid), tie by later confirmation then event_id. Features: side, distance/ATR, age_bars, touched flag, and missing flag.

Pseudocode: update/invalidate older zones -> evaluate break from previous levels -> confirm OB if break+impulse+opposing candle -> record event -> confirm new pivots for next bar.
Trade-off: strict ties may suppress events, and body-only bounds differ from v1's full candle; no statistical advantage is presumed.
Impact/security: version 2.0.0 only; existing v1 definitions and synthetic results cannot be reclassified. No order or sizing.
Acceptance: k-bar delay, tied/consumed pivots, initial/change/continuation breaks, no opposing candle, impulse at equality, current-body exclusion, touch vs invalidation, expiry and deterministic nearest-zone ties; prefix/replay invariance.
Rollback: disable v2 OB/BOS feature projection; retain immutable event records with their original version.

## ADR-R1-03 — FVG lifecycle and liquidity sweep

Problem: a recent occurrence flag is not an unfilled zone and proximity does not prove institutional liquidity.
Options: raw vectorized flags; single latest zone; immutable multi-zone events with sequential state. Choose event state; vectorize creation predicates only if equivalent.
Bullish FVG at t: L_t>H_(t-2), band [H_(t-2),L_t], side=+1. Bearish: H_t<L_(t-2), band [H_t,L_(t-2)], side=-1. Strict equality is no gap; middle candle direction is not an additional criterion in this version. Confirmation at A_t; no mitigation by creation bar.
For later bars u, bullish mitigation f_u=clip((zone_high-min L_(t+1..u))/width,0,1), bearish f_u=clip((max H_(t+1..u)-zone_low)/width,0,1). State ACTIVE at f=0, PARTIALLY_MITIGATED at 0<f<1, MITIGATED at f=1. Fully mitigated zones are not active; close beyond the distal band is logged as INVALIDATED with precedence over mitigation. Expire age>50 bars. This is maximal directional wick penetration, not traded-volume fill. An OHLC jump can imply penetration without a traded path: tag ambiguous_jump, and exclude that update from claims about execution fill.

Nearest unfilled same-side band uses the deterministic selector in ADR-R1-02. Features: signed width/ATR, distance/ATR, age_bars (not days), mitigation_fraction, status, missing flag. Record all bands; no future-informed nearest-zone selection.
Sweep uses previously confirmed unconsumed pivots: bullish L_t<low_pivot and C_t>low_pivot; bearish H_t>high_pivot and C_t<high_pivot. If both, AMBIGUOUS_SWEEP and no directional event. Equality is not a sweep. Each pivot gives at most one sweep event; sweep-consumption and breakout-consumption are separate flags. Snapshot sweep pulse only at confirmation bar, with an independently stored event age.

Pseudocode: update all prior zones from current closed bar -> resolve invalidation/mitigation/expiry -> create current gap -> evaluate sweep from previous confirmed levels -> append immutable snapshot.
Trade-off: all active zones cost memory; wick penetration is a descriptive feature, never a fill simulator.
Impact/security: differs from latest-only v1; new schema/model identity required. No arbitrage, orders or hidden capital allocation.
Acceptance: bullish/bearish/equality, creation-bar exclusion, partial/full/cross-band penetration, simultaneous sweep, expiry and no-retroactive-change fixtures; chunked replay equals whole-stream events.
Rollback: stop consuming v2 snapshots while keeping v1/event histories separately named.

## ADR-R1-04 — Brooks-inspired bar geometry and context

Problem: discretionary H2/L2 or Three Drives lacks a single supplied formal definition; calling a simplistic pattern the authentic strategy is misleading.
Options: claim literal Brooks equivalence; introduce many pattern searches; define a small versioned geometry comparator. Choose the comparator, not a purported H2/L2 replication.
Width=H-L; if zero, geometry ratios are missing with zero_range flag. Signed body=(C-O)/width; close_location=(C-L)/width. Inside requires H<=previous H and L>=previous L with at least one strict inequality. Outside requires both H>previous H and L<previous L. Identical bars get neither flag. Define high_trend_bar when abs(body)>=0.7 and bullish close_location>=0.8 or bearish close_location<=0.2. These constants are project choices, not paper-derived optimums.
EMA20 initializes from the first close and updates recursively alpha=2/21. Context is +1/-1/0 for C above/below/equal EMA, usable after 20 bars. Consecutive higher-high-and-higher-low count increments only when both are strictly greater than the previous bar in bullish context; symmetric for bearish. Reset to zero on context change or failure. A count does not imply a retracement entry.
Two-bar reversal comparator: previous bearish body<=-0.5, current bullish body>=0.5 and C_t>H_(t-1) gives +1; symmetric gives -1. Window ends at t, confirmed A_t. Do not equate it with discretionary two-legged pullback entries. No Three Drives detector is included.

Pseudocode: compute trailing bar geometry and EMA -> update consecutive count -> evaluate closed two-bar window -> snapshot at A_t.
Trade-off: loses narrative discretion but admits reproducible falsification.
Impact/security: a future challenger variant; cannot silently replace v1's second-entry feature or its old metrics.
Acceptance: zero-width/equality/inside/outside, threshold equality, EMA warmup, counter reset and signed reversal fixtures; causal prefix/future perturbation invariance.
Rollback: disable v2 geometry comparator; v1 negative/synthetic evidence remains intact.

## R1 -> R2 acceptance and status

Before implementation: bind this snapshot hash and a new experiment_id; publish reviewed decision/feature schemas and a complete prospective protocol manifest. No claim of a completed preregistration while dataset identity, inspected windows, uninspected evaluation window and implementation identity are null. The JSON file records these blockers explicitly.
R2 must implement events first, model projections second; no source column with a future dependency enters the whitelist. Test whole-prefix/future-perturbation, replay/chunk invariance, equality and warmup, multi-zone state, HTF availability, and failure paths before comparison with v1.
R3 retains chronological logistic baseline/calibration; ensemble, PCA selection, Kelly, triple-barrier replacement and risk-limit changes need separate experiment amendments.
R4 needs a costed executable equity path, accounting for all attempted strategies and temporal dependence. Classifier metrics and hypothetical horizon outcomes cannot be substituted. Passing any statistical screen is necessary at most, not automatic PAPER authorization.
