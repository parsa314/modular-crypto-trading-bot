import json

import pytest

from research_bot.research import profit_pipeline as pipeline


def test_frozen_trial_rejects_changed_sources(tmp_path, monkeypatch):
    pipeline.freeze(pipeline.CONFIG_PATH, tmp_path)
    monkeypatch.setattr(pipeline, "source_hashes", lambda: {"changed": "0" * 64})
    with pytest.raises(ValueError, match="changed"):
        pipeline.run(pipeline.CONFIG_PATH, tmp_path / "history", tmp_path)


def test_freeze_refuses_overwrite_and_financial_config_override(tmp_path):
    trial = tmp_path / "trial"
    record = pipeline.freeze(pipeline.CONFIG_PATH, trial)
    assert record["planned_folds"] == 29
    assert record["metrics_status"] == "NOT_EVALUATED"
    with pytest.raises(FileExistsError):
        pipeline.freeze(pipeline.CONFIG_PATH, trial)
    config = json.loads(pipeline.CONFIG_PATH.read_text())
    config["execution_authorized"] = True
    changed = tmp_path / "config.json"
    changed.write_text(json.dumps(config))
    with pytest.raises(ValueError):
        pipeline.load_config(changed)


def test_blocked_history_never_invokes_training(tmp_path, monkeypatch):
    trial, history = tmp_path / "trial", tmp_path / "history"
    history.mkdir()
    pipeline.freeze(pipeline.CONFIG_PATH, trial)
    manifest = {"status": "BLOCKED", "symbol": "BTCUSDT", "market": "spot", "timeframe": "1h",
                "start_inclusive": "2022-01-01T00:00:00+00:00", "end_exclusive": "2025-01-01T00:00:00+00:00"}
    (history / "manifest.json").write_text(json.dumps(manifest))
    (history / "quality.json").write_text('{"valid":false}')
    def forbidden(*a, **k):
        pytest.fail("Data quality must veto model training")
    monkeypatch.setattr(pipeline, "walk_forward_predictions", forbidden)
    report = pipeline.run(pipeline.CONFIG_PATH, history, trial)
    assert report["status"] == "BLOCKED_DATA_QUALITY_OR_INTEGRITY"
    assert report["economic_metrics"] is None
    assert report["execution_authorized"] is False
    with pytest.raises(FileExistsError):
        pipeline.run(pipeline.CONFIG_PATH, history, trial)


def test_undefined_sharpe_and_perfect_win_no_loss_do_not_grant_promotion():
    config = pipeline.load_config(pipeline.CONFIG_PATH)
    metrics = {"sharpe": None, "monthly_geometric_return": .05, "max_drawdown": 0,
               "trade_count": 0, "elapsed_days": 365, "win_rate": None,
               "profit_factor": None, "calmar": None}
    result = pipeline.gate_summary(metrics, metrics, {"ece_10": None}, fitted_folds=29,
                                   expected_folds=29, is_sharpe=None, config=config)
    assert result["historical_economic_checks_passed"] is False
    assert "sharpe" in result["failed_checks"]
    assert result["execution_authorized"] is False
