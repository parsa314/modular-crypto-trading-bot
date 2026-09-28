"""Execution and label-availability regressions for the frozen V58 barriers."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from research_bot.v58.barriers import (
    BarrierPolicy, DecisionEvent, EnteredEvent, OutcomeState,
    barrier_policy_hash, materialize_entry, resolve_barriers,
)
from research_bot.v58.contracts import Direction, StrategyArm, TargetClass
from research_bot.v58.targets import OHLCBar


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def decision(**kwargs):
    event = DecisionEvent("event", "BTC/USDT", "coinex", StrategyArm.ARM_A,
                          Direction.LONG, START, 2.0, "features", "data", "code", "strategy")
    return replace(event, **kwargs)


def bar(index=0, *, open=100, high=101, low=99, close=100):
    return OHLCBar(START + timedelta(hours=4 * index), open, high, low, close)


def entered(policy=None):
    policy = policy or BarrierPolicy()
    return EnteredEvent("entry", "event", START, 100, 98, 103, 2, barrier_policy_hash(policy))


def test_close_decision_and_immediate_next_open_share_timestamp():
    event = decision()
    entry = materialize_entry(event, entry_bar=bar(), policy=BarrierPolicy())
    assert entry.entry_time == event.decision_time
    assert (entry.stop_price, entry.target_price, entry.risk_distance) == (98, 103, 2)


def test_missing_immediate_entry_open_is_not_filled_later():
    with pytest.raises(ValueError, match="immediate"):
        materialize_entry(decision(), entry_bar=bar(1), policy=BarrierPolicy())


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_duration_requires_positive_integer_seconds(value):
    with pytest.raises(ValueError, match="bar_duration_seconds"):
        decision(bar_duration_seconds=value)


def test_explicit_duration_propagates_to_label_availability_and_identity():
    policy = BarrierPolicy(holding_horizon_bars=1)
    hourly = materialize_entry(decision(bar_duration_seconds=3600), entry_bar=bar(), policy=policy)
    h4 = materialize_entry(decision(), entry_bar=bar(), policy=policy)
    outcome = resolve_barriers(hourly, direction=Direction.LONG, bars=[bar()], policy=policy)
    assert hourly.bar_duration_seconds == 3600
    assert hourly.entry_id != h4.entry_id
    assert outcome.resolved_at == START + timedelta(hours=1)


def test_nonspot_execution_cannot_be_activated_by_market_flag():
    with pytest.raises(ValueError, match="only spot"):
        decision(market_type="derivatives")


@pytest.mark.parametrize("direction", ["LONG", "SHORT", None])
def test_direction_requires_enum(direction):
    with pytest.raises(ValueError, match="direction"):
        decision(direction=direction)


def test_spot_short_materialization_rejected():
    with pytest.raises(ValueError, match="spot"):
        materialize_entry(decision(direction=Direction.SHORT), entry_bar=bar(), policy=BarrierPolicy())


@pytest.mark.parametrize("bars", [[bar(1, high=105, close=104)], [bar(), bar(2, high=105, close=104)]])
def test_missing_followup_cannot_manufacture_outcome(bars):
    with pytest.raises(ValueError, match="contiguous"):
        resolve_barriers(entered(), direction=Direction.LONG, bars=bars, policy=BarrierPolicy())


def test_first_followup_open_must_equal_materialized_entry():
    with pytest.raises(ValueError, match="entry.*open"):
        resolve_barriers(entered(), direction=Direction.LONG,
                         bars=[bar(open=110, high=111, low=96)], policy=BarrierPolicy())


def test_policy_cannot_change_after_entry():
    with pytest.raises(ValueError, match="policy"):
        resolve_barriers(entered(), direction=Direction.LONG, bars=[],
                         policy=BarrierPolicy(reward_r=2))


def test_direction_cannot_change_after_entry():
    with pytest.raises(ValueError, match="direction"):
        resolve_barriers(entered(), direction=Direction.SHORT, bars=[], policy=BarrierPolicy())


@pytest.mark.parametrize("changes", [{"risk_distance": 3}, {"target_price": 104}])
def test_entry_geometry_must_match_frozen_policy(changes):
    with pytest.raises(ValueError, match="does not match"):
        resolve_barriers(replace(entered(), **changes), direction=Direction.LONG,
                         bars=[bar()], policy=BarrierPolicy())


def test_intrabar_stop_first_label_only_available_at_close():
    outcome = resolve_barriers(entered(), direction=Direction.LONG,
                               bars=[bar(high=104, low=97)], policy=BarrierPolicy())
    assert outcome.target_class is TargetClass.SL
    assert outcome.intrabar_ambiguity
    assert outcome.resolved_at == START + timedelta(hours=4)
    assert outcome.gross_return_r == -1


def test_timeout_label_only_available_at_horizon_close():
    policy = BarrierPolicy(holding_horizon_bars=2)
    outcome = resolve_barriers(entered(policy), direction=Direction.LONG,
                               bars=[bar(), bar(1, close=100.5)], policy=policy)
    assert outcome.target_class is TargetClass.TIMEOUT
    assert outcome.resolved_at == START + timedelta(hours=8)
    assert outcome.exit_price == 100.5


@pytest.mark.parametrize("ohlc,label,price,reason", [
    ({"open": 96, "high": 104, "low": 95}, TargetClass.SL, 96, "GAP_STOP"),
    ({"open": 105, "high": 106, "low": 96}, TargetClass.TP, 103, "GAP_TARGET"),
])
def test_next_bar_gap_has_known_open_execution(ohlc, label, price, reason):
    outcome = resolve_barriers(entered(), direction=Direction.LONG,
                               bars=[bar(), bar(1, **ohlc)], policy=BarrierPolicy())
    assert (outcome.target_class, outcome.exit_price, outcome.exit_reason) == (label, price, reason)
    assert outcome.resolved_at == START + timedelta(hours=4)


def test_incomplete_contiguous_path_is_censored():
    outcome = resolve_barriers(entered(), direction=Direction.LONG,
                               bars=[bar(), bar(1)], policy=BarrierPolicy())
    assert outcome.state is OutcomeState.RIGHT_CENSORED
    assert outcome.observed_bars == 2
    assert outcome.resolved_at is None


def test_flat_round_trip_cost_is_preserved():
    outcome = resolve_barriers(entered(), direction=Direction.LONG,
                               bars=[bar(high=103, close=103)], policy=BarrierPolicy())
    assert outcome.gross_return == pytest.approx(.03)
    assert outcome.after_cost(24) == pytest.approx(.0276)
    assert (BarrierPolicy().atr_multiple, BarrierPolicy().reward_r,
            BarrierPolicy().holding_horizon_bars) == (1, 1.5, 12)
