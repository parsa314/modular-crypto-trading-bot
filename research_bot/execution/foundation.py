"""Initialize MASTER v3 Stage 0 without importing models or contacting exchanges."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import sys
import tempfile

from .config import ConfigError, DeploymentMode, GovernanceConfig
from .ledger import LiveLedger
from .logging import event_logger, log_event


STAGES = (
    "FOUNDATION_GOVERNANCE", "DATA_LAYER", "FEATURE_PIPELINE", "CLASSICAL_ML",
    "DEEP_LEARNING", "REINFORCEMENT_LEARNING", "ENSEMBLE_REGIMES", "BACKTEST",
    "RISK_ENGINE", "PAPER_TRADING", "LIVE_MICRO_SCALING", "INFRASTRUCTURE",
    "IRAN_ADAPTATION", "THESIS_EXTRACTION", "DEFENSE_HANDOVER",
)


def source_fingerprint() -> dict:
    root = Path(__file__).resolve().parents[1]
    paths = [root / "execution" / name for name in ("__init__.py", "config.py", "ledger.py", "logging.py", "foundation.py")]
    paths.append(root / "live_runtime.py")
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def initialize(config: GovernanceConfig, ledger_path: str | Path) -> dict:
    if sys.version_info < (3, 11):
        raise ConfigError("PYTHON_311_REQUIRED")
    if config.mode != DeploymentMode.BACKTEST:
        raise ConfigError("INITIALIZATION_ONLY")
    source = source_fingerprint()
    identity = {"purpose": "MASTER_V3_STAGE0_V1", "config_sha256": config.sha256, "source_sha256": source}
    with LiveLedger(ledger_path, identity) as ledger, ledger.session_lock():
        state = ledger.get_state()
        if not state:
            state = {
                "protocol": "MASTER_V3", "current_stage": 0,
                "initialized_at_utc": datetime.now(timezone.utc).isoformat(),
                "stages": [{"stage": i, "name": name,
                            "status": "AWAITING_USER_REVIEW" if i == 0 else "PENDING"}
                           for i, name in enumerate(STAGES)],
                "metrics_status": "NOT_EVALUATED", "execution_authorized": False,
                "operating_constraints_status": "AWAITING_USER_INPUT",
            }
            ledger.set_state(state)
        if (state.get("protocol") != "MASTER_V3" or state.get("current_stage") != 0
                or state.get("execution_authorized") is not False
                or state.get("stages", []) != [{"stage": i, "name": name,
                    "status": "AWAITING_USER_REVIEW" if i == 0 else "PENDING"}
                    for i, name in enumerate(STAGES)]
                or state.get("metrics_status") != "NOT_EVALUATED"
                or state.get("operating_constraints_status") != "AWAITING_USER_INPUT"
                or not isinstance(state.get("initialized_at_utc"), str)):
            raise ConfigError("LEDGER_STATE_INVALID")
        # Repair a crash between state persistence and the audit-event commit.
        # The stable event ID and identity make repeated claims idempotent.
        ledger.claim_decision("MASTER_V3_STAGE0_INITIALIZATION", identity)
    return {
        "schema_version": 1, "stage": 0, "status": "AWAITING_USER_REVIEW",
        "mode": config.mode.value, "config_valid": True, "ledger_initialized": True,
        "config_sha256": config.sha256, "source_sha256": source,
        "python_version": platform.python_version(), "governance": config.public_dict(),
        "execution_authorized": False, "metrics_status": "NOT_EVALUATED", "ledger": state,
    }


def write_report(path: Path, report: dict) -> None:
    """Atomically replace a public JSON report with mode 0600 on a local filesystem."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".stage0-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/stage0.example.json"))
    parser.add_argument("--ledger", type=Path, default=Path("artifacts/stage0/ledger.sqlite"))
    parser.add_argument("--report", type=Path, default=Path("artifacts/stage0/report.json"))
    args = parser.parse_args(argv)
    logger = event_logger()
    try:
        ledger_path, report_path, config_path = (path.resolve() for path in (args.ledger, args.report, args.config))
        reserved = {ledger_path, Path(str(ledger_path) + "-wal"), Path(str(ledger_path) + "-shm")}
        if report_path in reserved or config_path in reserved or report_path == config_path:
            raise ConfigError("INVALID_CONFIG_SCHEMA")
        config = GovernanceConfig.from_json(args.config)
        report = initialize(config, args.ledger)
        write_report(args.report, report)
        log_event(logger, "FOUNDATION_INITIALIZED", stage=0, mode=config.mode.value, config_sha256=config.sha256)
        return 0
    except (ValueError, TypeError, OSError, RuntimeError, sqlite3.Error) as exc:
        log_event(logger, "FOUNDATION_REJECTED", stage=0, error_type=type(exc).__name__,
                  reason=str(exc) if isinstance(exc, ConfigError) else "FOUNDATION_FAILURE")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
