"""Explicit coordination contract between AI forecasts and financial controls.

AI may estimate probabilities and uncertainty. It may not size positions, relax
risk limits, reserve cash, or create orders. The financial layer may veto and
size only within frozen limits. PAPER/LIVE remain disabled.
"""
from __future__ import annotations

from dataclasses import asdict
import math
from typing import Any

import pandas as pd

from .contracts import LIVE_EXECUTION, PAPER_EXECUTION, assert_research_only
from .portfolio import PortfolioConfig


AI_FORBIDDEN_CONTROL_FIELDS = {
    "notional", "quantity", "position_size", "risk_per_trade", "max_asset_weight",
    "max_gross_exposure", "drawdown_kill", "max_cvar95", "leverage", "order_type",
    "paper_execution", "live_execution",
}


def _canonical(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a nonempty canonical string")
    return value


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite numeric data")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite numeric data") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite numeric data")
    return number


def _utc(value: Any, name: str) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError(f"{name} must be timezone-aware UTC")
    return stamp.tz_convert("UTC")


def coordinate_finance_ai_handoff(
    *,
    economics_decision: dict,
    ai_prediction: dict,
    portfolio_config: PortfolioConfig,
) -> dict:
    """Create one auditable AI -> economics -> finance handoff.

    This function never approves an order. It only establishes whether a
    forecast is eligible to reach the independent portfolio-risk engine.
    The portfolio simulator remains responsible for cash, sizing, exposure,
    drawdown and CVaR vetoes.
    """
    assert_research_only()
    if PAPER_EXECUTION or LIVE_EXECUTION:
        raise RuntimeError("execution firewall violation")
    if not isinstance(economics_decision, dict) or not isinstance(ai_prediction, dict):
        raise ValueError("decision and prediction must be dictionaries")
    if not isinstance(portfolio_config, PortfolioConfig):
        raise ValueError("portfolio_config must be PortfolioConfig")
    portfolio_config.__post_init__()

    unexpected = AI_FORBIDDEN_CONTROL_FIELDS.intersection(ai_prediction)
    if unexpected:
        raise PermissionError(f"AI forecast attempted financial/execution control: {sorted(unexpected)}")

    event_id = _canonical(economics_decision.get("event_id"), "event_id")
    if _canonical(ai_prediction.get("event_id"), "prediction.event_id") != event_id:
        raise ValueError("AI and economics event identities must match")
    symbol = _canonical(economics_decision.get("symbol"), "symbol")
    venue = _canonical(economics_decision.get("venue"), "venue")
    if venue != "synthetic":
        raise PermissionError("current finance/AI orchestration is synthetic-engineering only")

    decision_at = _utc(economics_decision.get("decision_at"), "decision_at")
    prediction_at = _utc(ai_prediction.get("decision_at"), "prediction.decision_at")
    if decision_at != prediction_at:
        raise ValueError("AI prediction and economics decision clocks must match")

    probabilities = ai_prediction.get("probabilities")
    if not isinstance(probabilities, dict) or list(probabilities) != ["TP", "SL", "TIMEOUT"]:
        raise ValueError("AI probabilities must use the frozen TP/SL/TIMEOUT axis")
    p = [_finite(probabilities[name], f"probability.{name}") for name in ("TP", "SL", "TIMEOUT")]
    if any(value < 0 or value > 1 for value in p) or not math.isclose(sum(p), 1.0, abs_tol=1e-8):
        raise ValueError("AI probabilities must be finite and sum to one")

    entropy = _finite(ai_prediction.get("entropy"), "entropy")
    shift = _finite(ai_prediction.get("shift_score"), "shift_score")
    if not 0 <= entropy <= 1 or shift < 0:
        raise ValueError("invalid AI uncertainty diagnostics")

    utility = _finite(economics_decision.get("expected_utility"), "expected_utility")
    reason = _canonical(economics_decision.get("admission_reason"), "admission_reason")
    cost_bps = _finite(economics_decision.get("round_trip_cost_bps"), "round_trip_cost_bps")
    if cost_bps < 0 or not math.isclose(cost_bps, portfolio_config.round_trip_cost_bps, abs_tol=1e-12):
        raise ValueError("economic and financial transaction-cost assumptions must match")
    if reason == "ADMITTED" and utility <= 0:
        raise ValueError("an admitted ML economics decision must have positive expected utility")

    if reason == "ADMITTED":
        pre_portfolio_gate = "ELIGIBLE_FOR_FINANCE_RISK_CHECK"
    else:
        pre_portfolio_gate = "ABSTAIN"

    portfolio_intent = dict(economics_decision)
    portfolio_intent["policy"] = "ML_UTILITY"

    return {
        "coordination_schema": "V58_FINANCE_AI_HANDOFF_V1",
        "event_id": event_id,
        "symbol": symbol,
        "decision_at": decision_at.isoformat(),
        "ai": {
            "role": "FORECAST_ONLY",
            "probabilities": {name: p[i] for i, name in enumerate(("TP", "SL", "TIMEOUT"))},
            "entropy": entropy,
            "shift_score": shift,
            "may_size": False,
            "may_change_risk_limits": False,
            "may_create_orders": False,
        },
        "economics": {
            "role": "COST_AND_EXPECTED_UTILITY_GATE",
            "expected_utility": utility,
            "round_trip_cost_bps": cost_bps,
            "gate_reason": reason,
            "may_rescue_ai_abstention": False,
        },
        "finance": {
            "role": "INDEPENDENT_VETO_AND_SIZING",
            "risk_per_trade": portfolio_config.risk_per_trade,
            "max_asset_weight": portfolio_config.max_asset_weight,
            "max_gross_exposure": portfolio_config.max_gross_exposure,
            "drawdown_kill": portfolio_config.drawdown_kill,
            "max_cvar95": portfolio_config.max_cvar95,
            "initial_equity": portfolio_config.initial_equity,
            "may_override_abstention": False,
            "may_relax_frozen_limits": False,
            "final_pre_portfolio_gate": pre_portfolio_gate,
        },
        "execution": {
            "paper_execution": False,
            "live_execution": False,
            "order_creation_authorized": False,
        },
        "portfolio_intent": portfolio_intent,
        "financial_policy": asdict(portfolio_config),
    }
