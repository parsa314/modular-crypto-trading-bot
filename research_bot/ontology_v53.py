from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class OntologyFamily(str, Enum):
    ICT_SMC = "ICT_SMC"
    AL_BROOKS = "AL_BROOKS"
    ICHIMOKU = "ICHIMOKU"
    MICROSTRUCTURE = "MICROSTRUCTURE"


@dataclass(frozen=True)
class OntologySpec:
    concept_id: str
    family: OntologyFamily
    practitioner_term: str
    operational_definition: str
    required_inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    intended_timeframes: tuple[str, ...]
    causal_delay_bars: int
    invalidation_condition: str
    interpretation_boundary: str

    def validate(self) -> None:
        if not self.concept_id or " " in self.concept_id:
            raise ValueError(f"invalid concept_id: {self.concept_id!r}")
        if not self.operational_definition.strip():
            raise ValueError(f"empty operational definition: {self.concept_id}")
        if not self.required_inputs:
            raise ValueError(f"missing required inputs: {self.concept_id}")
        if not self.outputs:
            raise ValueError(f"missing outputs: {self.concept_id}")
        if not self.intended_timeframes:
            raise ValueError(f"missing timeframes: {self.concept_id}")
        if self.causal_delay_bars < 0:
            raise ValueError(f"negative causal delay: {self.concept_id}")
        if not self.invalidation_condition.strip():
            raise ValueError(f"missing invalidation condition: {self.concept_id}")
        if not self.interpretation_boundary.strip():
            raise ValueError(f"missing interpretation boundary: {self.concept_id}")


def _spec(
    concept_id: str,
    family: OntologyFamily,
    practitioner_term: str,
    operational_definition: str,
    required_inputs: Iterable[str],
    outputs: Iterable[str],
    timeframes: Iterable[str],
    delay: int,
    invalidation: str,
    boundary: str,
) -> OntologySpec:
    return OntologySpec(
        concept_id=concept_id,
        family=family,
        practitioner_term=practitioner_term,
        operational_definition=operational_definition,
        required_inputs=tuple(required_inputs),
        outputs=tuple(outputs),
        intended_timeframes=tuple(timeframes),
        causal_delay_bars=delay,
        invalidation_condition=invalidation,
        interpretation_boundary=boundary,
    )


V53_ONTOLOGY: tuple[OntologySpec, ...] = (
    _spec(
        "smc_confirmed_swing",
        OntologyFamily.ICT_SMC,
        "Swing High / Swing Low",
        "A pivot at candidate bar c is emitted only after r right-hand bars have closed. At decision bar t=c+r, high[c] must equal the maximum (or low[c] the minimum) over [c-l, c+r].",
        ("timestamp", "high", "low"),
        ("smc_swing_high_confirmed", "smc_swing_low_confirmed", "smc_prior_swing_high", "smc_prior_swing_low"),
        ("5m", "15m", "1h", "4h"),
        2,
        "The level remains historical evidence but is superseded when a later confirmed swing of the same side appears.",
        "This is a causal structural pivot, not proof of institutional activity.",
    ),
    _spec(
        "smc_bos", OntologyFamily.ICT_SMC, "Break of Structure (BOS)",
        "Bullish BOS occurs when the current close crosses above the previously confirmed swing-high level; bearish BOS is the symmetric close below the prior confirmed swing low. The threshold is shifted so a pivot confirmed on the same bar cannot define its own break.",
        ("close", "smc_prior_swing_high", "smc_prior_swing_low"),
        ("smc_bos_bull", "smc_bos_bear", "smc_structure_state"),
        ("5m", "15m", "1h", "4h"), 0,
        "A later opposite structural break changes the structure state.",
        "BOS is an operational price-structure event; no causal claim about smart money is made.",
    ),
    _spec(
        "smc_choch", OntologyFamily.ICT_SMC, "Change of Character (ChoCH)",
        "A bullish ChoCH is the first bullish structural break while the carried structure state is bearish; bearish ChoCH is the first bearish break while the carried state is bullish.",
        ("smc_bos_bull", "smc_bos_bear", "smc_structure_state"),
        ("smc_choch_bull", "smc_choch_bear"), ("5m", "15m", "1h", "4h"), 0,
        "The event is point-in-time; subsequent state is updated to the new break direction.",
        "This is a deterministic state transition proxy, not a claim that a discretionary trader would label every event identically.",
    ),
    _spec(
        "ict_fvg", OntologyFamily.ICT_SMC, "Fair Value Gap (FVG)",
        "A bullish three-candle gap is emitted at t when low[t] > high[t-2]; bearish when high[t] < low[t-2]. Gap magnitude is normalized by current close.",
        ("high", "low", "close"),
        ("ict_fvg_bull", "ict_fvg_bear", "ict_fvg_bull_size_pct", "ict_fvg_bear_size_pct"),
        ("5m", "15m", "1h"), 0,
        "A gap feature is an event at formation; later mitigation/fill is represented by separate state features.",
        "FVG denotes observed price separation only; it is not assumed to represent hidden institutional orders.",
    ),
    _spec(
        "ict_displacement", OntologyFamily.ICT_SMC, "Displacement",
        "A directional bar whose real body exceeds a configurable multiple of ATR and whose close is near the directional extreme.",
        ("open", "high", "low", "close"),
        ("ict_displacement_bull", "ict_displacement_bear", "ict_body_atr"),
        ("5m", "15m", "1h"), 0,
        "The event expires after the bar; downstream order-block logic stores its consequence separately.",
        "This is a volatility-normalized impulse proxy, not evidence of a specific participant class.",
    ),
    _spec(
        "ict_order_block", OntologyFamily.ICT_SMC, "Order Block",
        "Bullish candidate: bullish BOS plus bullish displacement with the immediately preceding candle bearish. The preceding candle high/low becomes the candidate zone; bearish is symmetric.",
        ("open", "high", "low", "close", "smc_bos_bull", "smc_bos_bear", "ict_displacement_bull", "ict_displacement_bear"),
        ("ict_bull_ob_candidate", "ict_bear_ob_candidate", "ict_bull_ob_lower", "ict_bull_ob_upper", "ict_bear_ob_lower", "ict_bear_ob_upper"),
        ("5m", "15m", "1h"), 0,
        "Bullish zone invalidates on a close below its lower boundary; bearish on a close above its upper boundary.",
        "The label is an operational proxy for research; it does not establish that unobserved institutional inventory created the candle.",
    ),
    _spec(
        "ict_mitigation_block", OntologyFamily.ICT_SMC, "Mitigation Block / Mitigation",
        "A stored order-block zone is considered mitigated when a later bar overlaps the zone. A rejection flag additionally requires the close to finish back through the zone midpoint in the expected direction.",
        ("high", "low", "close", "ict_bull_ob_lower", "ict_bull_ob_upper", "ict_bear_ob_lower", "ict_bear_ob_upper"),
        ("ict_bull_ob_mitigation", "ict_bear_ob_mitigation", "ict_bull_ob_rejection", "ict_bear_ob_rejection"),
        ("5m", "15m"), 1,
        "The zone is invalid after a close through its invalidation boundary.",
        "Mitigation is defined as observable revisit/rejection behavior only.",
    ),
    _spec(
        "ict_breaker_block", OntologyFamily.ICT_SMC, "Breaker Block",
        "When a bearish order-block proxy is invalidated by a close above its upper bound, that failed zone becomes a bullish-breaker candidate; a later overlap/retest from above emits a bullish breaker retest. Bearish is symmetric.",
        ("high", "low", "close", "ict_bull_ob_lower", "ict_bull_ob_upper", "ict_bear_ob_lower", "ict_bear_ob_upper"),
        ("ict_bull_breaker_retest", "ict_bear_breaker_retest"),
        ("5m", "15m"), 1,
        "The breaker candidate is superseded by a more recent invalidated opposite order-block zone.",
        "This is a failed-zone/retest state machine, not a causal statement about market-maker intent.",
    ),
    _spec(
        "ict_liquidity_pool", OntologyFamily.ICT_SMC, "Liquidity Pool",
        "Previously confirmed swing highs/lows act as observable reference levels where stop/limit interest may plausibly cluster.",
        ("smc_prior_swing_high", "smc_prior_swing_low", "close"),
        ("ict_liquidity_high_distance_atr", "ict_liquidity_low_distance_atr"),
        ("5m", "15m", "1h", "4h"), 0,
        "Reference level is superseded by a newer confirmed swing.",
        "The feature measures distance to visible structural levels; actual resting liquidity is not inferred without order-book data.",
    ),
    _spec(
        "ict_liquidity_sweep", OntologyFamily.ICT_SMC, "Liquidity Sweep / Stop Run",
        "Bearish sweep: current high exceeds the prior confirmed swing high but the bar closes below that level. Bullish sweep is the symmetric excursion below prior swing low followed by a close back above.",
        ("high", "low", "close", "smc_prior_swing_high", "smc_prior_swing_low"),
        ("ict_sweep_bull", "ict_sweep_bear"), ("5m", "15m", "1h"), 0,
        "Point-in-time event; no future confirmation is required unless a downstream setup explicitly asks for it.",
        "The term sweep is descriptive price geometry; no intent to hunt stops is assumed.",
    ),
    _spec(
        "smc_premium_discount", OntologyFamily.ICT_SMC, "Premium / Discount",
        "Within the latest valid dealing range bounded by prior confirmed swing low/high, normalized position = (close-low)/(high-low). Values above 0.5 are premium and below 0.5 discount.",
        ("close", "smc_prior_swing_high", "smc_prior_swing_low"),
        ("smc_dealing_range_position", "smc_premium", "smc_discount"),
        ("15m", "1h", "4h"), 0,
        "Undefined when the most recent swing high is not above the most recent swing low.",
        "A relative location measure only; it is not itself a buy/sell signal.",
    ),
    _spec(
        "ict_inducement_proxy", OntologyFamily.ICT_SMC, "Inducement",
        "A minor confirmed pullback swing inside the current major dealing range and aligned with the existing structure state. Bullish proxy requires a minor swing low above the major swing low while structure is bullish; bearish is symmetric.",
        ("high", "low", "close", "smc_structure_state", "smc_prior_swing_high", "smc_prior_swing_low"),
        ("ict_inducement_long_proxy", "ict_inducement_short_proxy"),
        ("5m", "15m"), 1,
        "Superseded when structure flips or the major dealing range changes.",
        "This is explicitly an inducement proxy; trader psychology/manipulation is not inferred.",
    ),
    _spec(
        "ict_liquidity_engineering_proxy", OntologyFamily.ICT_SMC, "Liquidity Engineering",
        "A composite event requiring a recent liquidity sweep followed within a short trailing window by directional displacement and a BOS/ChoCH in the same direction.",
        ("ict_sweep_bull", "ict_sweep_bear", "ict_displacement_bull", "ict_displacement_bear", "smc_bos_bull", "smc_bos_bear", "smc_choch_bull", "smc_choch_bear"),
        ("ict_liquidity_engineering_bull_proxy", "ict_liquidity_engineering_bear_proxy"),
        ("5m", "15m"), 0,
        "The composite is only true on the confirmation bar and does not persist.",
        "The name mirrors practitioner terminology; the model only observes a sweep→impulse→structure sequence.",
    ),
    _spec(
        "ict_killzones", OntologyFamily.ICT_SMC, "ICT Killzones",
        "DST-aware New-York-local session flags using configurable research windows: London 02:00-05:00, New York AM 07:00-10:00, London close 10:00-12:00.",
        ("timestamp",),
        ("ict_killzone_london", "ict_killzone_ny_am", "ict_killzone_london_close"),
        ("5m", "15m"), 0,
        "Flags expire when local clock exits the configured interval.",
        "Session windows are temporal context variables, not standalone alpha claims.",
    ),
    _spec(
        "brooks_bar_types", OntologyFamily.AL_BROOKS, "Trend Bar / Doji / Inside / Outside Bar",
        "Classify bars from body-to-range ratio, close location, and relation to the previous bar.",
        ("open", "high", "low", "close"),
        ("brooks_doji", "brooks_bull_trend_bar", "brooks_bear_trend_bar", "brooks_inside_bar", "brooks_outside_bar"),
        ("5m", "15m"), 0, "Point-in-time bar classifications do not persist.",
        "Thresholds are formal research proxies for visual Brooks concepts.",
    ),
    _spec(
        "brooks_signal_bar", OntologyFamily.AL_BROOKS, "Signal Bar Quality",
        "Bull signal quality rises with bullish body ratio, close-near-high, small upper tail, trend alignment and low overlap; bearish is symmetric.",
        ("open", "high", "low", "close"),
        ("brooks_bull_signal_quality", "brooks_bear_signal_quality"),
        ("5m", "15m"), 0,
        "Quality is contextual and recomputed each bar; it is not a realized win probability.",
        "The score is a deterministic feature later calibrated by ML; it is not claimed to reproduce discretionary judgement exactly.",
    ),
    _spec(
        "brooks_always_in", OntologyFamily.AL_BROOKS, "Always-In Direction",
        "A carried state flips long after a bullish trend bar/breakout with positive EMA slope and flips short on the symmetric condition.",
        ("open", "high", "low", "close"), ("brooks_always_in",),
        ("5m", "15m"), 0,
        "State flips only after an opposite qualifying event.",
        "This is an explicit algorithmic proxy for the discretionary Always-In concept.",
    ),
    _spec(
        "brooks_trend_range", OntologyFamily.AL_BROOKS, "Trend vs Trading Range",
        "Trend state uses ATR-normalized EMA slope plus price location; range state requires low normalized slope and high trailing bar overlap.",
        ("open", "high", "low", "close"),
        ("brooks_market_trend", "brooks_trading_range", "brooks_ema_slope_atr", "brooks_overlap_mean"),
        ("5m", "15m", "1h"), 0,
        "State is recomputed each bar and can transition without future confirmation.",
        "This is a quantitative regime proxy, not a verbatim discretionary label.",
    ),
    _spec(
        "brooks_bar_count", OntologyFamily.AL_BROOKS, "Bar Counting",
        "Consecutive closes/bodies in the same direction are counted causally and reset on the opposite bar.",
        ("open", "close"), ("brooks_bull_bar_count", "brooks_bear_bar_count"),
        ("5m", "15m"), 0, "Count resets on an opposite-direction bar.", "Pure bar-sequence descriptor.",
    ),
    _spec(
        "brooks_h1_h2_l1_l2", OntologyFamily.AL_BROOKS, "H1/H2 and L1/L2",
        "During an Always-In trend, a pullback state is opened by counter-directional pressure. Successive trend-direction breakout attempts of the prior bar are numbered first/second entry proxies.",
        ("high", "low", "close", "brooks_always_in"),
        ("brooks_h1_long_proxy", "brooks_h2_long_proxy", "brooks_l1_short_proxy", "brooks_l2_short_proxy"),
        ("5m", "15m"), 0,
        "Attempt counter resets when Always-In state changes or the pullback is resolved.",
        "Explicit proxy: it does not claim perfect equivalence to manual Brooks counting.",
    ),
    _spec(
        "brooks_failed_breakout", OntologyFamily.AL_BROOKS, "Failed Breakout",
        "A prior close breaks a trailing resistance/support level and the current bar closes back through that same pre-breakout level.",
        ("high", "low", "close"),
        ("brooks_failed_bull_breakout", "brooks_failed_bear_breakout"),
        ("5m", "15m"), 1, "Point-in-time failure event.", "Uses only levels known before the breakout bar.",
    ),
    _spec(
        "brooks_micro_double", OntologyFamily.AL_BROOKS, "Micro Double Top / Bottom",
        "Two highs (or lows) two bars apart lie within a configurable ATR tolerance and the middle bar shows directional separation.",
        ("high", "low", "close"), ("brooks_micro_double_top", "brooks_micro_double_bottom"),
        ("5m", "15m"), 0, "Point-in-time pattern; later confirmation is a separate feature.", "A geometric proxy only.",
    ),
    _spec(
        "ichimoku_tk", OntologyFamily.ICHIMOKU, "Tenkan / Kijun",
        "Tenkan=(HH9+LL9)/2 and Kijun=(HH26+LL26)/2 with current and trailing information only; crosses are emitted at the current close.",
        ("high", "low", "close"),
        ("ichi_tenkan", "ichi_kijun", "ichi_tk_distance_pct", "ichi_tk_cross_up", "ichi_tk_cross_down"),
        ("15m", "1h", "4h"), 0,
        "Cross state changes when Tenkan/Kijun ordering changes.", "Standard components represented without future shifts.",
    ),
    _spec(
        "ichimoku_kumo", OntologyFamily.ICHIMOKU, "Senkou Span A/B and Kumo",
        "Decision-time cloud state is computed from information available now. Span A=(Tenkan+Kijun)/2 and Span B=(HH52+LL52)/2. No backward alignment of future-plotted values is used for model features.",
        ("high", "low", "close"),
        ("ichi_span_a_now", "ichi_span_b_now", "ichi_cloud_width_pct", "ichi_price_above_cloud", "ichi_price_below_cloud", "ichi_cloud_twist"),
        ("15m", "1h", "4h"), 0,
        "Cloud orientation changes when Span A/B ordering changes.", "Plotting convention is separated from information availability to prevent leakage.",
    ),
    _spec(
        "ichimoku_chikou_context", OntologyFamily.ICHIMOKU, "Chikou Span Context",
        "Leakage-safe context compares current close with the close 26 bars ago: (close[t]-close[t-26])/close[t]. The current close is never shifted backward into historical feature rows.",
        ("close",), ("ichi_chikou_context_pct",), ("15m", "1h", "4h"), 0,
        "Undefined until 26 prior bars exist.", "This preserves lagged-price context but is not the visual future-aligned Chikou plot.",
    ),
    _spec(
        "micro_order_flow", OntologyFamily.MICROSTRUCTURE, "Order Flow / Depth Imbalance",
        "Optional public trade/depth inputs are accepted only when already timestamp-aligned point-in-time: signed trade imbalance and depth imbalance in [-1,1].",
        ("timestamp", "trade_imbalance", "depth_imbalance"),
        ("micro_trade_imbalance", "micro_depth_imbalance", "smc_order_flow_confirmation"),
        ("tick", "1m", "5m"), 0,
        "Missing or invalid microstructure inputs remain unavailable rather than being imputed from future data.",
        "Observed flow variables are distinct from practitioner narratives about hidden liquidity.",
    ),
)


def ontology_by_id() -> dict[str, OntologySpec]:
    out: dict[str, OntologySpec] = {}
    for spec in V53_ONTOLOGY:
        spec.validate()
        if spec.concept_id in out:
            raise ValueError(f"duplicate concept_id: {spec.concept_id}")
        out[spec.concept_id] = spec
    return out


def output_to_concept() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for spec in V53_ONTOLOGY:
        for output in spec.outputs:
            if output in mapping:
                raise ValueError(f"duplicate ontology output: {output}")
            mapping[output] = spec.concept_id
    return mapping


def ontology_manifest() -> list[dict[str, object]]:
    return [
        {
            "concept_id": spec.concept_id,
            "family": spec.family.value,
            "practitioner_term": spec.practitioner_term,
            "operational_definition": spec.operational_definition,
            "required_inputs": list(spec.required_inputs),
            "outputs": list(spec.outputs),
            "intended_timeframes": list(spec.intended_timeframes),
            "causal_delay_bars": spec.causal_delay_bars,
            "invalidation_condition": spec.invalidation_condition,
            "interpretation_boundary": spec.interpretation_boundary,
        }
        for spec in V53_ONTOLOGY
    ]
