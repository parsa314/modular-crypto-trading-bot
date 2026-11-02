from __future__ import annotations

"""Fail-closed LIVE-readiness governance for private exchange execution.

This module does not submit orders, access exchange credentials, or authorize
real-money trading.  It converts the repository's scientific/engineering gates
into one auditable decision object so future TESTNET/LIVE work cannot bypass
missing evidence accidentally.
"""

from dataclasses import dataclass
from enum import Enum


class ReadinessStage(str, Enum):
    BLOCKED = "BLOCKED"
    TESTNET_REVIEW_CANDIDATE = "TESTNET_REVIEW_CANDIDATE"
    LIVE_REVIEW_CANDIDATE = "LIVE_REVIEW_CANDIDATE"


class LiveProductState(str, Enum):
    """Product capability and authorization states, not interchangeable gates.

    The current branch has no verified production order authority and therefore
    reports LIVE_NOT_IMPLEMENTED. Later states name future evidence-based
    transitions; declaring a value is not itself authorization.
    """
    LIVE_NOT_IMPLEMENTED = "LIVE_NOT_IMPLEMENTED"
    LIVE_IMPLEMENTED_DISABLED = "LIVE_IMPLEMENTED_DISABLED"
    LIVE_READ_ONLY_VERIFIED = "LIVE_READ_ONLY_VERIFIED"
    LIVE_EXECUTION_TESTED = "LIVE_EXECUTION_TESTED"
    LIVE_FINANCIAL_GATE_PASSED = "LIVE_FINANCIAL_GATE_PASSED"
    LIVE_RISK_GATE_PASSED = "LIVE_RISK_GATE_PASSED"
    LIVE_OPERATOR_APPROVAL_REQUIRED = "LIVE_OPERATOR_APPROVAL_REQUIRED"
    LIVE_AUTHORIZED = "LIVE_AUTHORIZED"
    LIVE_HALTED = "LIVE_HALTED"


def current_live_product_state() -> LiveProductState:
    """Current implementation status; no inferred account or operator proof."""
    return LiveProductState.LIVE_NOT_IMPLEMENTED


@dataclass(frozen=True)
class LiveReadinessChecklist:
    scientific_promotion_passed: bool = False
    forward_paper_reconciled: bool = False
    order_reconciliation_validated: bool = False
    risk_constitution_unified: bool = False
    testnet_failure_drills_passed: bool = False
    balance_reconciliation_validated: bool = False
    explicit_operator_approval: bool = False


@dataclass(frozen=True)
class LiveReadinessDecision:
    stage: ReadinessStage
    testnet_review_eligible: bool
    live_review_eligible: bool
    live_execution_authorized: bool
    missing_gates: tuple[str, ...]


_GATE_ORDER = (
    "scientific_promotion_passed",
    "forward_paper_reconciled",
    "order_reconciliation_validated",
    "risk_constitution_unified",
    "testnet_failure_drills_passed",
    "balance_reconciliation_validated",
    "explicit_operator_approval",
)

_TESTNET_GATES = (
    "scientific_promotion_passed",
    "forward_paper_reconciled",
    "order_reconciliation_validated",
    "risk_constitution_unified",
)


def evaluate_live_readiness(checklist: LiveReadinessChecklist) -> LiveReadinessDecision:
    missing = tuple(name for name in _GATE_ORDER if not bool(getattr(checklist, name)))
    testnet_ready = all(bool(getattr(checklist, name)) for name in _TESTNET_GATES)
    live_review_ready = len(missing) == 0

    if live_review_ready:
        stage = ReadinessStage.LIVE_REVIEW_CANDIDATE
    elif testnet_ready:
        stage = ReadinessStage.TESTNET_REVIEW_CANDIDATE
    else:
        stage = ReadinessStage.BLOCKED

    # Deliberately false.  Passing the checklist only makes the system eligible
    # for a separate human/governance review; it never flips real-money trading.
    return LiveReadinessDecision(
        stage=stage,
        testnet_review_eligible=testnet_ready,
        live_review_eligible=live_review_ready,
        live_execution_authorized=False,
        missing_gates=missing,
    )


def assert_live_execution_authorized(decision: LiveReadinessDecision) -> None:
    """Deny until a separately authenticated, scoped authority is implemented.

    A caller can construct or mutate a decision object in Python. In particular,
    its Boolean is not signed operator consent and cannot authorize execution.
    This temporary boundary must be replaced only together with a verified
    production authority and final broker-boundary enforcement.
    """
    raise RuntimeError(
        "LIVE_EXECUTION_NOT_AUTHORIZED: no verified production order authority"
    )

