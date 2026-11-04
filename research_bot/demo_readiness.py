"""Independent, fail-closed D0 gate for official simulated-venue orders.

This module does not authenticate to a venue or claim that any drill has passed.
Evidence must originate from separate, recorded operator/integration checks.
A synthetic unit test setting these fields is not operational D0 evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class DemoOrderEvidence:
    venue: str = ""
    allowed_symbols: tuple[str, ...] = ()
    max_virtual_notional: Decimal = Decimal("0")
    explicit_demo_enabled: bool = False
    official_sandbox_identity_verified: bool = False
    demo_scoped_credentials_verified: bool = False
    authenticated_readonly_preflight_passed: bool = False
    durable_intent_and_recovery_validated: bool = False
    ambiguous_submit_reconciliation_validated: bool = False
    fee_liability_and_terminal_reconciliation_validated: bool = False
    independent_risk_and_kill_switch_validated: bool = False
    failure_drills_passed: bool = False
    explicit_operator_demo_order_approval: bool = False


@dataclass(frozen=True)
class DemoOrderDecision:
    order_test_eligible: bool
    missing_evidence: tuple[str, ...]
    venue: str
    allowed_symbols: tuple[str, ...]
    max_virtual_notional: Decimal
    live_execution_authorized: bool = False


_REQUIRED = (
    "explicit_demo_enabled",
    "official_sandbox_identity_verified",
    "demo_scoped_credentials_verified",
    "authenticated_readonly_preflight_passed",
    "durable_intent_and_recovery_validated",
    "ambiguous_submit_reconciliation_validated",
    "fee_liability_and_terminal_reconciliation_validated",
    "independent_risk_and_kill_switch_validated",
    "failure_drills_passed",
    "explicit_operator_demo_order_approval",
)


def evaluate_demo_order_readiness(evidence: DemoOrderEvidence) -> DemoOrderDecision:
    """Check DEMO order safety, independently of scientific/production G1 gates.

    Only a verified external D0 evidence bundle may populate all affirmative
    fields in actual operations. This function itself makes no venue calls.
    """
    missing = [name for name in _REQUIRED if getattr(evidence, name) is not True]
    venue = evidence.venue.strip()
    if not venue or not venue.endswith("_DEMO"):
        missing.append("VERIFIED_DEMO_VENUE_REQUIRED")
    symbols = evidence.allowed_symbols
    if not isinstance(symbols, tuple) or not symbols or any(
        not isinstance(symbol, str) or not symbol.strip() for symbol in symbols
    ) or len(set(symbols)) != len(symbols):
        missing.append("NONEMPTY_UNIQUE_SYMBOL_ALLOWLIST_REQUIRED")
        symbols = ()
    try:
        max_notional = Decimal(str(evidence.max_virtual_notional))
        if not max_notional.is_finite() or max_notional <= 0:
            raise ValueError("invalid cap")
    except (ValueError, TypeError, InvalidOperation):
        max_notional = Decimal("0")
        missing.append("POSITIVE_FINITE_VIRTUAL_NOTIONAL_CAP_REQUIRED")
    return DemoOrderDecision(
        order_test_eligible=not missing,
        missing_evidence=tuple(missing),
        venue=venue,
        allowed_symbols=symbols,
        max_virtual_notional=max_notional,
        live_execution_authorized=False,
    )
