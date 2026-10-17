"""Real approved-event allocation, NAV timing, rolling reward and leakage checks."""
import numpy as np
import pandas as pd
import pytest

from src.rl.signal_env import TradingEnv, evaluate_signal_policy
from src.rl.train_signal_ppo import train_signal_ppo


def trades(returns=(.1, -.05), dates=None):
    dates = dates or pd.date_range("2023-01-01 09:00", periods=len(returns), freq="2D", tz="UTC")
    rows = []
    for i, (value, date) in enumerate(zip(returns, dates)):
        date = pd.Timestamp(date)
        date = date.tz_localize("UTC") if date.tzinfo is None else date.tz_convert("UTC")
        rows.append({
            "event_id": i, "available_at": date, "entry_time": date,
            "exit_time": date + pd.Timedelta(hours=1), "entry_price": 100.,
            "exit_price": 100. * (1. + value + .003), "gross_return": value + .003,
            "net_return": value, "ml_probability": .7, "atr_ratio": 1.2,
            "atr_pct": .01, "regime": 0, "signal_confidence": .6,
            "split": "train", "probability_source": "purged_oof",
        })
    return pd.DataFrame(rows)


def test_allocation_uses_net_costed_return_once_and_remains_cash_collateralized():
    env = TradingEnv(trades())
    state, _ = env.reset(seed=42)
    assert state.shape == (8,) and state[0] == 0 and state[1] == 1 and state[2] == 0
    assert state[5] == 1  # Upstream regime 0 means range.
    state, _, done, _, info = env.step([.5])
    assert not done and info["equity"] == pytest.approx(1.05)
    assert state[0] == 0 and state[2] == 0
    assert state[1] == pytest.approx(1.05)
    _, _, done, _, info = env.step([.5])
    assert done and info["equity"] == pytest.approx(1.05 * .975)
    assert info["cash"] == info["equity"] and info["position"] == 0
    with pytest.raises(RuntimeError):
        env.step([.5])
    assert env.reset()[0][1] == 1


def test_future_outcomes_do_not_enter_entry_observation():
    original = trades()
    changed = original.copy()
    changed.loc[1, "net_return"] = .5
    changed.loc[1, "gross_return"] = .503
    changed.loc[1, "exit_price"] = 150.3
    left, right = TradingEnv(original), TradingEnv(changed)
    np.testing.assert_array_equal(left.reset()[0], right.reset()[0])
    np.testing.assert_array_equal(left.step([.3])[0], right.step([.3])[0])


def test_reward_uses_calendar_daily_nav_not_last_thirty_trade_results():
    frame = trades((.1, .1), ["2023-01-01 09:00", "2023-01-31 09:00"])
    env = TradingEnv(frame)
    env.reset()
    env.step([1.])
    _, reward, _, _, info = env.step([1.])
    expected = np.r_[np.zeros(29), .1]
    sharpe = expected.mean() / expected.std(ddof=1) * np.sqrt(365.)
    assert reward == pytest.approx(sharpe)
    assert info["rolling_30_calendar_day_sharpe"] == pytest.approx(sharpe)


def test_same_day_events_compound_once_in_daily_nav():
    frame = trades((.1, -.1), ["2023-01-01 09:00", "2023-01-01 11:00"])
    metric, daily = evaluate_signal_policy(frame, lambda _: 1.)
    assert len(daily) == 1
    assert daily.iloc[0] == pytest.approx(-.01)
    assert metric["return"] == pytest.approx(-.01)


def test_midnight_exit_belongs_to_day_ending_and_quiet_calendar_is_retained():
    frame = trades((.1,), ["2023-01-01 23:00"])
    metric, daily = evaluate_signal_policy(frame, lambda _: 1., start="2023-01-01", end="2023-01-04")
    np.testing.assert_allclose(daily.to_numpy(), [.1, 0., 0.])
    assert metric["daily_observations"] == 3


def test_actual_intratrade_marks_determine_daily_nav_and_maximum_drawdown():
    frame = trades((.05,), ["2023-01-01 23:00"])
    frame.loc[0, "exit_time"] = pd.Timestamp("2023-01-02 00:15", tz="UTC")
    frame["mark_times"] = [[pd.Timestamp("2023-01-02 00:00", tz="UTC"), frame.exit_time.iloc[0]]]
    frame["mark_returns"] = [[-.2, .05]]
    metric, daily = evaluate_signal_policy(frame, lambda _: 1.)
    np.testing.assert_allclose(daily.to_numpy(), [-.2, .3125])
    assert metric["return"] == pytest.approx(.05)
    assert metric["max_drawdown"] == pytest.approx(.2)
    assert "actual intratrade" in metric["valuation"]


def test_intratrade_final_mark_must_match_realized_costed_return():
    frame = trades((.05,))
    frame["mark_times"] = [[frame.exit_time.iloc[0]]]
    frame["mark_returns"] = [[.06]]
    with pytest.raises(ValueError, match="finish at event net_return"):
        TradingEnv(frame)


def test_zero_variance_reward_is_zero_but_reported_sharpe_is_undefined():
    env = TradingEnv(trades())
    env.reset()
    assert env.step([0.])[1] == 0.
    assert env.step([0.])[1] == 0.
    metric, daily = evaluate_signal_policy(trades(), lambda _: 0.)
    assert metric["sharpe"] is None and not metric["sharpe_defined"]
    assert metric["num_trades"] == 0 and (daily == 0).all()


def test_empty_oos_group_is_cash_across_its_explicit_calendar():
    empty = pd.DataFrame()
    empty.attrs.update(period_start="2024-01-01", period_end="2024-01-04")
    metric, daily = evaluate_signal_policy(empty, lambda _: pytest.fail("no trade exists"))
    assert len(daily) == 3 and (daily == 0).all()
    assert metric["sharpe"] is None and metric["return"] == 0.


def test_bad_probability_provenance_overlap_and_out_of_range_action_rejected():
    invalid = trades()
    invalid.loc[0, "probability_source"] = "frozen_train_model"
    with pytest.raises(ValueError, match="purged_oof"):
        TradingEnv(invalid)
    overlap = trades()
    overlap.loc[1, "available_at"] = overlap.available_at.iloc[0] + pd.Timedelta(minutes=30)
    with pytest.raises(ValueError, match="preceding trade exit"):
        TradingEnv(overlap)
    env = TradingEnv(trades())
    env.reset()
    for action in ([np.nan], [-.1], [1.1]):
        with pytest.raises(ValueError):
            env.step(action)


def test_episode_round_robin_resets_cash_and_excludes_held_calendar_gaps():
    frame = trades((.1, -.05), ["2023-01-01 09:00", "2023-03-01 09:00"])
    frame["episode_id"] = [0, 1]
    frame.attrs.update(period_start="2023-01-01", period_end="2023-04-01", episode_periods={
        0: {"start": "2023-01-01", "end": "2023-01-03"},
        1: {"start": "2023-03-01", "end": "2023-03-03"},
    })
    env = TradingEnv(frame)
    env.reset(seed=42)
    assert env.period_start == pd.Timestamp("2023-01-01", tz="UTC")
    assert env.step([1.])[2]
    state, _ = env.reset()
    assert state[1] == 1. and env.period_start == pd.Timestamp("2023-03-01", tz="UTC")
    assert len(env.calendar) == 2
    assert env.step([1.])[2]
    env.reset()
    assert env.period_start == pd.Timestamp("2023-01-01", tz="UTC")


def test_validation_trades_cannot_be_used_to_fit_ppo():
    val = trades()
    val["split"], val["probability_source"] = "val", "frozen_train_model"
    with pytest.raises(ValueError, match="training trades"):
        train_signal_ppo(val)
