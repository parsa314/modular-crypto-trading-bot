"""Classify actual phase-one abstentions without inventing forecasts or risk vetoes.

Reasons are audit labels for a confirmed NO_TRADE decision, not replacement
signals or independent authorization. One primary reason per decision makes
the histogram reconcile exactly to the observed NO_TRADE count.
"""
from __future__ import annotations

from typing import Any, Mapping


def phase_one_trace(record: Mapping[str, Any]) -> dict:
    signal = record["signal"]
    metadata = signal["metadata"]
    if signal["decision"] != "NO_TRADE":
        raise ValueError("PHASE_ONE_TRACE_EXPECTS_NO_TRADE")
    if (metadata.get("research_status") != "HYPOTHESIS"
            or metadata.get("forecast_available") is not False
            or metadata.get("execution_authorized") is not False
            or record.get("execution_authorized") is not False):
        raise ValueError("PHASE_ONE_TRACE_SAFETY_CONTRACT_VIOLATED")

    rule_matched = metadata.get("candidate")
    if type(rule_matched) is not bool:
        raise ValueError("PHASE_ONE_CANDIDATE_FLAG_MISSING")
    primary = "HYPOTHESIS_ONLY_NO_FORECAST" if rule_matched else "STRATEGY_RULE_NOT_MATCHED"
    contributing = ["HYPOTHESIS_ONLY_NO_FORECAST", "EXECUTION_NOT_AUTHORIZED"]
    record_status = record.get("pipeline_status")
    if record_status != "RESEARCH_ONLY_UNVALIDATED_HYPOTHESIS":
        raise ValueError("UNEXPECTED_PHASE_ONE_PIPELINE_STATUS")

    risk = record.get("risk")
    # An absent risk evaluation is NOT an approved risk nor an actual veto.
    risk_status = "NOT_EVALUATED" if risk is None else (
        "RISK_VETO" if risk.get("approved") is False else "APPROVED_OBSERVED_ONLY"
    )
    if risk_status == "RISK_VETO":
        contributing.append("RISK_VETO")
    if not rule_matched:
        contributing.insert(0, primary)
    return {
        "primary_reason": primary,
        "contributing_reasons": tuple(dict.fromkeys(contributing)),
        "engine_reasons": tuple(signal.get("reasons", ())),
        "risk_status": risk_status,
        "rule_matched": rule_matched,
        "execution_authorized": False,
    }
