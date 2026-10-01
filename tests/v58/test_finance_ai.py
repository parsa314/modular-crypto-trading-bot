"""Finance/AI orchestration regression checks."""
from __future__ import annotations

import pandas as pd
import pytest

from research_bot.v58.finance_ai import coordinate_finance_ai_handoff
from research_bot.v58.portfolio import PortfolioConfig


def prediction(**extra):
    row = {
        "event_id": "e1",
        "decision_at": pd.Timestamp("2025-01-01T00:00:00Z"),
        "probabilities": {"TP": 0.60, "SL": 0.25, "TIMEOUT": 0.15},
        "entropy": 0.70,
        "shift_score": 1.2,
    }
    row.update(extra)
    return row


def decision(**extra):
    row = {
        "event_id": "e1",
        "venue": "synthetic",
        "symbol": "BTC/USDT",
        "decision_at": pd.Timestamp("2025-01-01T00:00:00Z"),
        "entry_price": 100.0,
        "stop_price": 99.0,
        "target_price": 101.5,
        "horizon_bars": 12,
        "expected_utility": 0.004,
        "admission_reason": "ADMITTED",
        "round_trip_cost_bps": 24.0,
    }
    row.update(extra)
    return row


def test_roles_are_separated_and_execution_stays_disabled():
    out = coordinate_finance_ai_handoff(
        economics_decision=decision(),
        ai_prediction=prediction(),
        portfolio_config=PortfolioConfig(),
    )
    assert out["ai"]["role"] == "FORECAST_ONLY"
    assert out["ai"]["may_size"] is False
    assert out["finance"]["role"] == "INDEPENDENT_VETO_AND_SIZING"
    assert out["finance"]["final_pre_portfolio_gate"] == "ELIGIBLE_FOR_FINANCE_RISK_CHECK"
    assert out["execution"] == {
        "paper_execution": False,
        "live_execution": False,
        "order_creation_authorized": False,
    }
    assert out["portfolio_intent"]["policy"] == "ML_UTILITY"


@pytest.mark.parametrize("field,value", [
    ("notional", 1000.0),
    ("quantity", 1.0),
    ("risk_per_trade", 0.10),
    ("leverage", 20),
    ("live_execution", True),
])
def test_ai_cannot_take_financial_or_execution_control(field, value):
    with pytest.raises(PermissionError, match="control"):
        coordinate_finance_ai_handoff(
            economics_decision=decision(),
            ai_prediction=prediction(**{field: value}),
            portfolio_config=PortfolioConfig(),
        )


def test_finance_cannot_resurrect_ai_or_economic_abstention():
    out = coordinate_finance_ai_handoff(
        economics_decision=decision(expected_utility=-0.002, admission_reason="HIGH_ENTROPY"),
        ai_prediction=prediction(entropy=0.99),
        portfolio_config=PortfolioConfig(),
    )
    assert out["finance"]["final_pre_portfolio_gate"] == "ABSTAIN"
    assert out["finance"]["may_override_abstention"] is False
    assert out["portfolio_intent"]["admission_reason"] == "HIGH_ENTROPY"


def test_cost_assumptions_must_match_across_economics_and_finance():
    with pytest.raises(ValueError, match="cost"):
        coordinate_finance_ai_handoff(
            economics_decision=decision(round_trip_cost_bps=24.0),
            ai_prediction=prediction(),
            portfolio_config=PortfolioConfig(round_trip_cost_bps=36.0),
        )


@pytest.mark.parametrize("mutation", ["id", "clock", "probability", "venue", "admitted_nonpositive"])
def test_cross_layer_contract_fails_closed(mutation):
    d, p = decision(), prediction()
    if mutation == "id":
        p["event_id"] = "other"
    elif mutation == "clock":
        p["decision_at"] += pd.Timedelta(hours=4)
    elif mutation == "probability":
        p["probabilities"] = {"TP": 0.9, "SL": 0.9, "TIMEOUT": 0.0}
    elif mutation == "venue":
        d["venue"] = "coinex"
    elif mutation == "admitted_nonpositive":
        d["expected_utility"] = 0.0
    with pytest.raises((ValueError, PermissionError)):
        coordinate_finance_ai_handoff(
            economics_decision=d,
            ai_prediction=p,
            portfolio_config=PortfolioConfig(),
        )


def test_finance_policy_is_frozen_or_tighter_only():
    out = coordinate_finance_ai_handoff(
        economics_decision=decision(),
        ai_prediction=prediction(),
        portfolio_config=PortfolioConfig(
            risk_per_trade=0.001,
            max_asset_weight=0.20,
            max_gross_exposure=0.50,
            drawdown_kill=0.03,
            max_cvar95=0.02,
        ),
    )
    finance = out["finance"]
    assert finance["risk_per_trade"] == pytest.approx(0.001)
    assert finance["max_asset_weight"] == pytest.approx(0.20)
    assert finance["max_gross_exposure"] == pytest.approx(0.50)
    assert finance["drawdown_kill"] == pytest.approx(0.03)
    assert finance["max_cvar95"] == pytest.approx(0.02)
    assert finance["may_relax_frozen_limits"] is False
