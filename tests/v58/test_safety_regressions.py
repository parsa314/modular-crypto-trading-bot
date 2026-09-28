"""Deterministic research safety checks; no model training or market access."""

import numpy as np
import pytest
from datetime import datetime, timezone

from research_bot.v58.holdout import Partition, TemporalSeal, authorize_partition_access
from research_bot.v58.safety import (
    AuditedCalibrator, AuditedScaler, DrawdownRiskGate, OrderIntentRegistry,
    PortfolioCashLedger,
)


@pytest.mark.parametrize("partition", ["VALIDATION", "TEST", "FINAL_HOLDOUT"])
def test_scaler_refuses_non_training_rows(partition):
    scaler = AuditedScaler()
    with pytest.raises(PermissionError):
        scaler.fit(np.array([[1.0], [2.0]]), row_ids=["a", "b"],
                   partitions=["DEVELOPMENT", partition])
    assert scaler.fit_row_ids == ()
    assert not hasattr(scaler.scaler, "mean_")


@pytest.mark.parametrize("ids", [["a", "a"], ["a", " "], ["a", None]])
def test_scaler_requires_unique_nonempty_row_identity(ids):
    with pytest.raises(ValueError):
        AuditedScaler().fit(np.array([[1.0], [2.0]]), row_ids=ids,
                            partitions=[Partition.DEVELOPMENT] * 2)


@pytest.mark.parametrize("partition", [Partition.FINAL_HOLDOUT, "FINAL_HOLDOUT"])
def test_final_holdout_requires_explicit_evaluation_authorization(partition):
    with pytest.raises(PermissionError):
        authorize_partition_access(partition, purpose="FINAL_EVALUATION")
    with pytest.raises(PermissionError):
        authorize_partition_access(partition, purpose="TRAIN")
    authorize_partition_access(partition, purpose="FINAL_EVALUATION",
                               final_evaluation_authorized=True)


@pytest.mark.parametrize("partition,purpose", [("DEVELOPMENT", "TYPO"),
                                               ("UNKNOWN", "TRAIN")])
def test_partition_guard_rejects_unknown_values(partition, purpose):
    with pytest.raises(ValueError):
        authorize_partition_access(partition, purpose=purpose)


def test_calibrator_cannot_report_a_fit_without_a_calibration_model():
    calibrator = AuditedCalibrator()
    with pytest.raises(NotImplementedError, match="not implemented"):
        calibrator.fit(np.array([0.2, 0.8]), np.array([0, 1]),
                       row_ids=["v1", "v2"], partitions=["VALIDATION"] * 2)
    assert calibrator.fit_row_ids == ()


def test_valid_calibration_inputs_are_checked_without_claiming_a_fit():
    calibrator = AuditedCalibrator()
    assert calibrator.validate_inputs(np.array([[0.2, 0.8], [0.6, 0.4]]),
                                     np.array([1, 0]), row_ids=["v1", "v2"],
                                     partitions=[Partition.VALIDATION] * 2) == ("v1", "v2")
    assert calibrator.fit_row_ids == ()


@pytest.mark.parametrize("probabilities,targets", [
    ([float("nan")], [1]), ([1.1], [1]), ([0.6], [2]),
    ([[0.2, 0.4]], [0]), ([[1.0]], [0]),
])
def test_invalid_calibration_values_are_rejected(probabilities, targets):
    with pytest.raises(ValueError):
        AuditedCalibrator().validate_inputs(np.array(probabilities), np.array(targets),
                                            row_ids=["v1"], partitions=["VALIDATION"])


@pytest.mark.parametrize("values", [[[float("nan")]], [[float("inf")]], [], [1.0]])
def test_scaler_rejects_invalid_value_matrix(values):
    with pytest.raises(ValueError):
        AuditedScaler().fit(np.array(values), row_ids=["a"], partitions=["DEVELOPMENT"])


def test_test_partition_is_scoring_only_and_hash_must_be_hex():
    authorize_partition_access(Partition.TEST, purpose="EVALUATE")
    for purpose in ("TRAIN", "FIT_PREPROCESSOR", "CALIBRATE", "MODEL_SELECTION"):
        with pytest.raises(PermissionError):
            authorize_partition_access(Partition.TEST, purpose=purpose)
    with pytest.raises(ValueError, match="hex"):
        TemporalSeal(datetime(2025, 1, 1, tzinfo=timezone.utc),
                     datetime(2025, 2, 1, tzinfo=timezone.utc),
                     datetime(2025, 3, 1, tzinfo=timezone.utc), "z" * 64)


@pytest.mark.parametrize("partition", ["DEVELOPMENT", "TEST", "FINAL_HOLDOUT"])
def test_calibration_only_validates_validation_partition(partition):
    with pytest.raises(PermissionError):
        AuditedCalibrator().fit(np.array([0.6]), np.array([1]),
                                row_ids=["a"], partitions=[partition])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_invalid_initial_cash_is_rejected(value):
    with pytest.raises(ValueError):
        PortfolioCashLedger(value)


@pytest.mark.parametrize("threshold", [float("nan"), float("inf"), 0, -0.1, 1.1])
def test_invalid_kill_threshold_is_rejected(threshold):
    with pytest.raises(ValueError):
        DrawdownRiskGate(threshold)


@pytest.mark.parametrize("equity,peak", [(float("nan"), 100),
                                       (100, float("nan")),
                                       (float("inf"), float("inf"))])
def test_nonfinite_risk_state_never_admits(equity, peak):
    with pytest.raises(ValueError):
        DrawdownRiskGate().evaluate(equity=equity, peak_equity=peak)


@pytest.mark.parametrize("threshold,boundary", [(0.03, 97), (0.05, 95), (0.07, 93)])
def test_kill_boundary_and_latch_allow_only_risk_reduction(threshold, boundary):
    gate = DrawdownRiskGate(threshold)
    assert gate.evaluate(equity=boundary + 0.01, peak_equity=100)
    assert not gate.evaluate(equity=boundary, peak_equity=100)
    assert not gate.evaluate(equity=100, peak_equity=100)
    assert gate.evaluate(equity=100, peak_equity=100, new_risk=False)


@pytest.mark.parametrize("identity", ["", " ", " event", "event ", None])
def test_order_identity_is_unambiguous(identity):
    with pytest.raises(ValueError):
        OrderIntentRegistry().register(identity)


def _admit(registry, ledger, gate, **overrides):
    from research_bot.v58.safety import admit_research_order
    arguments = dict(order_intent_id="event", notional=20.0,
                     expected_edge=0.01, round_trip_cost_bps=10,
                     equity=100.0, peak_equity=100.0,
                     registry=registry, ledger=ledger, risk_gate=gate)
    arguments.update(overrides)
    return admit_research_order(**arguments)


@pytest.mark.parametrize("overrides,reason", [
    ({"abstain": True}, "NO_TRADE"),
    ({"expected_edge": 0.001}, "COST_GATE"),
    ({"equity": 90}, "RISK_VETO"),
])
def test_admission_veto_never_reserves_cash_or_registers_intent(overrides, reason):
    registry, ledger, gate = OrderIntentRegistry(), PortfolioCashLedger(100), DrawdownRiskGate()
    decision = _admit(registry, ledger, gate, **overrides)
    assert not decision.admitted and decision.reason == reason
    assert ledger.cash == 100
    registry.register("event")  # The veto did not consume the identity.


def test_admission_reserves_once_and_duplicate_cannot_reduce_cash():
    registry, ledger, gate = OrderIntentRegistry(), PortfolioCashLedger(100), DrawdownRiskGate()
    assert _admit(registry, ledger, gate).admitted
    assert ledger.cash == 80
    with pytest.raises(RuntimeError, match="duplicate"):
        _admit(registry, ledger, gate)
    assert ledger.cash == 80


def test_insufficient_cash_does_not_consume_intent():
    registry, ledger, gate = OrderIntentRegistry(), PortfolioCashLedger(10), DrawdownRiskGate()
    with pytest.raises(RuntimeError, match="cash"):
        _admit(registry, ledger, gate)
    assert ledger.cash == 10
    registry.register("event")
