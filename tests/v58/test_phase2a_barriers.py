from datetime import datetime, timedelta, timezone

import pytest

from research_bot.v58.barriers import (
    BarrierPolicy, DecisionEvent, OutcomeState, materialize_entry, resolve_barriers,
)
from research_bot.v58.contracts import Direction, StrategyArm, TargetClass
from research_bot.v58.targets import OHLCBar


UTC = timezone.utc


def _decision(direction: Direction = Direction.LONG) -> DecisionEvent:
    return DecisionEvent(
        event_id="e" * 64, symbol="BTC/USDT", venue="coinex",
        strategy_arm=StrategyArm.ARM_A, direction=direction,
        decision_time=datetime(2026, 1, 1, 0, tzinfo=UTC), decision_atr=2.0,
        feature_snapshot_id="f" * 64, data_version="d1", code_version="c1",
        strategy_version="s1",
    )


def _bar(hour: int, o: float, h: float, l: float, c: float) -> OHLCBar:
    return OHLCBar(datetime(2026, 1, 1, hour, tzinfo=UTC), o, h, l, c)


def test_next_open_materializes_barriers_without_mutating_decision():
    event = _decision()
    entered = materialize_entry(event, entry_bar=_bar(1, 100, 101, 99, 100), policy=BarrierPolicy())
    assert entered.entry_time > event.decision_time
    assert entered.risk_distance == 2.0
    assert entered.stop_price == 98.0
    assert entered.target_price == 103.0
    assert not hasattr(event, "entry_price")


def test_stop_first_when_both_barriers_touch():
    policy = BarrierPolicy()
    entered = materialize_entry(_decision(), entry_bar=_bar(1, 100, 101, 99, 100), policy=policy)
    outcome = resolve_barriers(entered, direction=Direction.LONG, bars=[_bar(1, 100, 110, 96, 101)], policy=policy)
    assert outcome.target_class is TargetClass.SL
    assert outcome.intrabar_ambiguity is True


def test_gap_target_is_conservative_and_precedes_intrabar_stop():
    policy = BarrierPolicy()
    entered = materialize_entry(_decision(), entry_bar=_bar(1, 100, 101, 99, 100), policy=policy)
    outcome = resolve_barriers(entered, direction=Direction.LONG, bars=[_bar(1, 110, 111, 96, 100)], policy=policy)
    assert outcome.target_class is TargetClass.TP
    assert outcome.exit_price == entered.target_price
    assert outcome.exit_reason == "GAP_TARGET"


def test_incomplete_horizon_is_right_censored():
    policy = BarrierPolicy(holding_horizon_bars=3)
    entered = materialize_entry(_decision(), entry_bar=_bar(1, 100, 101, 99, 100), policy=policy)
    outcome = resolve_barriers(entered, direction=Direction.LONG, bars=[_bar(1, 100, 101, 99, 100), _bar(2, 100, 101, 99, 100)], policy=policy)
    assert outcome.state is OutcomeState.RIGHT_CENSORED
    assert outcome.target_class is None
    assert outcome.resolved_at is None


def test_horizon_bar_is_included_before_timeout():
    policy = BarrierPolicy(holding_horizon_bars=3)
    entered = materialize_entry(_decision(), entry_bar=_bar(1, 100, 101, 99, 100), policy=policy)
    bars = [_bar(1, 100, 101, 99, 100), _bar(2, 100, 101, 99, 100), _bar(3, 100, 109, 99, 109)]
    outcome = resolve_barriers(entered, direction=Direction.LONG, bars=bars, policy=policy)
    assert outcome.target_class is TargetClass.TP
    assert outcome.observed_bars == 3


def test_no_hit_at_horizon_times_out():
    policy = BarrierPolicy(holding_horizon_bars=1)
    entered = materialize_entry(_decision(), entry_bar=_bar(1, 100, 101, 99, 100), policy=policy)
    outcome = resolve_barriers(entered, direction=Direction.LONG, bars=[_bar(1, 100, 101, 99, 100.5)], policy=policy)
    assert outcome.target_class is TargetClass.TIMEOUT
    assert outcome.exit_price == 100.5


@pytest.mark.parametrize("kwargs", [
    {"atr_multiple": float("nan")}, {"reward_r": 0},
    {"holding_horizon_bars": True}, {"holding_horizon_bars": 0},
])
def test_invalid_policy_rejected(kwargs):
    with pytest.raises(ValueError):
        BarrierPolicy(**kwargs)


def test_entry_must_be_after_decision():
    with pytest.raises(ValueError, match="strictly after"):
        materialize_entry(_decision(), entry_bar=_bar(0, 100, 101, 99, 100), policy=BarrierPolicy())


def test_cost_is_separate_from_label_and_charged_once():
    policy = BarrierPolicy()
    entered = materialize_entry(_decision(), entry_bar=_bar(1, 100, 101, 99, 100), policy=policy)
    outcome = resolve_barriers(entered, direction=Direction.LONG, bars=[_bar(1, 100, 103, 99, 103)], policy=policy)
    assert outcome.target_class is TargetClass.TP
    assert outcome.gross_return == pytest.approx(0.03)
    assert outcome.gross_return_r == pytest.approx(1.5)
    assert outcome.after_cost(24) == pytest.approx(0.0276)
    assert outcome.target_class is TargetClass.TP


def test_sensitivity_contracts_are_not_primary_defaults():
    primary = BarrierPolicy()
    assert (primary.atr_multiple, primary.reward_r, primary.holding_horizon_bars) == (1.0, 1.5, 12)
