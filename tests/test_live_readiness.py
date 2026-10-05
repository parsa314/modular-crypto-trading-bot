from __future__ import annotations

import pytest

from research_bot.live_readiness import (
    LiveReadinessChecklist,
    ReadinessStage,
    assert_live_execution_authorized,
    evaluate_live_readiness,
)


def test_live_readiness_is_blocked_by_default():
    decision = evaluate_live_readiness(LiveReadinessChecklist())
    assert decision.stage is ReadinessStage.BLOCKED
    assert decision.testnet_review_eligible is False
    assert decision.live_review_eligible is False
    assert decision.live_execution_authorized is False
    assert "scientific_promotion_passed" in decision.missing_gates


def test_testnet_review_requires_scientific_paper_reconciliation_order_and_risk_gates():
    decision = evaluate_live_readiness(
        LiveReadinessChecklist(
            scientific_promotion_passed=True,
            forward_paper_reconciled=True,
            order_reconciliation_validated=True,
            risk_constitution_unified=True,
        )
    )
    assert decision.stage is ReadinessStage.TESTNET_REVIEW_CANDIDATE
    assert decision.testnet_review_eligible is True
    assert decision.live_review_eligible is False
    assert decision.live_execution_authorized is False
    assert "testnet_failure_drills_passed" in decision.missing_gates


def test_all_gates_reach_live_review_but_never_auto_enable_real_money():
    decision = evaluate_live_readiness(
        LiveReadinessChecklist(
            scientific_promotion_passed=True,
            forward_paper_reconciled=True,
            order_reconciliation_validated=True,
            risk_constitution_unified=True,
            testnet_failure_drills_passed=True,
            balance_reconciliation_validated=True,
            explicit_operator_approval=True,
        )
    )
    assert decision.stage is ReadinessStage.LIVE_REVIEW_CANDIDATE
    assert decision.testnet_review_eligible is True
    assert decision.live_review_eligible is True
    assert decision.live_execution_authorized is False
    with pytest.raises(RuntimeError, match="LIVE_EXECUTION_NOT_AUTHORIZED"):
        assert_live_execution_authorized(decision)
