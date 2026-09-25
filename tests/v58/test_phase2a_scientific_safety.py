import numpy as np
import pytest

from research_bot.v58.holdout import Partition
from research_bot.v58.safety import (
    AuditedCalibrator, AuditedScaler, DrawdownRiskGate, OrderIntentRegistry,
    PortfolioCashLedger, cost_aware_admission,
)


def test_train_only_scaling():
    scaler = AuditedScaler().fit(np.array([[1.0], [3.0]]), row_ids=["t1", "t2"],
                                 partitions=[Partition.DEVELOPMENT]*2)
    assert scaler.fit_row_ids == ("t1", "t2")
    assert scaler.scaler.mean_[0] == 2.0


def test_fee_accounting():
    decision = cost_aware_admission(expected_edge=0.0025, round_trip_cost_bps=24)
    assert decision.admitted is True


def test_drawdown_kill_switch():
    gate = DrawdownRiskGate(0.05)
    assert gate.evaluate(equity=9499, peak_equity=10000, new_risk=True) is False
    assert gate.evaluate(equity=9800, peak_equity=10000, new_risk=True) is False


def test_duplicate_order():
    registry = OrderIntentRegistry(); registry.register("event-1")
    with pytest.raises(RuntimeError, match="duplicate"):
        registry.register("event-1")


def test_portfolio_cash_constraint():
    ledger = PortfolioCashLedger(100.0); ledger.reserve(80.0)
    with pytest.raises(RuntimeError, match="cash"):
        ledger.reserve(21.0)


def test_abstention():
    result = cost_aware_admission(expected_edge=1.0, round_trip_cost_bps=0, abstain=True)
    assert result.admitted is False and result.reason == "NO_TRADE"


def test_cost_gate():
    result = cost_aware_admission(expected_edge=0.0024, round_trip_cost_bps=24)
    assert result.admitted is False and result.reason == "COST_GATE"


def test_calibration_isolation():
    calibrator = AuditedCalibrator()
    with pytest.raises(PermissionError):
        calibrator.fit(np.array([0.6]), np.array([1]), row_ids=["h1"],
                       partitions=[Partition.FINAL_HOLDOUT])


def test_final_holdout_isolation():
    with pytest.raises(PermissionError):
        AuditedScaler().fit(np.array([[1.0]]), row_ids=["h1"],
                            partitions=[Partition.FINAL_HOLDOUT])


def test_risk_veto():
    result = cost_aware_admission(expected_edge=0.5, round_trip_cost_bps=0, risk_veto=True)
    assert result.admitted is False and result.reason == "RISK_VETO"
