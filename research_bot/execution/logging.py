"""JSON event logging with a closed field contract and no free-text exceptions."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
import sys


_EVENTS = frozenset({"FOUNDATION_INITIALIZED", "FOUNDATION_REJECTED"})
_FIELDS = frozenset({"stage", "mode", "config_sha256", "error_type", "reason"})


class EventFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Ignore record.msg, args, exception text, stack info and arbitrary extras.
        event = getattr(record, "event_code", None)
        fields = getattr(record, "event_fields", {})
        if not isinstance(event, str) or event not in _EVENTS or not isinstance(fields, dict) or set(fields) - _FIELDS:
            event, fields = "FOUNDATION_REJECTED", {"reason": "INVALID_LOG_EVENT"}
        cleaned = {}
        for key, value in fields.items():
            if key == "stage" and type(value) is int and value == 0:
                cleaned[key] = value
            elif key == "mode" and isinstance(value, str) and value in {"BACKTEST", "PAPER", "LIVE_MICRO", "LIVE_FULL"}:
                cleaned[key] = value
            elif key == "config_sha256" and isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value):
                cleaned[key] = value
            elif key == "error_type" and isinstance(value, str) and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,63}", value):
                cleaned[key] = value
            elif key == "reason" and isinstance(value, str) and value in {
                "INITIALIZATION_ONLY", "INVALID_LOG_EVENT", "INVALID_CONFIG_SCHEMA",
                "INVALID_DEPLOYMENT_MODE", "INVALID_LIVE_CONFIG", "GOVERNANCE_LIMIT_OUT_OF_RANGE",
                "INVALID_FULL_ALLOCATION", "LIVE_LIMIT_EXCEEDS_GOVERNANCE", "CAPITAL_EXCEEDS_FULL_ALLOCATION",
                "LIVE_CONFIGURATION_INCOMPLETE", "MICRO_CAPITAL_EXCEEDS_FIVE_PERCENT",
                "DUPLICATE_CONFIG_KEY", "NONFINITE_CONFIG_VALUE", "CONFIG_OBJECT_REQUIRED",
                "FOUNDATION_FAILURE", "PYTHON_311_REQUIRED", "LEDGER_STATE_INVALID",
            }:
                cleaned[key] = value
        level = record.levelname if record.levelname in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"} else "INFO"
        return json.dumps({"timestamp_utc": datetime.now(timezone.utc).isoformat(),
                           "level": level, "event": event, **cleaned}, allow_nan=False)


def event_logger(stream=None) -> logging.Logger:
    # A local logger avoids changing the application's root logger or handlers.
    logger = logging.Logger("master_v3.foundation", level=logging.INFO)
    handler = logging.StreamHandler(sys.stdout if stream is None else stream)
    handler.setFormatter(EventFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def log_event(logger: logging.Logger, event: str, **fields) -> None:
    logger.info("", extra={"event_code": event, "event_fields": fields})
