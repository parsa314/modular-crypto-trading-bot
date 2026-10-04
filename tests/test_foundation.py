from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import hashlib
import io
import json
import logging
from pathlib import Path
import stat
import subprocess
import sys

import pytest

from research_bot.execution.config import ConfigError, DeploymentMode, GovernanceConfig, LiveConfig
from research_bot.execution.foundation import initialize, main
from research_bot.execution.ledger import LiveLedger
from research_bot.execution.logging import EventFormatter, event_logger, log_event


ROOT = Path(__file__).resolve().parents[1]


def live_config(**overrides):
    return LiveConfig(**{"exchange_id": "coinex", "symbol": "BTC/USDT", "timeframe": "1h",
                         "capital_limit_quote": 50., "max_order_quote": 10.,
                         "max_daily_loss": .02, "max_drawdown": .10, **overrides})


def test_l0_is_frozen_and_public_snapshot_is_detached():
    config = GovernanceConfig(live=live_config())
    original = config.sha256
    with pytest.raises(FrozenInstanceError):
        config.max_daily_loss_fraction = .50
    with pytest.raises(FrozenInstanceError):
        config.live.max_daily_loss = .50
    snapshot = config.public_dict()
    snapshot["live"]["capital_limit_quote"] = 999
    assert config.sha256 == original
    assert config.live.capital_limit_quote == 50
    assert replace(config, max_daily_loss_fraction=.01, live=None).sha256 != original


@pytest.mark.parametrize("name, value", [
    ("max_daily_loss_fraction", True), ("max_daily_loss_fraction", float("nan")),
    ("max_daily_loss_fraction", float("inf")), ("max_daily_loss_fraction", .02001),
    ("max_daily_loss_fraction", 0), ("max_drawdown_fraction", .10001),
    ("max_drawdown_fraction", -1), ("max_drawdown_fraction", "0.1"),
    ("full_allocation_quote", True), ("full_allocation_quote", float("inf")),
    ("full_allocation_quote", 0), ("mode", "LIVE"), ("mode", None), ("live", {}),
])
def test_invalid_l0_limits_fail_closed(name, value):
    with pytest.raises(ConfigError):
        GovernanceConfig(**{name: value})


@pytest.mark.parametrize("mode", [DeploymentMode.LIVE_MICRO, DeploymentMode.LIVE_FULL])
def test_live_config_requires_selected_limits_and_capital(mode):
    with pytest.raises(ConfigError, match="INCOMPLETE"):
        GovernanceConfig(mode=mode)
    with pytest.raises(ConfigError, match="INCOMPLETE"):
        GovernanceConfig(mode=mode, live=live_config())


def test_micro_budget_boundary_and_hard_risk_ceiling():
    config = GovernanceConfig(mode="LIVE_MICRO", live=live_config(), full_allocation_quote=1000)
    assert config.live.capital_limit_quote == .05 * config.full_allocation_quote
    with pytest.raises(ConfigError, match="FIVE_PERCENT"):
        replace(config, live=live_config(capital_limit_quote=50.01))
    with pytest.raises(ConfigError, match="GOVERNANCE"):
        GovernanceConfig(live=live_config(max_daily_loss=.03))
    with pytest.raises(ConfigError, match="ALLOCATION"):
        GovernanceConfig(mode="LIVE_FULL", live=live_config(), full_allocation_quote=49)


@pytest.mark.parametrize("payload", [
    '{"mode":"BACKTEST","mode":"LIVE_FULL"}', '{"max_daily_loss_fraction":NaN}',
    '{"full_allocation_quote":Infinity}', '{"api_key":"NEVER_PERSIST_ME"}',
    '{"max_daily_loss_percent":2}', '[]', '{"live":[]}',
    '{"live":{"api_secret":"NEVER_PERSIST_ME"}}',
    '{"live":{"symbol":"BTC/USDT","symbol":"ETH/USDT"}}',
])
def test_strict_json_rejects_ambiguous_or_credential_config(tmp_path, payload):
    path = tmp_path / "config.json"
    path.write_text(payload)
    with pytest.raises(ConfigError) as exc:
        GovernanceConfig.from_json(path)
    assert "NEVER_PERSIST_ME" not in str(exc.value)


def test_example_validates_without_inventing_operating_constraints():
    config = GovernanceConfig.from_json(ROOT / "configs/stage0.example.json")
    assert config.mode == DeploymentMode.BACKTEST
    assert config.live is None and config.full_allocation_quote is None


def test_stage0_initialization_is_idempotent_pinned_and_has_no_promotion(tmp_path):
    path = tmp_path / "ledger.sqlite"
    config = GovernanceConfig()
    report = initialize(config, path)
    assert initialize(config, path) == report
    assert report["execution_authorized"] is False
    assert report["metrics_status"] == "NOT_EVALUATED"
    assert len(report["ledger"]["stages"]) == 15
    assert report["ledger"]["stages"][0]["status"] == "AWAITING_USER_REVIEW"
    assert all(stage["status"] == "PENDING" for stage in report["ledger"]["stages"][1:])
    for relative, digest in report["source_sha256"].items():
        assert digest == hashlib.sha256((ROOT / "research_bot" / relative).read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        initialize(replace(config, max_daily_loss_fraction=.01), path)


@pytest.mark.parametrize("mode", ["PAPER", "LIVE_MICRO", "LIVE_FULL"])
def test_foundation_cannot_start_later_modes_even_with_valid_config(tmp_path, mode):
    config = GovernanceConfig(mode=mode, live=live_config(), full_allocation_quote=1000)
    with pytest.raises(ConfigError, match="INITIALIZATION_ONLY"):
        initialize(config, tmp_path / "never.sqlite")
    assert not (tmp_path / "never.sqlite").exists()


def test_forged_stage_completion_cannot_advance_foundation(tmp_path):
    path = tmp_path / "ledger.sqlite"
    report = initialize(GovernanceConfig(), path)
    identity = {"purpose": "MASTER_V3_STAGE0_V1", "config_sha256": report["config_sha256"],
                "source_sha256": report["source_sha256"]}
    with LiveLedger(path, identity) as ledger:
        state = ledger.get_state()
        state["stages"][0]["status"] = "PASSED"
        ledger.set_state(state)
    with pytest.raises(ConfigError, match="LEDGER_STATE_INVALID"):
        initialize(GovernanceConfig(), path)


def test_restart_repairs_interruption_before_initialization_event(tmp_path, monkeypatch):
    path = tmp_path / "ledger.sqlite"
    config = GovernanceConfig()
    def interrupted(*args, **kwargs):
        raise OSError("simulated interruption before event commit")
    with monkeypatch.context() as patch:
        patch.setattr(LiveLedger, "claim_decision", interrupted)
        with pytest.raises(OSError):
            initialize(config, path)
    report = initialize(config, path)
    identity = {"purpose": "MASTER_V3_STAGE0_V1", "config_sha256": report["config_sha256"],
                "source_sha256": report["source_sha256"]}
    with LiveLedger(path, identity) as ledger:
        assert ledger.claim_decision("MASTER_V3_STAGE0_INITIALIZATION", identity) is False


def test_logging_never_emits_raw_message_exception_url_or_arbitrary_fields():
    stream = io.StringIO()
    logger = event_logger(stream)
    try:
        raise RuntimeError("https://example.test?apiKey=NEVER_LOG_ME&signature=NEVER_LOG_ME")
    except RuntimeError:
        logger.exception("NEVER_LOG_ME", extra={"event_code": "FOUNDATION_REJECTED",
                                                "event_fields": {"stage": 0, "error_type": "RuntimeError"}})
    log_event(logger, "FOUNDATION_INITIALIZED", api_key="NEVER_LOG_ME")
    assert "NEVER_LOG_ME" not in stream.getvalue()
    assert "example.test" not in stream.getvalue()
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert events[0]["error_type"] == "RuntimeError"
    assert events[1]["reason"] == "INVALID_LOG_EVENT"


@pytest.mark.parametrize("fields", [{"mode": []}, {"reason": {}}, {"config_sha256": float("nan")}])
def test_malformed_log_fields_do_not_crash_formatter(fields):
    record = logging.LogRecord("test", logging.INFO, "", 0, "private", (), None)
    record.event_code = "FOUNDATION_INITIALIZED"
    record.event_fields = fields
    assert "private" not in EventFormatter().format(record)


def test_execution_foundation_imports_without_site_packages_or_research():
    script = """
import sys
from research_bot.execution.foundation import initialize
from research_bot.execution.config import GovernanceConfig
blocked = ('numpy', 'pandas', 'ccxt', 'torch', 'stable_baselines3',
           'research_bot.research', 'research_bot.ensemble_', 'research_bot.live_runtime',
           'research_bot.contracts', 'research_bot.execution.legacy')
assert not any(name.startswith(blocked) for name in sys.modules)
assert GovernanceConfig().mode.value == 'BACKTEST'
"""
    result = subprocess.run([sys.executable, "-S", "-c", script], cwd=ROOT,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_legacy_paper_api_survives_module_to_package_conversion():
    from research_bot.execution import OrderSide, PaperExecutionEngine, ExecutionMode
    from research_bot.execution.legacy import OrderSide as LegacySide
    assert OrderSide is LegacySide
    assert PaperExecutionEngine().policy.mode == ExecutionMode.PAPER


def test_cli_writes_public_report_and_safe_json_log(tmp_path, capsys):
    path = tmp_path / "report.json"
    args = ["--config", str(ROOT / "configs/stage0.example.json"),
            "--ledger", str(tmp_path / "ledger.sqlite"), "--report", str(path)]
    assert main(args) == 0
    event = json.loads(capsys.readouterr().out)
    assert event["event"] == "FOUNDATION_INITIALIZED"
    report = json.loads(path.read_text())
    assert report["config_valid"] and report["ledger_initialized"]
    assert report["execution_authorized"] is False
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert main(args) == 0


def test_cli_rejects_secrets_without_printing_or_persisting_them(tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text('{"api_key":"NEVER_LOG_ME"}')
    assert main(["--config", str(path), "--ledger", str(tmp_path / "never.sqlite"),
                 "--report", str(tmp_path / "never.json")]) == 2
    captured = capsys.readouterr()
    assert "NEVER_LOG_ME" not in captured.out + captured.err
    assert json.loads(captured.out)["event"] == "FOUNDATION_REJECTED"
    assert not (tmp_path / "never.sqlite").exists()
    assert not (tmp_path / "never.json").exists()


@pytest.mark.parametrize("collision", ["config", "ledger", "wal", "shm"])
def test_cli_cannot_replace_config_or_ledger_with_report(tmp_path, capsys, collision):
    config = tmp_path / "config.json"
    config.write_text('{}')
    ledger = tmp_path / "ledger.sqlite"
    report = {"config": config, "ledger": ledger, "wal": Path(str(ledger) + "-wal"),
              "shm": Path(str(ledger) + "-shm")}[collision]
    assert main(["--config", str(config), "--ledger", str(ledger), "--report", str(report)]) == 2
    assert config.read_text() == '{}'
    assert not ledger.exists()


@pytest.mark.parametrize("mode", ["live", "testnet"])
def test_existing_operator_cli_blocks_private_modes_before_reading_inputs(tmp_path, capsys, mode):
    from research_bot.live_runtime import main as live_main
    from research_bot.live_runtime import LiveConfig as CompatibilityConfig
    from research_bot.live_ledger import LiveLedger as CompatibilityLedger
    assert CompatibilityConfig is LiveConfig
    assert CompatibilityLedger is LiveLedger
    missing = str(tmp_path / "does-not-exist")
    with pytest.raises(SystemExit) as exc:
        live_main(["--config", missing, "--run-dir", missing, "--fold", "0", "--seed", "42",
                   "--journal", missing, "--mode", mode, "--once"])
    assert exc.value.code == 2
    assert json.loads(capsys.readouterr().out)["status"] == "BLOCKED_BY_MASTER_V3_GATE"
