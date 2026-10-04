"""Actual PPO artifact and observation checks without exchange requests."""

import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("gymnasium")
pytest.importorskip("stable_baselines3")

from research_bot.ensemble_env import EnsembleTradingEnv, TradingEnvConfig
from research_bot.ensemble_features import FEATURE_COLUMNS, extract_all_features
from research_bot.ensemble_rl import (
    TrainOnlyScaler, WalkForwardConfig, main as experiment_main,
    run_experiment, synthetic_ohlcv,
)
from research_bot.live_runtime import FrozenPpoPolicy, LiveConfig, LiveController, SafetyStop


MARKET = {"exchange_id": "coinex", "symbol": "BTC/USDT", "timeframe": "4h"}
SEMANTIC_SOURCES = ("ensemble_features.py", "ensemble_env.py", "ensemble_agent.py", "ensemble_rl.py")


@pytest.fixture(scope="module", autouse=True)
def _single_torch_thread():
    previous = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(previous)


@pytest.fixture(scope="module")
def trained_run(tmp_path_factory):
    """One genuine PPO rollout/update, reused only as engineering test data."""
    frame = synthetic_ohlcv(350)
    output = tmp_path_factory.mktemp("live-policy") / "run"
    result = run_experiment(
        frame, output, data_source="synthetic policy contract fixture; no market evidence",
        synthetic=True, walk_forward=WalkForwardConfig(120, 30, 1, 1),
        env_config=TradingEnvConfig(lookback=4), timesteps=128, seeds=(7,),
        market_identity=MARKET,
    )
    assert result.bars.tolist() == [30, 30, 30]
    return output, frame


@pytest.fixture
def artifact(trained_run, tmp_path):
    output, _ = trained_run
    target = tmp_path / "run"
    shutil.copytree(output, target)
    return target


def _manifest(path):
    return json.loads((path / "manifest.json").read_text())


def _write_manifest(path, manifest):
    (path / "manifest.json").write_text(json.dumps(manifest, allow_nan=False))


def _rewrite_scaler_with_matching_checksum(path, mutate):
    scaler_path = path / "fold_0/scaler.json"
    scaler = json.loads(scaler_path.read_text())
    mutate(scaler)
    # A deliberately malformed fixture can include NaN. Rehash it so the
    # semantic check, rather than the integrity check, must reject the file.
    scaler_path.write_text(json.dumps(scaler))
    manifest = _manifest(path)
    manifest["artifacts_sha256"]["fold_0/scaler.json"] = hashlib.sha256(scaler_path.read_bytes()).hexdigest()
    _write_manifest(path, manifest)


def _history(frame):
    records = frame.rename(columns={"timestamp": "timestamp_ms"}).copy()
    records["timestamp_ms"] = pd.to_datetime(records.timestamp_ms, utc=True).dt.as_unit("ns").astype("int64") // 1_000_000
    return records.to_dict("records")


def _live_config(**overrides):
    return LiveConfig(**{
        "exchange_id": "coinex", "symbol": "BTC/USDT", "timeframe": "4h",
        "capital_limit_quote": 1000.0, "max_order_quote": 100.0,
        "max_daily_loss": 0.04, "max_drawdown": 0.08,
        "max_position_weight": 0.2, **overrides,
    })


def _exchange_identity(config, mode):
    # No adapter is instantiated and no private/public network methods exist.
    return SimpleNamespace(exchange_id=config.exchange_id, symbol=config.symbol, mode=mode)


def test_real_same_fold_artifact_load_and_inference(trained_run):
    output, frame = trained_run
    manifest = _manifest(output)
    scaler = json.loads((output / "fold_0/scaler.json").read_text())
    policy = FrozenPpoPolicy(output, fold=0, seed=7)
    np.testing.assert_array_equal(policy.mean, scaler["mean"])
    np.testing.assert_array_equal(policy.scale, scaler["scale"])
    assert policy.config.lookback == 4
    assert policy.synthetic is True
    assert policy.market_identity == MARKET
    assert policy.bar_seconds == 4 * 3600
    assert policy.model.device.type == "cpu"
    assert manifest["folds"][0]["trained_timesteps"]["7"] == 128
    assert manifest["evidence_state"] == "ENGINEERING_SMOKE"
    assert manifest["scientific_decision"] == "NOT_EVALUATED"
    for name in SEMANTIC_SOURCES:
        source = Path(__file__).resolve().parents[1] / "research_bot" / name
        assert manifest["source"]["file_sha256"][f"research_bot/{name}"] == hashlib.sha256(source.read_bytes()).hexdigest()
    expected_fingerprint = hashlib.sha256(
        (output / "manifest.json").read_bytes()
        + (output / "fold_0/ppo_seed_7.zip").read_bytes()
        + (output / "fold_0/scaler.json").read_bytes()
    ).hexdigest()
    assert policy.fingerprint == expected_fingerprint
    observation = policy.observation(_history(frame.iloc[:250]), cash=1000.0,
                                     quantity=0.0, initial_equity=1000.0, model_peak=1000.0)
    assert observation.shape == (4, len(FEATURE_COLUMNS) + 4)
    assert observation.dtype == np.float32
    action = policy.predict(observation)
    assert isinstance(action, int) and action in {0, 1, 2}


def test_policy_observation_exactly_matches_offline_after_buy_hold_and_sell(trained_run):
    output, frame = trained_run
    policy = FrozenPpoPolicy(output, fold=0, seed=7)
    featured, columns = extract_all_features(frame)
    first = int(np.flatnonzero(featured[columns].notna().all(axis=1).to_numpy())[0])
    usable = featured.iloc[first:].reset_index(drop=True)
    scaler = TrainOnlyScaler(columns).fit(usable.iloc[:120])
    env = EnsembleTradingEnv(scaler.transform(usable), columns, policy.config, start_index=100)
    observation, _ = env.reset(seed=7)

    def assert_same_observation():
        prefix = frame.iloc[:first + env.step_idx + 1]
        actual = policy.observation(_history(prefix), cash=env.cash, quantity=env.quantity,
            initial_equity=env.initial_balance, model_peak=env.peak_value)
        np.testing.assert_array_equal(actual, observation)
        assert np.isfinite(actual).all()

    assert_same_observation()
    for action in (1, 0, 2):
        observation, _, ended, truncated, _ = env.step(action)
        assert not ended and not truncated
        if action == 1:
            assert env.quantity > 0
        elif action == 2:
            assert env.quantity == 0
        assert_same_observation()
    env.close()


def test_insufficient_indicator_warmup_fails_closed(trained_run):
    output, frame = trained_run
    policy = FrozenPpoPolicy(output, fold=0, seed=7)
    with pytest.raises(SafetyStop, match="FEATURE_HISTORY_NOT_READY"):
        policy.observation(_history(frame.iloc[:77]), cash=1000.0, quantity=0.0,
                           initial_equity=1000.0, model_peak=1000.0)


@pytest.mark.parametrize("relative", ["fold_0/ppo_seed_7.zip", "fold_0/scaler.json"])
def test_corrupt_artifact_checksum_is_rejected_before_model_load(artifact, monkeypatch, relative):
    from stable_baselines3 import PPO
    called = []
    monkeypatch.setattr(PPO, "load", lambda *args, **kwargs: called.append(True))
    path = artifact / relative
    path.write_bytes(path.read_bytes() + b"corrupted")
    with pytest.raises(ValueError, match="checksum"):
        FrozenPpoPolicy(artifact, fold=0, seed=7)
    assert called == []


def test_verified_artifact_snapshot_survives_files_changed_during_model_load(artifact, monkeypatch, tmp_path):
    from stable_baselines3 import PPO
    model_path = artifact / "fold_0/ppo_seed_7.zip"
    scaler_path = artifact / "fold_0/scaler.json"
    verified_model_bytes = model_path.read_bytes()
    verified_scaler_bytes = scaler_path.read_bytes()
    expected_fingerprint = hashlib.sha256(
        (artifact / "manifest.json").read_bytes() + verified_model_bytes + verified_scaler_bytes
    ).hexdigest()
    verified_scaler = json.loads(verified_scaler_bytes)
    original_load = PPO.load
    changed_model = original_load(model_path, device="cpu")
    verified_parameter = next(changed_model.policy.parameters()).detach().clone()
    with torch.no_grad():
        next(changed_model.policy.parameters()).add_(0.5)
    replacement_path = tmp_path / "changed_weights.zip"
    changed_model.save(replacement_path)
    replacement_bytes = replacement_path.read_bytes()
    changed_scaler = {**verified_scaler, "mean": [value + 100.0 for value in verified_scaler["mean"]]}
    invoked = []

    def swap_files_after_checks_then_load(source, **kwargs):
        invoked.append(True)
        model_path.write_bytes(replacement_bytes)
        scaler_path.write_text(json.dumps(changed_scaler))
        return original_load(source, **kwargs)

    monkeypatch.setattr(PPO, "load", swap_files_after_checks_then_load)
    policy = FrozenPpoPolicy(artifact, fold=0, seed=7)
    assert invoked == [True]
    assert model_path.read_bytes() != verified_model_bytes
    assert scaler_path.read_bytes() != verified_scaler_bytes
    torch.testing.assert_close(next(policy.model.policy.parameters()), verified_parameter)
    np.testing.assert_array_equal(policy.mean, verified_scaler["mean"])
    assert policy.fingerprint == expected_fingerprint


@pytest.mark.parametrize("missing", ["status", "synthetic", "feature_columns", "source"])
def test_required_manifest_metadata_cannot_be_omitted(artifact, missing):
    manifest = _manifest(artifact)
    del manifest[missing]
    _write_manifest(artifact, manifest)
    with pytest.raises(ValueError):
        FrozenPpoPolicy(artifact, fold=0, seed=7)


@pytest.mark.parametrize("source", SEMANTIC_SOURCES)
@pytest.mark.parametrize("change", ["missing", "changed"])
def test_training_source_semantics_must_match_runtime(artifact, source, change):
    manifest = _manifest(artifact)
    name = f"research_bot/{source}"
    if change == "missing":
        del manifest["source"]["file_sha256"][name]
    else:
        manifest["source"]["file_sha256"][name] = "0" * 64
    _write_manifest(artifact, manifest)
    with pytest.raises(ValueError, match="source|semantics"):
        FrozenPpoPolicy(artifact, fold=0, seed=7)


def test_manifest_feature_order_cannot_change(artifact):
    manifest = _manifest(artifact)
    manifest["feature_columns"] = list(reversed(manifest["feature_columns"]))
    _write_manifest(artifact, manifest)
    with pytest.raises(ValueError, match="schema"):
        FrozenPpoPolicy(artifact, fold=0, seed=7)


def test_scaler_feature_order_rejected_even_with_matching_checksum(artifact):
    _rewrite_scaler_with_matching_checksum(artifact, lambda scaler: scaler.update(columns=list(reversed(scaler["columns"]))))
    with pytest.raises(ValueError, match="scaler"):
        FrozenPpoPolicy(artifact, fold=0, seed=7)


@pytest.mark.parametrize("field,bad_value", [("scale", 0.0), ("scale", -1.0), ("scale", float("inf")), ("mean", float("nan"))])
def test_nonfinite_or_nonpositive_scaler_values_rejected(artifact, field, bad_value):
    def mutate(scaler):
        scaler[field][0] = bad_value
    _rewrite_scaler_with_matching_checksum(artifact, mutate)
    with pytest.raises(ValueError, match="scaler"):
        FrozenPpoPolicy(artifact, fold=0, seed=7)


@pytest.mark.parametrize("field", ["mean", "scale"])
def test_scaler_dimensions_must_match_feature_schema(artifact, field):
    _rewrite_scaler_with_matching_checksum(artifact, lambda scaler: scaler[field].pop())
    with pytest.raises(ValueError, match="scaler"):
        FrozenPpoPolicy(artifact, fold=0, seed=7)


def test_model_observation_space_must_match_manifest_lookback(artifact):
    manifest = _manifest(artifact)
    manifest["environment"]["lookback"] += 1
    _write_manifest(artifact, manifest)
    with pytest.raises(ValueError, match="spaces|observation"):
        FrozenPpoPolicy(artifact, fold=0, seed=7)


def test_symlinked_model_outside_artifact_directory_is_rejected(artifact, tmp_path):
    model_path = artifact / "fold_0/ppo_seed_7.zip"
    outside = tmp_path / "outside.zip"
    shutil.copyfile(model_path, outside)
    model_path.unlink()
    model_path.symlink_to(outside)
    with pytest.raises(ValueError, match="escaped"):
        FrozenPpoPolicy(artifact, fold=0, seed=7)


@pytest.mark.parametrize("fold,seed", [(True, 7), (0, True), (-1, 7), (0, -1)])
def test_fold_and_seed_must_be_explicit_nonnegative_integers(trained_run, fold, seed):
    output, _ = trained_run
    with pytest.raises(ValueError):
        FrozenPpoPolicy(output, fold=fold, seed=seed)


def test_runtime_timeframe_mismatch_rejects_before_any_requests(trained_run):
    output, _ = trained_run
    policy = FrozenPpoPolicy(output, fold=0, seed=7)
    config = _live_config(timeframe="1h")
    with pytest.raises(SafetyStop, match="MODEL_TIMEFRAME_MISMATCH"):
        LiveController(_exchange_identity(config, "dry-run"), object(), policy, config, mode="dry-run")


def test_actual_synthetic_policy_cannot_enable_live_mode(trained_run):
    output, _ = trained_run
    policy = FrozenPpoPolicy(output, fold=0, seed=7)
    config = _live_config()
    with pytest.raises(SafetyStop, match="SYNTHETIC_MODEL_CANNOT_TRADE_LIVE"):
        LiveController(_exchange_identity(config, "live"), object(), policy, config, mode="live")


@pytest.mark.parametrize("changed", [{"exchange_id": "binance"}, {"symbol": "ETH/USDT"}])
def test_nonsynthetic_metadata_fixture_must_match_live_market(artifact, changed):
    # This modified engineering fixture exercises only a constructor guard;
    # no controller cycle is run and it is not promoted to market evidence.
    manifest = _manifest(artifact)
    manifest["synthetic"] = False
    manifest["market_identity"] = {**MARKET, **changed}
    _write_manifest(artifact, manifest)
    policy = FrozenPpoPolicy(artifact, fold=0, seed=7)
    config = _live_config()
    with pytest.raises(SafetyStop, match="MODEL_MARKET_IDENTITY_MISMATCH"):
        LiveController(_exchange_identity(config, "live"), object(), policy, config, mode="live")


@pytest.mark.parametrize("fields", [["--market-exchange", "coinex"], ["--market-symbol", "BTC/USDT"], ["--market-timeframe", "4h"], ["--market-exchange", "coinex", "--market-symbol", "BTC/USDT"]])
def test_training_cli_requires_market_metadata_triplet(tmp_path, capsys, fields):
    output = tmp_path / "must-not-exist"
    with pytest.raises(SystemExit) as error:
        experiment_main(["--synthetic-bars", "350", "--output", str(output), *fields])
    assert error.value.code == 2
    assert "all three" in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize("identity", [{"exchange_id": "coinex"}, {**MARKET, "symbol": ""}, {**MARKET, "timeframe": "1h"}])
def test_invalid_training_market_identity_is_rejected_before_output(tmp_path, identity):
    output = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match="market|identity"):
        run_experiment(synthetic_ohlcv(350), output, data_source="engineering rejection fixture",
            synthetic=True, walk_forward=WalkForwardConfig(120, 30, 1, 1),
            env_config=TradingEnvConfig(lookback=4), timesteps=128, seeds=(7,),
            market_identity=identity)
    assert not output.exists()
