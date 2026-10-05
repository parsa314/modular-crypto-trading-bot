import math

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("gymnasium")

from gymnasium.utils.env_checker import check_env

from research_bot.ensemble_env import EnsembleTradingEnv, TradingEnvConfig, empirical_cvar


def candles(closes, opens=None):
    close = np.asarray(closes, dtype=float)
    open_ = close.copy() if opens is None else np.asarray(opens, dtype=float)
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=len(close), freq="h", tz="UTC"),
        "open": open_, "high": np.maximum(open_, close) + 1,
        "low": np.minimum(open_, close) - 1, "close": close,
        "volume": np.full(len(close), 100.0), "feature": np.arange(len(close), dtype=float),
    })


def config(**kwargs):
    defaults = dict(
        lookback=2, initial_balance=1000.0, transaction_cost=0.0,
        slippage_bps=0.0, max_asset_weight=0.5, max_drawdown=0.9,
        cvar_alpha=0.95, cvar_window=10, cvar_min_samples=1, max_cvar=1.0,
    )
    defaults.update(kwargs)
    return TradingEnvConfig(**defaults)


def test_fractional_empirical_tail_and_sign():
    # The worst 37.5% of four observations has mass 1.5: all of 10, half of 4.
    assert empirical_cvar([0, 0, 4, 10], alpha=0.625) == pytest.approx(8.0)
    assert empirical_cvar([0, 0, 0, 10], alpha=0.75) == pytest.approx(10.0)
    assert empirical_cvar([-3, -2, -1], alpha=0.95) == pytest.approx(-1.0)
    assert empirical_cvar([0.125], alpha=0.999) == pytest.approx(0.125)
    assert empirical_cvar([1e308, 1e308], alpha=.1) == pytest.approx(1e308)


@pytest.mark.parametrize("losses,alpha", [([], .95), ([np.nan], .95), ([np.inf], .95),
                                             ([[1, 2]], .95), ([1], 0), ([1], 1), ([1], np.nan)])
def test_invalid_cvar_samples_fail_closed(losses, alpha):
    with pytest.raises(ValueError):
        empirical_cvar(losses, alpha)


@pytest.mark.parametrize("overrides", [
    {"lookback": 0}, {"lookback": 2.5}, {"lookback": True},
    {"initial_balance": np.inf}, {"initial_balance": 0},
    {"transaction_cost": -0.01}, {"transaction_cost": 1},
    {"slippage_bps": 10_000}, {"slippage_bps": -1},
    {"max_asset_weight": 1.1}, {"max_drawdown": 0}, {"max_drawdown": 1},
    {"cvar_alpha": 1}, {"max_cvar": 0}, {"cvar_window": 2, "cvar_min_samples": 3},
])
def test_invalid_configuration_is_rejected(overrides):
    with pytest.raises(ValueError):
        config(**overrides)


def test_non_config_object_is_not_silently_replaced():
    with pytest.raises(ValueError, match="config"):
        EnsembleTradingEnv(candles([100] * 4), ["feature"], config=False)


def test_observation_cutoff_and_next_open_execution():
    frame = candles([100, 100, 120, 120], opens=[100, 100, 110, 120])
    env = EnsembleTradingEnv(frame, ["feature"], config())
    obs, _ = env.reset()
    np.testing.assert_array_equal(obs[:, 0], [0, 1])
    assert obs.shape == (2, 5)
    _, reward, terminated, truncated, info = env.step(1)
    # Buy 500 / 110 at next open, then mark at its distinct close 120.
    assert info["quantity"] == pytest.approx(50 / 11)
    assert info["cash"] == pytest.approx(500)
    assert info["equity"] == pytest.approx(500 + 6000 / 11)
    assert reward == pytest.approx(math.log(info["equity"] / 1000))
    assert not terminated and not truncated
    assert info["decision_timestamp"] == frame.timestamp.iloc[1].isoformat()
    assert info["timestamp"] == frame.timestamp.iloc[2].isoformat()


def test_fees_charged_once_and_log_rewards_telescope():
    env = EnsembleTradingEnv(candles([100] * 4), ["feature"],
                             config(transaction_cost=.01, max_asset_weight=1))
    env.reset()
    _, reward_buy, _, _, buy = env.step(1)
    assert buy["quantity"] == pytest.approx(1000 / 101)
    assert buy["fees"] == pytest.approx(1000 / 101)
    assert buy["equity"] == pytest.approx(100_000 / 101)
    _, reward_sell, terminated, truncated, sell = env.step(2)
    assert sell["equity"] == pytest.approx(99_000 / 101)
    assert sell["fees"] == pytest.approx(1000 / 101)
    assert reward_buy + reward_sell == pytest.approx(math.log(sell["equity"] / 1000))
    assert terminated and not truncated
    assert sell["quantity"] == 0


def test_post_cost_target_and_adverse_slippage():
    env = EnsembleTradingEnv(candles([100] * 4), ["feature"],
                             config(transaction_cost=.01, slippage_bps=100))
    env.reset()
    _, reward, _, _, info = env.step(1)
    assert info["exposure"] == pytest.approx(.5)
    assert info["cash"] >= 0
    assert info["equity"] < 1000
    # Independent cash conservation using the adverse buy fill 101.
    assert info["cash"] + info["quantity"] * 101 * 1.01 == pytest.approx(1000)
    assert info["fees"] == pytest.approx(info["quantity"] * 101 * .01)
    assert reward == pytest.approx(math.log(info["equity"] / 1000))


def test_terminal_liquidation_uses_final_close_and_one_exit_fee():
    env = EnsembleTradingEnv(candles([100, 100, 100, 110]), ["feature"],
                             config(transaction_cost=.01, max_asset_weight=1))
    env.reset()
    env.step(1)
    _, _, terminated, truncated, info = env.step(0)
    assert terminated and not truncated
    assert info["terminal_liquidation"] and not info["risk_stop"]
    assert info["quantity"] == 0
    assert info["equity"] == pytest.approx(1000 / 101 * 110 * .99)
    assert info["fees"] == pytest.approx(1000 / 101 * 110 * .01)


def test_insufficient_history_prevents_buying():
    env = EnsembleTradingEnv(candles([100] * 6), ["feature"],
                             config(cvar_min_samples=3))
    env.reset()
    for _ in range(2):
        _, reward, _, _, info = env.step(1)
        assert info["cvar"] is None and info["risk_cap"] == 0
        assert info["quantity"] == 0 and info["equity"] == 1000 and reward == 0
    _, _, _, _, info = env.step(1)
    assert info["cvar_sample_count"] == 3
    assert info["quantity"] == pytest.approx(5)


def test_cvar_uses_raw_observed_returns_and_hold_reduces_tightened_cap():
    frame = candles([100, 100, 80, 80, 80], opens=[100, 100, 100, 80, 80])
    frame["feature"] = 0  # Risk must not use the normalized market feature.
    env = EnsembleTradingEnv(frame, ["feature"], config(max_cvar=.05, cvar_window=2))
    env.reset()
    _, _, _, _, entry = env.step(1)
    assert entry["cvar"] == 0
    assert entry["quantity"] == 5
    _, _, _, _, reduced = env.step(0)
    assert reduced["cvar"] == pytest.approx(.2)
    assert reduced["risk_cap"] == pytest.approx(.25)
    assert reduced["exposure"] == pytest.approx(.25)
    assert reduced["quantity"] == pytest.approx(2.8125)


def test_future_asset_returns_cannot_change_current_risk_sizing():
    first = candles([100, 100, 100, 100, 100, 100])
    second = candles([100, 100, 100, 20, 300, 50])
    envs = [EnsembleTradingEnv(frame, ["feature"], config()) for frame in (first, second)]
    for env in envs:
        env.reset()
    results = [env.step(1)[4] for env in envs]
    for key in ("cvar", "risk_cap", "equity", "cash", "quantity", "fees"):
        assert results[0][key] == results[1][key]


def test_drawdown_stop_liquidates_with_costs_and_forbids_further_steps():
    frame = candles([100, 100, 70, 70, 70], opens=[100, 100, 100, 70, 70])
    env = EnsembleTradingEnv(frame, ["feature"],
                             config(transaction_cost=.01, max_asset_weight=1, max_drawdown=.2))
    env.reset()
    _, reward, terminated, truncated, info = env.step(1)
    assert terminated and not truncated and info["risk_stop"]
    assert info["quantity"] == 0
    assert info["equity"] == pytest.approx(1000 * .7 * .99 / 1.01)
    assert info["fees"] == pytest.approx(1700 / 101)
    assert info["drawdown"] == pytest.approx(1 - info["equity"] / 1000)
    assert reward == pytest.approx(math.log(info["equity"] / 1000))
    with pytest.raises(RuntimeError):
        env.step(0)


def test_drawdown_uses_running_peak():
    frame = candles([100, 100, 120, 108, 130])
    env = EnsembleTradingEnv(frame, ["feature"], config(max_asset_weight=1, max_drawdown=.09))
    env.reset()
    env.step(1)
    _, _, terminated, _, info = env.step(0)
    assert terminated
    assert info["drawdown"] == pytest.approx(.1)


def test_full_remaining_data_is_used_without_invalid_terminal_observation():
    env = EnsembleTradingEnv(candles([100] * 5), ["feature"], config())
    env.reset()
    for index in (2, 3, 4):
        obs, reward, terminated, truncated, info = env.step(0)
        np.testing.assert_array_equal(obs[:, 0], [index - 1, index])
        assert np.isfinite(obs).all() and env.observation_space.contains(obs)
        assert reward == 0 and not truncated
        assert terminated == (index == 4)
        assert info["equity"] == 1000
    with pytest.raises(RuntimeError):
        env.step(0)


def test_start_index_preserves_risk_context_and_first_execution_boundary():
    frame = candles([100] * 8)
    env = EnsembleTradingEnv(frame, ["feature"], config(cvar_min_samples=4), start_index=4)
    obs, info = env.reset()
    np.testing.assert_array_equal(obs[:, 0], [3, 4])
    assert info["cvar_sample_count"] == 4
    _, _, _, _, info = env.step(1)
    assert info["timestamp"] == frame.timestamp.iloc[5].isoformat()
    assert info["quantity"] == 5


@pytest.mark.parametrize("start", [0, 4, -1, 1.5, True])
def test_invalid_start_index_is_rejected(start):
    with pytest.raises(ValueError):
        EnsembleTradingEnv(candles([100] * 5), ["feature"], config(), start_index=start)


@pytest.mark.parametrize("problem", ["duplicate_time", "reversed_time", "nan_feature", "bad_high", "zero_close"])
def test_invalid_market_data_is_rejected(problem):
    frame = candles([100] * 5)
    if problem == "duplicate_time":
        frame.loc[1, "timestamp"] = frame.timestamp.iloc[0]
    elif problem == "reversed_time":
        frame = frame.iloc[::-1]
    elif problem == "nan_feature":
        frame.loc[1, "feature"] = np.nan
    elif problem == "bad_high":
        frame.loc[1, "high"] = 50
    else:
        frame.loc[1, "close"] = 0
    with pytest.raises(ValueError):
        EnsembleTradingEnv(frame, ["feature"], config())


def test_gymnasium_contract_and_reset_reproducibility():
    env = EnsembleTradingEnv(candles([100] * 6), ["feature"], config())
    check_env(env, skip_render_check=True)
    first, _ = env.reset(seed=17)
    env.step(1)
    second, info = env.reset(seed=17)
    np.testing.assert_array_equal(first, second)
    assert info["equity"] == 1000 and info["quantity"] == 0


def test_finite_liquidated_endpoint_does_not_bootstrap_as_time_limit():
    pytest.importorskip("stable_baselines3")
    from stable_baselines3.common.vec_env import DummyVecEnv
    env = DummyVecEnv([lambda: EnsembleTradingEnv(candles([100] * 3), ["feature"], config())])
    env.reset()
    _, _, done, infos = env.step(np.array([1]))
    assert done[0]
    assert infos[0]["data_exhausted"] and infos[0]["episode_end_reason"] == "DATA_END"
    assert infos[0]["terminal_liquidation"]
    assert infos[0]["TimeLimit.truncated"] is False
    env.close()
