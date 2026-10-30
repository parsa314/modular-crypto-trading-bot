"""Negative and positive contract tests; no external system or order path."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from research_bot.contracts import EvidenceStamp, ExecutionMode
from research_bot.safer_trade_contract import TargetAllocationIntent, review_allocation


T = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)


def intent(**updates):
    base = TargetAllocationIntent(
        decision_id="study-001",
        model_version="ppo-seed-1",
        as_of=T,
        decided_at=T + timedelta(seconds=1),
        valid_until=T + timedelta(minutes=2),
        target_weights={"BTCUSDT": 0.35, "ETHUSDT": -0.35},
    )
    return replace(base, **updates)


def review(value=None, **kwargs):
    return review_allocation(
        value or intent(),
        evaluated_at=T + timedelta(seconds=2),
        independent_risk_approved=True,
        risk_evidence_id="risk-decision-01",
        **kwargs,
    )


def assert_rejected(item, code):
    result = review(item)
    assert result.accepted_for_research is False
    assert code in result.reasons
    assert result.validated_weights == ()


def test_valid_weights_are_normalized_but_not_executed():
    result = review(intent(target_weights={" btcusdt ": 0.25, "ETHUSDT": -0.35}))
    assert result.accepted_for_research
    assert result.validated_weights == (("BTCUSDT", 0.25), ("ETHUSDT", -0.35))


def test_future_data_rejected():
    assert_rejected(intent(as_of=T + timedelta(minutes=3)), "FUTURE_AS_OF")


def test_future_observation_rejected():
    stamp = EvidenceStamp(
        source="news", observed_at=T, available_at=T + timedelta(seconds=30)
    )
    assert_rejected(intent(evidence=(stamp,)), "PIT_EVIDENCE_UNAVAILABLE")


def test_expired_rejected():
    assert_rejected(intent(valid_until=T), "DECISION_EXPIRED")


def test_research_only_modes():
    assert_rejected(intent(mode=ExecutionMode.LIVE), "MODE_NOT_RESEARCH_ONLY")
    assert_rejected(intent(mode=ExecutionMode.TESTNET), "MODE_NOT_RESEARCH_ONLY")


def test_no_trade_always_rejected():
    assert_rejected(intent(no_trade=True), "NO_TRADE")


def test_independent_risk_attestation_required():
    result = review_allocation(intent(), evaluated_at=T + timedelta(seconds=2))
    assert not result.accepted_for_research
    assert "INDEPENDENT_RISK_EVIDENCE_REQUIRED" in result.reasons


@pytest.mark.parametrize("weights,reason", [
    ({"BTCUSDT": 0.36}, "ASSET_WEIGHT_CAP"),
    ({"BTCUSDT": 0.35, "ETHUSDT": 0.35, "SOLUSDT": 0.01}, "GROSS_EXPOSURE_CAP"),
    ({"BTCUSDT": float("nan")}, "NONFINITE_WEIGHT"),
    ({"BTCUSDT": float("inf")}, "NONFINITE_WEIGHT"),
    ({"BTCUSDT": "garbage"}, "INVALID_WEIGHT"),
    ({"btcusdt": 0.1, "BTCUSDT": 0.2}, "DUPLICATE_NORMALIZED_SYMBOL"),
    ({}, "EMPTY_ALLOCATION"),
    ({"BTCUSDT": 0.0}, "ALL_ZERO_WEIGHTS"),
])
def test_bad_allocations_fail_closed(weights, reason):
    assert_rejected(intent(target_weights=weights), reason)


def test_naive_timestamps_rejected():
    assert_rejected(intent(as_of=T.replace(tzinfo=None)), "NAIVE_OR_INVALID_TIMESTAMP")


def test_future_decision_rejected():
    assert_rejected(intent(decided_at=T + timedelta(minutes=1)), "FUTURE_DECISION")
