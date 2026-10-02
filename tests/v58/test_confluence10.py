"""Tests for the ten pre-registered four-framework confluence hypotheses."""
from __future__ import annotations

from research_bot.v58.confluence10 import BY_ID, FAMILIES, STRATEGIES, evaluate_confluence10, registry


def strong_trend_row():
    return {
        "price_cloud_distance_atr": 0.4,
        "tenkan_kijun_distance_atr": 0.2,
        "price_kijun_distance_atr": 0.2,
        "kijun_slope": 0.1,
        "bars_since_mss": 1.0,
        "fvg_age": 1.0,
        "trend_strength": 0.6,
        "pullback_depth_atr": 1.0,
        "signal_bar_quality": 0.7,
        "follow_through_strength": 0.2,
        "bars_since_cloud_break": 1.0,
        "displacement_body_ratio": 0.7,
        "displacement_range_atr": 1.4,
        "mss_break_distance_atr": 0.2,
        "breakout_strength": 0.4,
        "structure_strength": 0.3,
        "micro_channel_length": 4.0,
        "range_compression": 5.0,
        "cloud_width_atr": 1.0,
        "liquidity_distance_atr": 0.5,
        "relative_volume": 1.3,
        "trend_slope": 0.1,
        "EMA_distance": 0.5,
        "bars_since_sweep": 1.0,
        "sweep_reclaim_strength_atr": 0.2,
        "failed_breakout_score": 0.2,
        "close_location_value": 0.7,
        "trading_range_position": 0.6,
    }


def test_registry_is_exactly_ten_and_every_strategy_requires_financial_gate():
    rows = registry()
    assert len(rows) == len(STRATEGIES) == len(BY_ID) == 10
    assert len({row["strategy_id"] for row in rows}) == 10
    assert all(row["financial_gate_required"] for row in rows)
    assert all(row["families"] == list(FAMILIES) for row in rows)
    assert all(row["primary_backtest_exit_policy"] == "COMMON_V58_BARRIER" for row in rows)
    assert all(row["exit_confirmations"] for row in rows)


def test_strong_four_framework_row_emits_multiple_named_hypotheses_including_max_confluence():
    ids = {hit.strategy_id for hit in evaluate_confluence10(strong_trend_row())}
    assert "C10_01_TREND_PULLBACK_REJECTION" in ids
    assert "C10_03_CLOUD_BREAK_FVG_CONTINUATION" in ids
    assert "C10_06_MICROCHANNEL_FVG_REENTRY" in ids
    assert "C10_07_SQUEEZE_EXPANSION_BREAKOUT" in ids
    assert "C10_09_REGIME_ALIGNED_CONTINUATION" in ids
    assert "C10_10_MAX_CONFLUENCE_SELECTIVE" in ids


def test_range_failed_breakout_has_separate_hypothesis():
    row = strong_trend_row()
    row.update({
        "trend_strength": 0.05,
        "trading_range_position": 0.1,
        "cloud_width_atr": 1.0,
        "bars_since_sweep": 0.0,
        "failed_breakout_score": 0.3,
        "displacement_body_ratio": 0.6,
        "close_location_value": 0.8,
    })
    ids = {hit.strategy_id for hit in evaluate_confluence10(row)}
    assert "C10_05_RANGE_EDGE_FAILED_BREAKOUT" in ids


def test_future_or_outcome_junk_cannot_change_signal_generation():
    row = strong_trend_row()
    original = evaluate_confluence10(row)
    changed = dict(row)
    changed.update({
        "future_return": 999.0,
        "outcome": "TP",
        "exit_price": 1e9,
        "label": "TP",
        "realized_pnl": 1e9,
    })
    assert evaluate_confluence10(changed) == original


def test_missing_or_nonfinite_inputs_fail_closed_by_not_emitting():
    row = strong_trend_row()
    row["price_cloud_distance_atr"] = float("nan")
    ids = {hit.strategy_id for hit in evaluate_confluence10(row)}
    assert "C10_10_MAX_CONFLUENCE_SELECTIVE" not in ids
