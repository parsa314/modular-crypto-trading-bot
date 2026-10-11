from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from research_bot.course.contracts import (
    BookSnapshot,
    MarketMetadata,
    UnsupportedCapability,
)
from research_bot.course.microstructure import (
    calculate_microstructure,
    causal_price_impact,
    executable_cost,
    short_horizon_volatility,
)

NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)


def book():
    metadata = MarketMetadata(
        "fixture",
        "BTC/USDT",
        "BTC",
        "USDT",
        Decimal(1),
        Decimal("0.1"),
        Decimal(1),
        "USDT",
    )
    return BookSnapshot(
        metadata,
        NOW - timedelta(seconds=2),
        NOW - timedelta(seconds=1),
        NOW,
        NOW,
        "fixture-sequence-1",
        ((Decimal(99), Decimal(3)), (Decimal(98), Decimal(2))),
        ((Decimal(101), Decimal(1)), (Decimal(102), Decimal(4))),
        "fixture-raw-sha",
    )


def test_metrics_hand_calculated_asymmetric_sizes():
    value = calculate_microstructure(book(), NOW, levels=2)
    assert value.spread == 2
    assert value.relative_spread == Decimal(".02") and value.spread_bps == 200
    assert value.top_of_book_imbalance == Decimal(".5")
    assert value.multi_level_imbalance == 0
    assert value.microprice == Decimal("100.5")
    assert value.depth_weighted_price == Decimal("100.2")
    assert value.liquidity_concentration_hhi == sum(
        (Decimal(x) / 1002) ** 2 for x in (297, 196, 101, 408)
    )
    assert value.quote_age_seconds == 2
    assert value.snapshot_hash == book().snapshot_hash
    with pytest.raises(FrozenInstanceError):
        value.spread = Decimal(0)


def test_single_level_depth_and_selection_are_explicit():
    value = calculate_microstructure(book(), NOW, levels=1)
    assert value.bid_levels_used == value.ask_levels_used == 1
    assert value.multi_level_imbalance == Decimal(".5")
    assert value.depth_weighted_price == Decimal("99.5")
    all_levels = calculate_microstructure(book(), NOW, levels=100)
    assert all_levels.bid_levels_used == all_levels.ask_levels_used == 2


def test_buy_walks_asks_and_sell_walks_bids_correct_amount_units():
    buy = executable_cost(book(), "BUY", Decimal(3), NOW)
    assert buy.depth_complete and buy.fillable_base_quantity == 3
    assert buy.visible_quote_notional == 305
    assert buy.executable_vwap == Decimal(305) / 3
    assert buy.best_quote == 101 and buy.marginal_quote == 102
    assert buy.slippage_quote == 2
    assert buy.slippage_bps == (Decimal(305) / 3 / 101 - 1) * 10000
    sell = executable_cost(book(), "SELL", Decimal(4), NOW)
    assert sell.visible_quote_notional == 395
    assert sell.executable_vwap == Decimal("98.75")
    assert sell.best_quote == 99 and sell.marginal_quote == 98
    assert sell.slippage_quote == 1 and sell.slippage_bps > 0
    assert buy.status == sell.status == "SNAPSHOT_ESTIMATE_NOT_ORDER_OR_FILL"


def test_partial_visible_depth_is_not_extrapolated():
    value = executable_cost(book(), "BUY", Decimal(7), NOW)
    assert value.fillable_base_quantity == 5
    assert value.unfilled_base_quantity == 2
    assert not value.depth_complete
    assert value.visible_quote_notional == 509
    assert value.executable_vwap == Decimal("101.8")


def test_best_level_only_has_zero_slippage():
    for side in ("BUY", "SELL"):
        value = executable_cost(book(), side, Decimal(".5"), NOW)
        assert value.executable_vwap == value.best_quote
        assert (
            value.slippage_bps
            == value.slippage_quote
            == value.marginal_depth_distance_bps
            == 0
        )


@pytest.mark.parametrize(
    "bad",
    [replace(book(), sequence_id=None), replace(book(), quality_status="UNKNOWN")],
)
def test_bad_provenance_blocks_metrics_and_cost(bad):
    with pytest.raises(ValueError):
        calculate_microstructure(bad, NOW)
    with pytest.raises(ValueError):
        executable_cost(bad, "BUY", Decimal(1), NOW)


@pytest.mark.parametrize(
    "moment", [NOW - timedelta(microseconds=1), NOW + timedelta(seconds=20)]
)
def test_point_in_time_and_staleness_both_block(moment):
    with pytest.raises(ValueError):
        calculate_microstructure(book(), moment)
    with pytest.raises(ValueError):
        executable_cost(book(), "BUY", Decimal(1), moment)


@pytest.mark.parametrize("levels", [0, -1, True, 1.5, 1001])
def test_invalid_level_count(levels):
    with pytest.raises(ValueError):
        calculate_microstructure(book(), NOW, levels=levels)


@pytest.mark.parametrize(
    "quantity",
    [Decimal(0), Decimal(-1), Decimal("NaN"), Decimal("Infinity"), Decimal(".01")],
)
def test_invalid_quantity_and_step_rejected(quantity):
    with pytest.raises(ValueError):
        executable_cost(book(), "BUY", quantity, NOW)


def test_float_quantity_wrong_side_and_unsupported_history_claims():
    with pytest.raises(TypeError):
        executable_cost(book(), "BUY", 0.5, NOW)
    with pytest.raises(ValueError):
        executable_cost(book(), "UNKNOWN", Decimal(1), NOW)
    with pytest.raises(UnsupportedCapability):
        short_horizon_volatility(book())
    with pytest.raises(UnsupportedCapability):
        causal_price_impact(book())


def test_deterministic_repeat_and_microsecond_age():
    assert calculate_microstructure(book(), NOW) == calculate_microstructure(
        book(), NOW
    )
    assert executable_cost(book(), "BUY", Decimal(2), NOW) == executable_cost(
        book(), "BUY", Decimal(2), NOW
    )
    assert calculate_microstructure(
        book(), NOW + timedelta(microseconds=1)
    ).quote_age_seconds == Decimal("2.000001")


def test_minimum_notional_is_enforced_before_depth_walk():
    snapshot = book()
    snapshot = replace(snapshot, market=replace(snapshot.market, minimum_notional=Decimal(100)))
    with pytest.raises(ValueError, match="MINIMUM_NOTIONAL_BREACH"):
        executable_cost(snapshot, "SELL", Decimal(".5"), NOW)
