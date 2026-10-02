from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from research_bot.v59.config import FinancialConstitution, V59Config
from research_bot.v59.contracts import (
    Direction,
    GateStatus,
    ModelPrediction,
    PortfolioState,
    Regime,
    SignalCandidate,
    UncertaintyAssessment,
)
from research_bot.v59.decision import economic_gate
from research_bot.v59.evidence import EvidenceLedger
from research_bot.v59.finance import financial_gate
from research_bot.v59.hashing import stable_hash
from research_bot.v59.orchestrator import V59DecisionOrchestrator
from research_bot.v59.registry import ComponentRegistry
from research_bot.v59.think_tank import ROOM, StageFinding, prioritize


DECISION_AT = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
ENTRY_AT = DECISION_AT + timedelta(minutes=5)


def candidate(**changes):
    base = SignalCandidate(
        event_id=stable_hash({"event": 1}),
        strategy_id="C10_01_TREND_PULLBACK_REJECTION",
        strategy_family="CONFLUENCE10",
        symbol="BTC/USDT",
        venue="synthetic",
        direction=Direction.LONG,
        decision_at=DECISION_AT,
        entry_time=ENTRY_AT,
        entry_price=100.0,
        stop_price=99.0,
        target_price=102.0,
        horizon_bars=12,
        regime=Regime.TREND_UP,
        regime_confidence=0.8,
        feature_snapshot_id=stable_hash({"features": 1}),
        data_version=stable_hash({"data": 1}),
        strategy_version="v59-test",
        source_hash=stable_hash({"source": 1}),
        confirmations=("ICHIMOKU", "ICT", "SMC", "AL_BROOKS"),
    )
    return replace(base, **changes)


def prediction(event_id=None, **changes):
    base = ModelPrediction(
        event_id=event_id or candidate().event_id,
        model_id="LOGISTIC",
        model_version="1",
        available_at=DECISION_AT,
        probabilities={"TP": 0.70, "SL": 0.20, "TIMEOUT": 0.10},
        calibrated=True,
        calibration_id=stable_hash({"cal": 1}),
        entropy=0.60,
        shift_score=1.0,
    )
    return replace(base, **changes)


def uncertainty(event_id=None, **changes):
    base = UncertaintyAssessment(
        event_id=event_id or candidate().event_id,
        prediction_set=("TP",),
        conformity_score=0.9,
        empirical_coverage=0.9,
        abstain=False,
        reason="PASS",
    )
    return replace(base, **changes)


def portfolio(**changes):
    base = PortfolioState(
        timestamp=ENTRY_AT,
        equity=10_000.0,
        cash=10_000.0,
        peak_equity=10_000.0,
        gross_exposure=0.0,
        asset_exposure={},
        recent_returns=(),
    )
    return replace(base, **changes)


@pytest.mark.parametrize(
    "field,value",
    [
        ("risk_per_trade", 0.0026),
        ("max_asset_weight", 0.36),
        ("max_gross_exposure", 0.71),
        ("drawdown_kill", 0.051),
        ("max_cvar95", 0.036),
    ],
)
def test_financial_constitution_cannot_be_relaxed(field, value):
    with pytest.raises(ValueError):
        FinancialConstitution(**{field: value})


def test_execution_is_hard_disabled():
    with pytest.raises(RuntimeError):
        V59Config(live_execution=True)
    with pytest.raises(RuntimeError):
        V59Config(paper_execution=True)


def test_signal_requires_strict_next_time_not_same_time():
    with pytest.raises(ValueError, match="strictly after"):
        candidate(entry_time=DECISION_AT)


def test_prediction_probability_axis_and_sum_are_frozen():
    with pytest.raises(ValueError):
        prediction(probabilities={"TP": 0.7, "TIMEOUT": 0.1, "SL": 0.2})
    with pytest.raises(ValueError):
        prediction(probabilities={"TP": 0.8, "SL": 0.8, "TIMEOUT": 0.1})


def test_future_prediction_is_rejected():
    c = candidate()
    with pytest.raises(ValueError, match="not available"):
        economic_gate(
            c,
            prediction(c.event_id, available_at=DECISION_AT + timedelta(seconds=1)),
            uncertainty(c.event_id),
            V59Config(),
        )


def test_uncalibrated_prediction_abstains():
    c = candidate()
    result = economic_gate(
        c,
        prediction(c.event_id, calibrated=False),
        uncertainty(c.event_id),
        V59Config(),
    )
    assert result.status is GateStatus.ABSTAIN
    assert result.reason == "UNCALIBRATED_PREDICTION"


@pytest.mark.parametrize(
    "pred_change,unc_change,expected",
    [
        ({"entropy": 0.99}, {}, "HIGH_ENTROPY"),
        ({"shift_score": 9.0}, {}, "DISTRIBUTION_SHIFT"),
        ({}, {"abstain": True, "reason": "WIDE_SET"}, "CONFORMAL_UNCERTAINTY"),
    ],
)
def test_uncertainty_can_force_no_trade(pred_change, unc_change, expected):
    c = candidate()
    econ = economic_gate(
        c,
        prediction(c.event_id, **pred_change),
        uncertainty(c.event_id, **unc_change),
        V59Config(),
    )
    assert econ.status is GateStatus.ABSTAIN
    assert econ.reason == expected


def test_positive_expected_utility_reaches_financial_gate():
    c = candidate()
    econ = economic_gate(c, prediction(c.event_id), uncertainty(c.event_id), V59Config())
    assert econ.status is GateStatus.PASS
    finance = financial_gate(c, econ, portfolio(), V59Config())
    assert finance.status is GateStatus.PASS
    assert 0 < finance.approved_notional <= 3500
    assert finance.projected_asset_weight <= 0.35
    assert finance.projected_gross_exposure <= 0.70


def test_drawdown_kill_has_absolute_veto():
    c = candidate()
    econ = economic_gate(c, prediction(c.event_id), uncertainty(c.event_id), V59Config())
    state = portfolio(equity=9500.0, cash=9500.0, peak_equity=10_000.0)
    finance = financial_gate(c, econ, state, V59Config())
    assert finance.status is GateStatus.VETO
    assert finance.reason == "DRAWDOWN_KILL"
    assert finance.approved_notional == 0


def test_cash_buffer_limits_position_size():
    c = candidate()
    econ = economic_gate(c, prediction(c.event_id), uncertainty(c.event_id), V59Config())
    state = portfolio(cash=600.0)
    finance = financial_gate(c, econ, state, V59Config())
    assert finance.status is GateStatus.PASS
    assert finance.approved_notional <= 100.0 + 1e-9


def test_short_is_signal_only_in_phase1():
    c = candidate(direction=Direction.SHORT, stop_price=101.0, target_price=98.0)
    econ = economic_gate(c, prediction(c.event_id), uncertainty(c.event_id), V59Config())
    finance = financial_gate(c, econ, portfolio(), V59Config())
    assert finance.status is GateStatus.VETO
    assert finance.reason == "SHORT_NOT_AUTHORIZED_PHASE1"


def test_orchestrator_is_one_way_and_duplicate_event_fails_closed():
    c = candidate()
    engine = V59DecisionOrchestrator()
    final = engine.evaluate(
        candidate=c,
        prediction=prediction(c.event_id),
        uncertainty=uncertainty(c.event_id),
        portfolio=portfolio(),
    )
    assert final.action == "ADMITTED_RESEARCH_SIMULATION"
    assert engine.ledger.verify()
    with pytest.raises(RuntimeError, match="duplicate"):
        engine.evaluate(
            candidate=c,
            prediction=prediction(c.event_id),
            uncertainty=uncertainty(c.event_id),
            portfolio=portfolio(),
        )


def test_evidence_ledger_is_hash_chained_and_immutable(tmp_path):
    ledger = EvidenceLedger()
    ledger.append(record_type="A", payload={"x": 1}, recorded_at=DECISION_AT)
    ledger.append(record_type="B", payload={"y": 2}, recorded_at=ENTRY_AT)
    assert ledger.verify()
    path = tmp_path / "ledger.json"
    ledger.write_immutable(path)
    with pytest.raises(FileExistsError):
        ledger.write_immutable(path)


def test_component_registry_keeps_deep_and_rl_candidates_disabled():
    registry = ComponentRegistry()
    assert registry.get("LOGISTIC").enabled is True
    assert registry.get("CONFORMAL_SELECTIVE").enabled is True
    assert registry.get("DIFF_LSTM").enabled is False
    assert registry.get("TIMESFM").promotion_required is True
    assert registry.get("RECURRENT_SAC").enabled is False


def test_think_tank_has_all_requested_expert_roles_and_prioritizes_blocker():
    assert len(ROOM) == 12
    ids = {role.role_id for role in ROOM}
    assert {
        "DATA_PROFESSOR",
        "FINANCIAL_ENGINEERING_PHD",
        "ECONOMICS_PHD",
        "COMPUTER_ENGINEER",
        "PYTHON_ENGINEER",
        "AI_PHD",
        "MATHEMATICS_PHD",
        "AL_BROOKS_TRADER",
        "ICHIMOKU_TRADER",
        "ICT_TRADER",
        "SMC_TRADER",
        "RESEARCH_WRITER",
    } == ids
    decision = prioritize(
        (
            StageFinding("B", "MEDIUM", "later"),
            StageFinding("A", "BLOCKER", "repair first"),
        )
    )
    assert decision["decision"] == "STOP_AND_REPAIR_BLOCKER"
    assert decision["next_priority"] == "A"


def test_hashing_is_stable_across_dictionary_order():
    assert stable_hash({"a": 1, "b": 2}) == stable_hash({"b": 2, "a": 1})
