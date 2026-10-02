"""Ten pre-registered Ichimoku + ICT/SMC + Al Brooks confluence hypotheses.

The primary comparison uses the common V58 execution/label policy so signal
quality can be compared fairly before strategy-specific exits are promoted.
Each definition also carries a frozen candidate exit policy for a later,
separately reported ablation. All predicates use current/past causal features.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import math
from typing import Mapping, Any


FAMILIES = ("ICHIMOKU", "ICT", "SMC", "AL_BROOKS")


@dataclass(frozen=True)
class StrategyDefinition:
    strategy_id: str
    name: str
    regime: str
    entry_confirmations: tuple[str, ...]
    exit_confirmations: tuple[str, ...]
    candidate_stop_atr: float
    candidate_reward_r: float
    candidate_horizon_bars: int
    financial_gate_required: bool = True

    def __post_init__(self) -> None:
        if not self.strategy_id.startswith("C10_"):
            raise ValueError("strategy_id must use C10_ prefix")
        if len(self.entry_confirmations) < 4:
            raise ValueError("every strategy requires multi-framework confirmation")
        if not self.exit_confirmations:
            raise ValueError("exit confirmations are required")
        if not (self.candidate_stop_atr > 0 and self.candidate_reward_r > 0 and self.candidate_horizon_bars > 0):
            raise ValueError("invalid candidate exit policy")
        if not self.financial_gate_required:
            raise ValueError("financial gate cannot be disabled")


@dataclass(frozen=True)
class StrategyHit:
    strategy_id: str
    states: tuple[str, ...]


STRATEGIES = (
    StrategyDefinition(
        "C10_01_TREND_PULLBACK_REJECTION",
        "Ichimoku trend + ICT/SMC continuation + Brooks pullback rejection",
        "TREND",
        ("ICHIMOKU_ABOVE_CLOUD_TK_BULL", "SMC_RECENT_MSS", "ICT_RECENT_FVG", "BROOKS_PULLBACK_SIGNAL"),
        ("CLOSE_BELOW_KIJUN_AND_STRUCTURE_WEAK", "HARD_STOP", "TARGET", "TIMEOUT"),
        1.00, 1.75, 12,
    ),
    StrategyDefinition(
        "C10_02_LIQUIDITY_SWEEP_REVERSAL",
        "Liquidity sweep + reclaim + MSS + Kijun reclaim + Brooks failed breakout",
        "REVERSAL",
        ("ICT_LIQUIDITY_SWEEP_RECENT", "SMC_RECLAIM_OR_MSS", "ICHIMOKU_KIJUN_RECLAIM", "BROOKS_FAILED_BREAKOUT"),
        ("CLOSE_BACK_BELOW_RECLAIM_ZONE", "HARD_STOP", "TARGET", "TIMEOUT"),
        1.10, 2.00, 10,
    ),
    StrategyDefinition(
        "C10_03_CLOUD_BREAK_FVG_CONTINUATION",
        "Kumo breakout + displacement/FVG + structure break + Brooks follow-through",
        "BREAKOUT",
        ("ICHIMOKU_RECENT_CLOUD_BREAK", "ICT_DISPLACEMENT_FVG", "SMC_POSITIVE_STRUCTURE_BREAK", "BROOKS_BREAKOUT_FOLLOW_THROUGH"),
        ("FAILED_CLOUD_BREAK_AND_NEGATIVE_STRUCTURE", "HARD_STOP", "TARGET", "TIMEOUT"),
        1.00, 2.00, 12,
    ),
    StrategyDefinition(
        "C10_04_KIJUN_PULLBACK_H2",
        "Kijun pullback + nearby liquidity + recent FVG + Brooks H2-style continuation proxy",
        "TREND_PULLBACK",
        ("ICHIMOKU_KIJUN_PULLBACK", "SMC_STRUCTURE_HELD", "ICT_RECENT_FVG", "BROOKS_SECOND_ENTRY_PROXY"),
        ("CLOSE_BELOW_KIJUN_WITH_NEGATIVE_FOLLOW_THROUGH", "HARD_STOP", "TARGET", "TIMEOUT"),
        0.90, 1.50, 8,
    ),
    StrategyDefinition(
        "C10_05_RANGE_EDGE_FAILED_BREAKOUT",
        "Range-edge liquidity sweep + cloud compression + displacement + Brooks failed breakout",
        "RANGE_REVERSAL",
        ("ICHIMOKU_COMPRESSED_CLOUD", "ICT_RECENT_SWEEP", "SMC_RECLAIM", "BROOKS_RANGE_FAILED_BREAKOUT"),
        ("RANGE_RECLAIM_FAILURE", "HARD_STOP", "TARGET", "TIMEOUT"),
        0.80, 1.50, 8,
    ),
    StrategyDefinition(
        "C10_06_MICROCHANNEL_FVG_REENTRY",
        "Bull microchannel + cloud trend + FVG re-entry + controlled pullback",
        "TREND",
        ("ICHIMOKU_TREND_ALIGNED", "ICT_RECENT_FVG", "SMC_STRUCTURE_POSITIVE", "BROOKS_MICROCHANNEL_PULLBACK"),
        ("MICROCHANNEL_BREAK_AND_KIJUN_LOSS", "HARD_STOP", "TARGET", "TIMEOUT"),
        0.90, 1.80, 10,
    ),
    StrategyDefinition(
        "C10_07_SQUEEZE_EXPANSION_BREAKOUT",
        "Kumo/range compression + liquidity proximity + displacement breakout + follow-through",
        "VOLATILITY_EXPANSION",
        ("ICHIMOKU_CLOUD_COMPRESSION", "ICT_LIQUIDITY_PROXIMITY", "SMC_DISPLACEMENT_BREAK", "BROOKS_BREAKOUT_FOLLOW_THROUGH"),
        ("BREAKOUT_FAILURE_AND_RANGE_REENTRY", "HARD_STOP", "TARGET", "TIMEOUT"),
        1.20, 2.20, 12,
    ),
    StrategyDefinition(
        "C10_08_DEEP_PULLBACK_REVERSAL",
        "Deep trend pullback + sweep/MSS recovery + Kijun support + Brooks reversal signal",
        "DEEP_PULLBACK",
        ("ICHIMOKU_KIJUN_TREND_SUPPORT", "ICT_SWEEP_RECENT", "SMC_RECOVERY", "BROOKS_DEEP_PULLBACK_REVERSAL"),
        ("KijUN_SUPPORT_FAILURE_AND_NEGATIVE_STRUCTURE", "HARD_STOP", "TARGET", "TIMEOUT"),
        1.20, 2.00, 14,
    ),
    StrategyDefinition(
        "C10_09_REGIME_ALIGNED_CONTINUATION",
        "Slow-trend alignment + Ichimoku trend + positive structure + Brooks continuation",
        "TREND",
        ("ICHIMOKU_TREND_ALIGNED", "ICT_LIQUIDITY_NOT_EXTREME", "SMC_STRUCTURE_POSITIVE", "BROOKS_TREND_CONTINUATION"),
        ("TREND_SLOPE_REVERSAL_AND_KIJUN_LOSS", "HARD_STOP", "TARGET", "TIMEOUT"),
        1.00, 1.80, 12,
    ),
    StrategyDefinition(
        "C10_10_MAX_CONFLUENCE_SELECTIVE",
        "High-selectivity four-framework confluence",
        "SELECTIVE",
        ("ICHIMOKU_STRONG_TREND", "ICT_FVG_DISPLACEMENT", "SMC_MSS_STRUCTURE", "BROOKS_STRONG_SIGNAL_FOLLOW_THROUGH"),
        ("TWO_OF_FOUR_FRAMEWORKS_INVALIDATED", "HARD_STOP", "TARGET", "TIMEOUT"),
        1.00, 2.50, 16,
    ),
)

BY_ID = {s.strategy_id: s for s in STRATEGIES}
if len(BY_ID) != 10:
    raise RuntimeError("exactly ten unique confluence strategies are required")


def registry() -> list[dict[str, Any]]:
    return [{**asdict(s), "families": list(FAMILIES), "primary_backtest_exit_policy": "COMMON_V58_BARRIER"}
            for s in STRATEGIES]


def _v(row: Mapping[str, Any], name: str) -> float:
    value = row.get(name)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.nan
    return number if math.isfinite(number) else math.nan


def _le(row: Mapping[str, Any], name: str, threshold: float) -> bool:
    value = _v(row, name)
    return math.isfinite(value) and value <= threshold


def _ge(row: Mapping[str, Any], name: str, threshold: float) -> bool:
    value = _v(row, name)
    return math.isfinite(value) and value >= threshold


def _between(row: Mapping[str, Any], name: str, low: float, high: float) -> bool:
    value = _v(row, name)
    return math.isfinite(value) and low <= value <= high


def _hits(row: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    hits: dict[str, tuple[str, ...]] = {}

    if (
        _ge(row, "price_cloud_distance_atr", 0.10)
        and _ge(row, "tenkan_kijun_distance_atr", 0.0)
        and _ge(row, "kijun_slope", 0.0)
        and _le(row, "bars_since_mss", 4.0)
        and _le(row, "fvg_age", 6.0)
        and _ge(row, "trend_strength", 0.25)
        and _between(row, "pullback_depth_atr", 0.35, 1.75)
        and _ge(row, "signal_bar_quality", 0.45)
        and _ge(row, "follow_through_strength", 0.0)
    ):
        hits["C10_01_TREND_PULLBACK_REJECTION"] = BY_ID["C10_01_TREND_PULLBACK_REJECTION"].entry_confirmations

    if (
        _le(row, "bars_since_sweep", 2.0)
        and (_ge(row, "sweep_reclaim_strength_atr", 0.05) or _le(row, "bars_since_mss", 2.0))
        and _ge(row, "price_kijun_distance_atr", 0.0)
        and _ge(row, "price_cloud_distance_atr", -0.50)
        and _ge(row, "failed_breakout_score", 0.15)
        and _ge(row, "signal_bar_quality", 0.40)
    ):
        hits["C10_02_LIQUIDITY_SWEEP_REVERSAL"] = BY_ID["C10_02_LIQUIDITY_SWEEP_REVERSAL"].entry_confirmations

    if (
        _le(row, "bars_since_cloud_break", 2.0)
        and _ge(row, "price_cloud_distance_atr", 0.05)
        and _ge(row, "tenkan_kijun_distance_atr", 0.0)
        and _le(row, "fvg_age", 3.0)
        and _ge(row, "displacement_body_ratio", 0.55)
        and _ge(row, "displacement_range_atr", 1.0)
        and _ge(row, "mss_break_distance_atr", 0.05)
        and _ge(row, "breakout_strength", 0.20)
        and _ge(row, "follow_through_strength", 0.05)
    ):
        hits["C10_03_CLOUD_BREAK_FVG_CONTINUATION"] = BY_ID["C10_03_CLOUD_BREAK_FVG_CONTINUATION"].entry_confirmations

    if (
        _ge(row, "price_cloud_distance_atr", 0.0)
        and _between(row, "price_kijun_distance_atr", 0.0, 0.40)
        and _ge(row, "kijun_slope", 0.0)
        and _ge(row, "structure_strength", 0.0)
        and _le(row, "fvg_age", 6.0)
        and _between(row, "pullback_depth_atr", 0.50, 2.00)
        and _ge(row, "signal_bar_quality", 0.50)
        and _ge(row, "follow_through_strength", 0.0)
    ):
        hits["C10_04_KIJUN_PULLBACK_H2"] = BY_ID["C10_04_KIJUN_PULLBACK_H2"].entry_confirmations

    if (
        _between(row, "trend_strength", -0.25, 0.25)
        and _le(row, "trading_range_position", 0.20)
        and _le(row, "cloud_width_atr", 1.50)
        and _le(row, "bars_since_sweep", 1.0)
        and _ge(row, "failed_breakout_score", 0.15)
        and _ge(row, "displacement_body_ratio", 0.45)
        and _ge(row, "close_location_value", 0.25)
    ):
        hits["C10_05_RANGE_EDGE_FAILED_BREAKOUT"] = BY_ID["C10_05_RANGE_EDGE_FAILED_BREAKOUT"].entry_confirmations

    if (
        _ge(row, "price_cloud_distance_atr", 0.10)
        and _ge(row, "tenkan_kijun_distance_atr", 0.0)
        and _le(row, "fvg_age", 5.0)
        and _ge(row, "structure_strength", 0.0)
        and _ge(row, "micro_channel_length", 3.0)
        and _between(row, "pullback_depth_atr", 0.40, 1.40)
        and _ge(row, "signal_bar_quality", 0.40)
    ):
        hits["C10_06_MICROCHANNEL_FVG_REENTRY"] = BY_ID["C10_06_MICROCHANNEL_FVG_REENTRY"].entry_confirmations

    if (
        _le(row, "range_compression", 6.0)
        and _le(row, "cloud_width_atr", 1.50)
        and _le(row, "liquidity_distance_atr", 0.75)
        and _ge(row, "relative_volume", 1.10)
        and _ge(row, "displacement_body_ratio", 0.60)
        and _ge(row, "displacement_range_atr", 1.20)
        and _ge(row, "breakout_strength", 0.25)
        and _ge(row, "follow_through_strength", 0.05)
    ):
        hits["C10_07_SQUEEZE_EXPANSION_BREAKOUT"] = BY_ID["C10_07_SQUEEZE_EXPANSION_BREAKOUT"].entry_confirmations

    if (
        _ge(row, "kijun_slope", 0.0)
        and _ge(row, "price_cloud_distance_atr", -0.25)
        and _between(row, "pullback_depth_atr", 1.00, 2.50)
        and _le(row, "bars_since_sweep", 3.0)
        and (_le(row, "bars_since_mss", 3.0) or _ge(row, "structure_strength", 0.05))
        and _ge(row, "failed_breakout_score", 0.10)
        and _ge(row, "signal_bar_quality", 0.45)
    ):
        hits["C10_08_DEEP_PULLBACK_REVERSAL"] = BY_ID["C10_08_DEEP_PULLBACK_REVERSAL"].entry_confirmations

    if (
        _ge(row, "trend_slope", 0.0)
        and _ge(row, "EMA_distance", 0.0)
        and _ge(row, "price_cloud_distance_atr", 0.10)
        and _ge(row, "tenkan_kijun_distance_atr", 0.0)
        and _ge(row, "structure_strength", 0.0)
        and _ge(row, "trend_strength", 0.30)
        and _ge(row, "relative_volume", 0.80)
        and _ge(row, "signal_bar_quality", 0.35)
    ):
        hits["C10_09_REGIME_ALIGNED_CONTINUATION"] = BY_ID["C10_09_REGIME_ALIGNED_CONTINUATION"].entry_confirmations

    if (
        _ge(row, "price_cloud_distance_atr", 0.25)
        and _ge(row, "tenkan_kijun_distance_atr", 0.10)
        and _ge(row, "kijun_slope", 0.0)
        and _le(row, "fvg_age", 4.0)
        and _ge(row, "displacement_body_ratio", 0.60)
        and _ge(row, "displacement_range_atr", 1.10)
        and (_ge(row, "mss_break_distance_atr", 0.10) or _le(row, "bars_since_mss", 2.0))
        and _ge(row, "trend_strength", 0.40)
        and _ge(row, "signal_bar_quality", 0.55)
        and _ge(row, "follow_through_strength", 0.10)
        and _ge(row, "relative_volume", 1.10)
    ):
        hits["C10_10_MAX_CONFLUENCE_SELECTIVE"] = BY_ID["C10_10_MAX_CONFLUENCE_SELECTIVE"].entry_confirmations

    return hits


def evaluate_confluence10(row: Mapping[str, Any]) -> list[StrategyHit]:
    """Return deterministic long-spot hypotheses for one closed decision bar."""
    if not isinstance(row, Mapping):
        raise ValueError("row must be a mapping of causal feature names to values")
    return [StrategyHit(strategy_id, states) for strategy_id, states in _hits(row).items()]
