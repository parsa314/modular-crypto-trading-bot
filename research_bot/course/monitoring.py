"""Public, read-only projection of an explicitly selected research report.

No strategies, exchange clients, credentials, journals or execution ports are
imported. Unknown text/fields never cross the public boundary.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_REPORT_BYTES = 4 * 1024 * 1024
MAX_REPORT_AGE_SECONDS = 24 * 60 * 60
PANELS = (
    "system_health", "data_freshness", "active_hypotheses", "strategy_status",
    "market_regime", "order_book_metrics", "portfolio_risk", "risk_gate_decisions",
    "simulated_orders", "grid_state", "arbitrage_opportunities", "experiment_registry",
    "model_calibration", "evidence_ledger", "scientific_validation",
)
LABELS = frozenset({
    "NOT_RUN", "NOT_EVALUATED", "DATA_UNAVAILABLE", "VALID", "INVALID", "STALE",
    "ERROR", "BLOCKED", "INTEGRATION_BLOCKED", "IMPLEMENTED_TESTED",
    "RESEARCH_CANDIDATE", "SCIENTIFIC_DECISION_DEFERRED", "UNVERIFIED",
    "ENGINEERING_TESTED", "ENGINEERING_REPLAY", "SYNTHETIC_ENGINEERING_FIXTURE",
    "NO_TRADE", "REJECTED", "NO_ORDER", "RESEARCH_ONLY", "RANGE", "TREND",
    "VOLATILE", "UNKNOWN", "INITIALIZED", "READY", "RESERVED", "ACTIVE",
    "PARTIALLY_FILLED", "REBALANCING", "COMPLETED", "CANCELLED", "HALTED",
    "PASS", "FAIL", "DENIED_RESEARCH_ONLY", "SIMULATED", "INSUFFICIENT_DATA",
    "INTEGRATED", "EVIDENCE_VERIFIED", "IMPLEMENTED", "TESTED",
})
STRATEGIES = frozenset({
    "sma_crossover", "bollinger_reversion", "rsi_reversal", "donchian_breakout",
    "macd_crossover", "technical_confluence", "simple_scalper",
    "order_book_imbalance", "rsi_moving_average", "grid", "grid_trading",
    "triangular_arbitrage",
})
NUMERIC_FIELDS = frozenset({
    "hypotheses", "no_trade", "simulated_fills", "engineered_checks", "trade_count",
    "event_count", "strategy_count", "experiment_count", "rejection_count", "order_count", "fill_count",
    "spread_bps", "relative_spread", "imbalance", "quote_age_seconds",
    "market_data_age_seconds", "gross_exposure", "net_exposure", "asset_weight",
    "drawdown", "cvar_95", "turnover", "brier", "average_uniqueness", "pbo", "dsr",
    "net_return", "max_drawdown", "cost_bps", "partial_fill_count", "opportunity_count",
})


def _label(value: Any) -> str:
    return value if isinstance(value, str) and value in LABELS else "NOT_EVALUATED"


def _metrics(value: Any) -> dict[str, float | int]:
    if not isinstance(value, dict):
        return {}
    return {
        key: number for key, number in value.items()
        if key in NUMERIC_FIELDS and isinstance(number, (int, float))
        and not isinstance(number, bool) and math.isfinite(number) and abs(number) <= 1e15
    }


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_REPORT_FIELD")
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise ValueError("NONFINITE_REPORT_VALUE")


def course_monitoring_summary(*, now: datetime | None = None) -> dict[str, Any]:
    """Read a bounded report; malformed/missing evidence yields public labels.

    Report age describes the artifact, not the freshness of underlying market
    data. Failure never restarts a process or changes execution authorization.
    """
    result: dict[str, Any] = {
        "schema": "COURSE_RESEARCH_MONITORING_V1", "status": "NOT_RUN",
        "scientific_status": "NOT_EVALUATED", "data_class": "NOT_EVALUATED",
        "execution_flags": {"research_only": True, "paper_execution": False, "live_execution": False},
        "execution_authorization": "DENIED_RESEARCH_ONLY", "report_freshness": "NOT_EVALUATED",
        "generated_at": None, "report_age_seconds": None, "strategies": [], "metrics": {},
        "panels": {name: {"status": "NOT_EVALUATED", "metrics": {}} for name in PANELS},
    }
    configured = os.getenv("COURSE_RESEARCH_REPORT")
    if not configured:
        return result
    try:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("NAIVE_MONITORING_TIME")
        with Path(configured).open("rb") as stream:
            raw = stream.read(MAX_REPORT_BYTES + 1)
        if len(raw) > MAX_REPORT_BYTES:
            raise ValueError("REPORT_TOO_LARGE")
        report = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
        if not isinstance(report, dict) or report.get("schema") != "COURSE_RESEARCH_REPORT_V1":
            raise ValueError("INVALID_REPORT_SCHEMA")
        flags = report.get("execution_flags")
        if (not isinstance(flags, dict) or flags.get("research_only") is not True
                or flags.get("paper_execution") is not False or flags.get("live_execution") is not False):
            result["status"] = "BLOCKED"
            result["panels"]["system_health"]["status"] = "BLOCKED"
            return result
        stamp = datetime.fromisoformat(report["generated_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("NAIVE_REPORT_TIME")
        age = (current - stamp).total_seconds()
        if age < -5:
            raise ValueError("FUTURE_REPORT_TIME")
        result.update(
            status=_label(report.get("status")), scientific_status=_label(report.get("scientific_status")),
            data_class=_label(report.get("data_class")), generated_at=stamp.astimezone(timezone.utc).isoformat(),
            report_age_seconds=max(0.0, age),
            report_freshness="STALE" if age >= MAX_REPORT_AGE_SECONDS else "VALID",
            metrics=_metrics(report.get("metrics")),
        )
        strategies = report.get("strategies", [])
        if not isinstance(strategies, list):
            raise TypeError("INVALID_STRATEGY_LIST")
        result["strategies"] = [
            {"strategy_id": item["strategy_id"], "status": _label(item.get("status"))}
            for item in strategies[:100] if isinstance(item, dict)
            and isinstance(item.get("strategy_id"), str) and item["strategy_id"] in STRATEGIES
        ]
        panels = report.get("panels", {})
        if isinstance(panels, dict):
            for name in PANELS:
                item = panels.get(name)
                if isinstance(item, dict):
                    result["panels"][name] = {"status": _label(item.get("status")),
                                              "metrics": _metrics(item.get("metrics"))}
        result["panels"]["system_health"]["status"] = result["report_freshness"]
        result["panels"]["strategy_status"] = {"status": "VALID", "metrics": {"strategy_count": len(result["strategies"])}}
        result["panels"]["scientific_validation"]["status"] = result["scientific_status"]
        return result
    except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError, AttributeError):
        # Do not expose file paths, raw exception text or any parsed fragments.
        result.update(status="ERROR", scientific_status="NOT_EVALUATED", data_class="NOT_EVALUATED",
                      generated_at=None, report_age_seconds=None, report_freshness="ERROR", strategies=[], metrics={})
        result["panels"] = {name: {"status": "NOT_EVALUATED", "metrics": {}} for name in PANELS}
        result["panels"]["system_health"]["status"] = "ERROR"
        return result
