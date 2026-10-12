# V58 Four-Framework Strategy Research Lab
## Ichimoku + ICT + SMC + Al Brooks

Status: FROZEN DESIGN DRAFT — NO MODEL TRAINING  
Mode: STRICT SCIENTIFIC RESEARCH  
PAPER_EXECUTION = FALSE  
LIVE_EXECUTION = FALSE

## 1. Objective

This lab isolates and tests four discretionary/technical trading frameworks as falsifiable event families:

1. Ichimoku Kinko Hyo
2. ICT (Inner Circle Trader-derived concepts)
3. SMC (Smart Money Concepts-derived structural concepts)
4. Al Brooks price-action concepts

The scientific question is not whether the frameworks are popular or visually convincing.

The question is whether mechanically defined components add incremental predictive/economic information after:
- causality controls;
- transaction costs;
- regime conditioning;
- multi-asset validation;
- ablation;
- uncertainty/abstention;
- multiple-testing controls.

## 2. Core doctrine

- Each framework is tested independently before confluence.
- A framework may fail and remain scientifically useful.
- A visual concept must be converted into deterministic rules.
- No future pivot, centered swing, future-confirmed structure, or hindsight zone is allowed.
- All event definitions use completed information only.
- All primary H4 events inherit the V58 common barrier contract:
  - ATR(14);
  - stop distance = max(1.5 ATR, 0.05% of entry);
  - 3R target;
  - next executable 4h open entry;
  - maximum 30 complete bars after entry;
  - STOP_FIRST for same-bar TP/SL.
- Strategy identity, feature identity, target identity, model identity, risk, and execution remain separate layers.

## 3. Research hierarchy

### Tier 1 — Atomic concepts
Measure components individually.

### Tier 2 — Framework-native setups
Combine only components that belong to the same framework.

### Tier 3 — Pairwise confluence
Combine two frameworks.

### Tier 4 — Four-framework confluence
Only after individual and pairwise evidence is known.

No Tier 4 result may be interpreted unless its components have been separately measured.

---

# 4. ICHIMOKU LAB

Ichimoku is treated as a trend/state representation, not a magical signal system.

Default parameters remain 9/26/52 for the primary experiment. Parameter optimization is prohibited in the primary lab.

## ICH_I1 — Kumo Breakout

LONG:
- close crosses from at/below cloud top to above cloud top;
- cloud is valid and non-zero width;
- optional state features record Tenkan/Kijun relationship but do not hard-gate the primary event.

SHORT:
- symmetric below cloud bottom.

Event features:
- price_cloud_distance_atr
- cloud_width_atr
- tenkan_kijun_distance_atr
- kijun_slope
- tenkan_slope
- bars_since_cloud_break
- price_vs_cloud_percentile
- trend_strength
- realized_volatility

## ICH_I2 — Kijun Pullback Rejection

LONG:
- prior trend state above cloud;
- price retraces toward/touches Kijun within a frozen ATR tolerance;
- bar closes back above Kijun;
- rejection strength is stored continuously.

SHORT:
- symmetric.

Do not require bullish/bearish candle color as a universal truth; candle body/rejection enters as continuous quality.

## ICH_I3 — Tenkan/Kijun Cross Conditional Event

Detect causal TK cross.

Store:
- cross direction;
- distance from cloud;
- cloud state;
- cross distance ATR;
- slope alignment;
- regime.

The primary event is the cross itself. “Strong/neutral/weak cross” is measured, not assumed.

## ICH_I4 — Kumo Breakout Retest

Sequence:
1. causal cloud breakout;
2. price remains outside cloud;
3. retrace to cloud boundary within a frozen lifetime;
4. rejection away from cloud.

No zone may be moved after future outcome.

---

# 5. ICT LAB

ICT is defined as a sequence model. Terms that imply institutional intent are not assumed to be true causal explanations.

## ICT_C1 — Sweep → Displacement → MSS/CISD → FVG Retrace

Primary flagship sequence:

1. LIQUIDITY SWEEP
   - price exceeds a prior causal swing/equal-high/equal-low reference;
   - closes back through the swept reference OR records post-sweep acceptance/rejection state.

2. DISPLACEMENT
   - directional body/range expansion;
   - displacement_body_ratio;
   - displacement_range_atr;
   - relative_volume.

3. MSS/CISD
   - causal break of a pre-existing structural reference;
   - no future-confirmed pivot.

4. FVG
   - standard 3-candle imbalance;
   - width and age recorded.

5. RETRACE
   - revisit of FVG;
   - midpoint and penetration depth stored continuously.

Entry event:
- next-bar-open only after the retrace/rejection decision candle closes.

Features:
- sweep_depth_atr
- liquidity_distance_atr
- bars_since_sweep
- displacement_body_ratio
- displacement_range_atr
- relative_volume
- mss_break_distance_atr
- bars_since_mss
- fvg_width_atr
- fvg_age
- fvg_penetration
- close_location_value

## ICT_C2 — Sweep + MSS Without FVG

Purpose:
Determine whether FVG adds information or merely filters the sweep/MSS sequence.

## ICT_C3 — FVG-Only Retest

Negative-control style setup:
- causal FVG event;
- retrace;
- no sweep/MSS requirement.

This directly measures whether the FVG component adds value independently.

## ICT_C4 — Premium/Discount Conditional Variant

The 50% dealing-range position is recorded as a continuous conditional feature.

It is not allowed to become a hard gate until validation supports it.

Session/kill-zone rules are secondary because crypto trades continuously. They may be evaluated only as a pre-specified ablation.

---

# 6. SMC LAB

SMC is separated from ICT by emphasizing structural zones rather than the full time-and-price sequence.

## SMC_S1 — BOS → Order Block → Mitigation

1. causal BOS;
2. define candidate opposing candle/zone before the impulse;
3. freeze the OB coordinates at creation;
4. wait for revisit/mitigation;
5. generate event only after decision candle closes.

Record:
- ob_width_atr
- ob_age
- ob_penetration
- bos_distance_atr
- impulse_strength
- distance_to_opposing_liquidity

No future successful reaction may be used to decide whether an OB was “valid.”

## SMC_S2 — CHoCH / Structure Reversal

Operationalize change of structure without discretionary relabeling.

Store:
- previous structure direction;
- break magnitude;
- time since prior structure break;
- displacement confirmation;
- regime.

## SMC_S3 — Supply/Demand Return

Use causal DBR/RBR/DBD/RBD-style zone construction.

Store proximal/distal geometry and zone age.

## SMC_S4 — Sweep + OB Retest

Measures whether liquidity-sweep context improves OB mitigation relative to SMC_S1.

---

# 7. AL BROOKS LAB

Brooks concepts require the most formalization in the current repository.

The implementation must avoid subjective chart reading by converting price-action concepts into continuous geometric measures.

## BRK_B1 — Trend Pullback

Trend state:
- EMA/trend slope;
- consecutive directional pressure;
- micro-channel length;
- overlap;
- pullback depth.

Event:
- trend established;
- pullback occurs without structural trend failure;
- signal bar closes in trend direction.

Signal-bar quality remains continuous.

## BRK_B2 — Breakout + Follow-Through

1. break of prior range/reference;
2. breakout strength measured in ATR;
3. next bars evaluated for follow-through;
4. entry decision uses only information available at signal close.

Features:
- breakout_strength
- follow_through_strength
- bar_overlap
- signal_bar_quality
- bars_since_breakout
- range_compression

## BRK_B3 — Failed Breakout Reversal

1. breakout occurs;
2. price re-enters prior range within a frozen lifetime;
3. rejection/failure magnitude recorded;
4. event generated at completed failure bar.

This setup is important because failure may carry more information than the original breakout.

## BRK_B4 — Trading Range Fade

Define range algorithmically:
- low slope;
- high overlap;
- bounded rolling range;
- repeated boundary tests.

Events near range extremes are measured independently from trend setups.

## BRK_B5 — Signal Bar Quality Study

This is not a standalone trading claim.

Measure whether:
- body fraction;
- close location;
- tail asymmetry;
- relative range;
- overlap;
- context

predict TP/SL/TIMEOUT outcomes.

---

# 8. CROSS-FRAMEWORK CONFLUENCE

Confluence is not “more confirmations = better.”

Confluence is a hypothesis that multiple conditionally independent information families may improve event selection.

Required experiments:

PAIR_ICH_ICT
PAIR_ICH_SMC
PAIR_ICH_BROOKS
PAIR_ICT_SMC
PAIR_ICT_BROOKS
PAIR_SMC_BROOKS
ALL_FOUR

For every confluence setup store:
- number_of_frameworks_active
- framework_bitmask
- pairwise agreement score
- directional agreement
- timing dispersion
- feature-family confidence

Hard requirement:
The raw individual event remains in the dataset even when it fails confluence.

This permits direct paired comparison.

---

# 9. KEY ABLATIONS

For each setup:

- event-only
- + market/base features
- + regime
- + HTF
- + framework-native features
- + second framework
- ALL
- DROP each feature family

Special ICT/SMC component ablations:
- SWEEP_ONLY
- MSS_ONLY
- FVG_ONLY
- OB_ONLY
- SWEEP_MSS
- MSS_FVG
- BOS_OB
- SWEEP_OB
- FULL_SEQUENCE

Special Ichimoku ablations:
- CLOUD_ONLY
- TK_ONLY
- KIJUN_ONLY
- CLOUD_TK
- CLOUD_KIJUN
- ALL_ICHIMOKU

Special Brooks ablations:
- TREND_ONLY
- BREAKOUT_ONLY
- SIGNAL_BAR_ONLY
- FOLLOW_THROUGH_ONLY
- FAILED_BREAKOUT_ONLY
- RANGE_ONLY

---

# 10. PRIMARY SCIENTIFIC QUESTIONS

Q1.
Does Ichimoku add information beyond generic trend/momentum/volatility features?

Q2.
Does a liquidity sweep predict reversal, continuation, or neither?

Q3.
Does MSS/CISD add incremental information after a sweep?

Q4.
Does FVG add information beyond displacement and volatility expansion?

Q5.
Does an Order Block add information beyond a generic prior impulse/retest zone?

Q6.
Are Brooks-style failed breakouts more informative than raw breakouts?

Q7.
Does signal-bar quality have incremental information after regime and volatility controls?

Q8.
Does pairwise or four-framework confluence improve economic utility after costs, or merely reduce coverage?

Q9.
Are any effects stable across BTC, ETH, SOL, XRP and DOGE?

Q10.
Are effects regime-specific?

---

# 11. REJECTION CONDITIONS

Reject a setup as incremental alpha evidence if:

- generic price-action baseline explains the result;
- effect vanishes after costs;
- result is one-asset-only without pre-specified asset hypothesis;
- only one regime/fold works without stability;
- confluence improves win rate but worsens net utility;
- coverage collapses to an impractically small sample;
- result depends on a visually tuned threshold;
- future-confirmed pivots/zones are required;
- bootstrap or paired evidence is weak;
- NO_TRADE dominates.

---

# 12. EVIDENCE LEVELS

E0 — Visual/discretionary idea only  
E1 — Deterministic causal definition implemented  
E2 — Unit/causality tests pass  
E3 — Historical descriptive evidence  
E4 — Validation-only incremental evidence  
E5 — Multi-asset + regime + cost robustness  
E6 — Prospective sealed-holdout support

No framework is allowed to be called proven alpha below E6.

---

# 13. IMPLEMENTATION ORDER

1. Brooks formalization
2. ICT/SMC separation and atomic detectors
3. Ichimoku event standardization
4. unified event registry
5. atomic component tests
6. framework-native event tests
7. pairwise confluence
8. four-framework confluence
9. only then meta-model selection

No model training is authorized by this protocol alone.
