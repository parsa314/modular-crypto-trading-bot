import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from research_bot.ensemble_rl import (
    TrainOnlyScaler, WalkForwardConfig, buy_and_hold_equity, equity_metrics,
    evaluate_agent, run_experiment, synthetic_ohlcv, walk_forward_splits,
)


def test_expanding_folds_and_embargo_are_disjoint():
    cfg = WalkForwardConfig(100, 20, 3, 2)
    folds = list(walk_forward_splits(166, cfg))
    assert folds == [(0, 100, 102, 122), (1, 122, 124, 144), (2, 144, 146, 166)]
    with pytest.raises(ValueError, match="need 166"):
        list(walk_forward_splits(165, cfg))


def test_scaler_uses_only_train_and_preserves_prices():
    train = pd.DataFrame({"signal": [1., 3.], "constant": [7., 7.], "close": [100., 101.]})
    scaler = TrainOnlyScaler(["signal", "constant"]).fit(train)
    test = pd.DataFrame({"signal": [1000.], "constant": [7.], "close": [500.]})
    result = scaler.transform(test)
    assert result.signal.iloc[0] == 998
    assert result.constant.iloc[0] == 0
    assert result.close.iloc[0] == 500
    assert scaler.to_dict()["mean"] == [2, 7]
    assert train.signal.tolist() == [1, 3]
    with pytest.raises(RuntimeError):
        TrainOnlyScaler(["signal"]).transform(test)
    with pytest.raises(ValueError):
        scaler.transform(test.assign(signal=np.inf))


def test_drawdown_uses_running_peak_and_includes_initial_capital():
    pytest.importorskip("gymnasium")
    result = equity_metrics([100, 90, 120, 110], periods_per_year=365)
    assert result["max_drawdown"] == pytest.approx(.10)
    assert result["net_return"] == pytest.approx(.10)
    assert result["bars"] == 3
    cash = equity_metrics([100, 100, 100], periods_per_year=365)
    assert cash["annualized_sharpe"] is None
    assert cash["cvar_95"] == 0


def test_buy_hold_exact_round_trip_and_static_quantity():
    pytest.importorskip("gymnasium")
    from research_bot.ensemble_env import TradingEnvConfig
    cfg = TradingEnvConfig(initial_balance=1000, transaction_cost=.01, slippage_bps=0,
                           max_asset_weight=1)
    test = pd.DataFrame({"open": [100, 100], "close": [100, 110]})
    values = buy_and_hold_equity(test, cfg)
    # Fees are quoted in cash: 1000 pays for units plus the buy fee.
    assert values == pytest.approx([1000, 1000/1.01, 1000/1.01*1.10*.99])


def test_partial_weight_baseline_uses_post_cost_exposure():
    pytest.importorskip("gymnasium")
    from research_bot.ensemble_env import TradingEnvConfig
    cfg = TradingEnvConfig(initial_balance=1000, transaction_cost=.01,
                           slippage_bps=0, max_asset_weight=.5)
    test = pd.DataFrame({"open": [100, 100], "close": [100, 100]})
    # Equal cash and marked holdings after entry: q*100 = 1000-q*101,
    # hence q=1000/201. The final sale yields 99 per unit.
    assert buy_and_hold_equity(test, cfg) == pytest.approx([1000, 1000*200/201, 1000*199/201])


def test_risk_stop_keeps_cash_to_common_end():
    pytest.importorskip("gymnasium")
    from research_bot.ensemble_env import TradingEnvConfig
    frame = synthetic_ohlcv(26)
    frame.loc[:, ["open", "close"]] = 100.
    frame.loc[:, "high"], frame.loc[:, "low"] = 101., 99.
    frame.loc[21:, "close"] = 50.
    frame.loc[21:, "low"] = 49.
    frame["signal"] = 0.
    cfg = TradingEnvConfig(lookback=2, initial_balance=1000, max_asset_weight=1,
        transaction_cost=0, slippage_bps=0, max_drawdown=.2)

    class Buy:
        def predict(self, obs, deterministic):
            return np.array(1), None

    equity, details = evaluate_agent(Buy(), frame, ["signal"], cfg, start_index=20)
    assert equity == pytest.approx([1000, 500, 500, 500, 500, 500])
    assert details["risk_stop"]
    assert details["action_counts"]["1"] == 1


def test_real_training_artifacts_cover_every_fold(tmp_path):
    pytest.importorskip("torch")
    pytest.importorskip("stable_baselines3")
    from research_bot.ensemble_env import TradingEnvConfig
    frame = synthetic_ohlcv(350)
    destination = tmp_path / "run"
    result = run_experiment(frame, destination, data_source="synthetic engineering fixture",
        synthetic=True, walk_forward=WalkForwardConfig(120, 30, 2, 1),
        env_config=TradingEnvConfig(lookback=4), timesteps=128, seeds=(7,))
    assert len(result) == 6
    assert set(result.model) == {"cash", "buy_and_hold", "ppo_lstm"}
    assert result.bars.tolist() == [30] * 6
    manifest = json.loads((destination / "manifest.json").read_text())
    assert manifest["status"] == "COMPLETED"
    assert manifest["scientific_decision"] == "NOT_EVALUATED"
    assert manifest["evidence_state"] == "ENGINEERING_SMOKE"
    assert not manifest["live_execution"] and not manifest["paper_execution"]
    for fold in range(2):
        equity = pd.read_csv(destination / f"fold_{fold}/equity.csv")
        assert len(equity) == 31
        assert (destination / f"fold_{fold}/ppo_seed_7.zip").exists()
        assert manifest["folds"][fold]["trained_timesteps"]["7"] == 128
        assert pd.Timestamp(equity.timestamp.iloc[1]) == pd.Timestamp(manifest["folds"][fold]["test_start"])
    for path, expected in manifest["artifacts_sha256"].items():
        assert hashlib.sha256((destination / path).read_bytes()).hexdigest() == expected
    with pytest.raises(FileExistsError):
        run_experiment(frame, destination, data_source="synthetic", synthetic=True,
            walk_forward=WalkForwardConfig(120, 30, 2, 1), timesteps=128, seeds=(7,))


@pytest.mark.parametrize("cfg", [(1, 20, 3, 0), (100, 0, 3, 0), (100, 20, 0, 0), (100, 20, 3, -1), (100, 20, True, 0)])
def test_bad_split_configs_fail(cfg):
    with pytest.raises(ValueError):
        WalkForwardConfig(*cfg)
