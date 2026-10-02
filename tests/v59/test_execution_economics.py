from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from research_bot.v59.config import V59Config
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
from research_bot.v59.execution import (
    ExecutionAssumptions,
    LiquiditySnapshot,
    estimate_execution_cost,
)
from research_bot.v59.finance import financial_gate
from research_bot.v59.hashing import stable_hash
from research_bot.v59.orchestrator import V59DecisionOrchestrator


UTC = timezone.utc
DECISION = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
ENTRY = DECISION + timedelta(minutes=5)


def candidate(**changes):
    base = SignalCandidate(
        event_id=stable_hash({"event": "execution-stage3"}),
        strategy_id="FVG_ICT_TSI_MTF",
        strategy_family="FVG_ICT_TSI_MTF",
        symbol="BTC/USDT",
        venue="synthetic",
        direction=Direction.LONG,
        decision_at=DECISION,
        entry_time=ENTRY,
        entry_price=100.0,
        stop_price=99.0,
        target_price=102.0,
        horizon_bars=12,
        regime=Regime.TREND_UP,
        regime_confidence=0.85,
        feature_snapshot_id=stable_hash({"features": 3}),
        data_version=stable_hash({"data": 3}),
        strategy_version="V59_STAGE3",
        source_hash=stable_hash({"source": 3}),
        confirmations=("FVG", "TSI", "STRUCTURE_BREAK"),
    )
    return replace(base, **changes)


def portfolio(**changes):
    base = PortfolioState(
        timestamp=ENTRY,
        equity=10_000.0,
        cash=10_000.0,
        peak_equity=10_000.0,
        gross_exposure=0.0,
        asset_exposure={},
        recent_returns=(),
    )
    return replace(base, **changes)


def prediction(event_id):
    return ModelPrediction(
        event_id=event_id,
        model_id="LOGISTIC",
        model_version="stage3",
        available_at=DECISION,
        probabilities={"TP": 0.70, "SL": 0.20, "TIMEOUT": 0.10},
        calibrated=True,
        calibration_id=stable_hash({"cal": 3}),
        entropy=0.60,
        shift_score=1.0,
    )


def uncertainty(event_id):
    return UncertaintyAssessment(
        event_id=event_id,
        prediction_set=("TP",),
        conformity_score=0.9,
        empirical_coverage=0.9,
        abstain=False,
        reason="PASS",
    )


def liquidity(**changes):
    base = LiquiditySnapshot(
        symbol="BTC/USDT",
        observed_at=DECISION,
        bid=99.99,
        ask=100.01,
        depth_notional_10bps=100_000.0,
        source="fixture-orderbook",
        source_hash=stable_hash({"book": 1}),
    )
    return replace(base, **changes)


def test_low_cost_liquidity_estimate_is_auditable_and_fillable():
    c = candidate()
    result = estimate_execution_cost(
        candidate=c,
        portfolio=portfolio(),
        liquidity=liquidity(),
        config=V59Config(),
    )
    assert result.event_id == c.event_id
    assert result.liquidity_pass is True
    assert result.fill_fraction == pytest.approx(1.0)
    assert 0 < result.total_round_trip_bps < 50
    assert result.max_fillable_notional == pytest.approx(result.reference_notional)
    assert result.source_classification == "OBSERVED_BID_ASK_DEPTH_WITH_STRESS_COMPONENTS"


def test_future_or_stale_liquidity_is_rejected():
    c = candidate()
    with pytest.raises(ValueError, match="future"):
        estimate_execution_cost(
            candidate=c,
            portfolio=portfolio(),
            liquidity=liquidity(observed_at=DECISION + timedelta(seconds=1)),
            config=V59Config(),
        )
    with pytest.raises(ValueError, match="stale"):
        estimate_execution_cost(
            candidate=c,
            portfolio=portfolio(),
            liquidity=liquidity(observed_at=DECISION - timedelta(seconds=31)),
            config=V59Config(),
        )


def test_low_depth_forces_liquidity_veto():
    c = candidate()
    cost = estimate_execution_cost(
        candidate=c,
        portfolio=portfolio(),
        liquidity=liquidity(depth_notional_10bps=100.0),
        config=V59Config(),
    )
    assert cost.liquidity_pass is False
    econ = economic_gate(c, prediction(c.event_id), uncertainty(c.event_id), V59Config(), execution_cost=cost)
    finance = financial_gate(c, econ, portfolio(), V59Config(), execution_cost=cost)
    assert finance.status is GateStatus.VETO
    assert finance.reason == "INSUFFICIENT_LIQUIDITY_CAPACITY"
    assert finance.approved_notional == 0


def test_dynamic_execution_cost_can_turn_nominal_edge_into_no_trade():
    c = candidate()
    cheap = estimate_execution_cost(
        candidate=c,
        portfolio=portfolio(),
        liquidity=liquidity(),
        config=V59Config(),
    )
    cheap_econ = economic_gate(
        c, prediction(c.event_id), uncertainty(c.event_id), V59Config(), execution_cost=cheap
    )
    assert cheap_econ.status is GateStatus.PASS

    expensive = estimate_execution_cost(
        candidate=c,
        portfolio=portfolio(),
        liquidity=liquidity(bid=98.5, ask=101.5),
        config=V59Config(),
    )
    expensive_econ = economic_gate(
        c, prediction(c.event_id), uncertainty(c.event_id), V59Config(), execution_cost=expensive
    )
    assert expensive.total_round_trip_bps > cheap.total_round_trip_bps
    assert expensive_econ.status is GateStatus.VETO
    assert expensive_econ.reason == "NON_POSITIVE_EXPECTED_UTILITY"


def test_finance_and_economics_must_share_exact_execution_cost_assumption():
    c = candidate()
    cost = estimate_execution_cost(
        candidate=c,
        portfolio=portfolio(),
        liquidity=liquidity(),
        config=V59Config(),
    )
    econ = economic_gate(c, prediction(c.event_id), uncertainty(c.event_id), V59Config(), execution_cost=cost)
    assert econ.status is GateStatus.PASS
    with pytest.raises(ValueError, match="must match"):
        financial_gate(c, econ, portfolio(), V59Config())


def test_finance_notional_never_exceeds_liquidity_capacity():
    c = candidate()
    assumptions = ExecutionAssumptions(min_fill_fraction=0.01)
    cost = estimate_execution_cost(
        candidate=c,
        portfolio=portfolio(),
        liquidity=liquidity(depth_notional_10bps=5_000.0),
        config=V59Config(),
        assumptions=assumptions,
    )
    econ = economic_gate(c, prediction(c.event_id), uncertainty(c.event_id), V59Config(), execution_cost=cost)
    finance = financial_gate(c, econ, portfolio(), V59Config(), execution_cost=cost)
    assert finance.status is GateStatus.PASS
    assert finance.approved_notional <= cost.max_fillable_notional + 1e-12


def test_orchestrator_records_execution_cost_before_economics():
    c = candidate()
    cost = estimate_execution_cost(
        candidate=c,
        portfolio=portfolio(),
        liquidity=liquidity(),
        config=V59Config(),
    )
    engine = V59DecisionOrchestrator()
    final = engine.evaluate(
        candidate=c,
        prediction=prediction(c.event_id),
        uncertainty=uncertainty(c.event_id),
        portfolio=portfolio(),
        execution_cost=cost,
    )
    assert final.action == "ADMITTED_RESEARCH_SIMULATION"
    types = [row["record_type"] for row in engine.ledger.records]
    assert types.index("EXECUTION_COST_ESTIMATE") < types.index("ECONOMIC_DECISION")
    assert engine.ledger.verify()
