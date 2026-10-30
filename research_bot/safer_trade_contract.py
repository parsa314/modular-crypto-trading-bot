"""Research-only bridge from model target weights to an independently gated intent.

No venue connection, no fill engine, no order placement or promotion decisions.
Risk approval is an external attestation, not verification of the underlying
RiskEngine; only the later audited OMS may consume approved intents.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Mapping

from .contracts import EvidenceStamp, ExecutionMode


@dataclass(frozen=True)
class TargetAllocationIntent:
    decision_id: str
    model_version: str
    as_of: datetime
    decided_at: datetime
    valid_until: datetime
    target_weights: Mapping[str, float]
    evidence: tuple[EvidenceStamp, ...] = ()
    no_trade: bool = False
    mode: ExecutionMode = ExecutionMode.BACKTEST


@dataclass(frozen=True)
class AllocationReview:
    accepted_for_research: bool
    reasons: tuple[str, ...]
    validated_weights: tuple[tuple[str, float], ...] = ()


def review_allocation(
    intent: TargetAllocationIntent,
    *,
    evaluated_at: datetime,
    independent_risk_approved: bool = False,
    risk_evidence_id: str = "",
    max_asset_weight: float = 0.35,
    max_gross_exposure: float = 0.70,
) -> AllocationReview:
    """Validate a point-in-time model intention; never convert it to an order.

    Independent risk approval is required for research hand-off but is **not**
    proof of successful durable account/risk validation. No LIVE/TESTNET mode
    is accepted in this research-only bridge.
    """
    reasons: list[str] = []

    if not intent.decision_id.strip() or not intent.model_version.strip():
        reasons.append("MISSING_DECISION_ID_OR_MODEL")
    if intent.mode not in (ExecutionMode.BACKTEST, ExecutionMode.PAPER):
        reasons.append("MODE_NOT_RESEARCH_ONLY")

    dates = (intent.as_of, intent.decided_at, intent.valid_until, evaluated_at)
    if any(not isinstance(t, datetime) or t.utcoffset() is None for t in dates):
        return AllocationReview(False, ("NAIVE_OR_INVALID_TIMESTAMP",))

    if intent.as_of > intent.decided_at:
        reasons.append("FUTURE_AS_OF")
    if intent.decided_at > evaluated_at:
        reasons.append("FUTURE_DECISION")
    if intent.valid_until < intent.decided_at or evaluated_at > intent.valid_until:
        reasons.append("DECISION_EXPIRED")
    for e in intent.evidence:
        if not e.is_available(intent.as_of):
            reasons.append("PIT_EVIDENCE_UNAVAILABLE")
            break

    if intent.no_trade:
        reasons.append("NO_TRADE")
    if not independent_risk_approved or not risk_evidence_id.strip():
        reasons.append("INDEPENDENT_RISK_EVIDENCE_REQUIRED")

    if (
        not math.isfinite(max_asset_weight) or not math.isfinite(max_gross_exposure)
        or max_asset_weight <= 0 or max_asset_weight > 1
        or max_gross_exposure <= 0 or max_gross_exposure > 1
    ):
        reasons.append("INVALID_ALLOCATION_LIMITS")

    clean: list[tuple[str, float]] = []
    symbols: set[str] = set()
    if not intent.target_weights and not intent.no_trade:
        reasons.append("EMPTY_ALLOCATION")
    for raw_symbol, raw_weight in intent.target_weights.items():
        if not isinstance(raw_symbol, str) or not raw_symbol.strip():
            reasons.append("INVALID_SYMBOL")
            continue
        symbol = raw_symbol.strip().upper()
        if symbol in symbols:
            reasons.append("DUPLICATE_NORMALIZED_SYMBOL")
            continue
        symbols.add(symbol)
        try:
            value = float(raw_weight)
        except (ValueError, TypeError, OverflowError):
            reasons.append("INVALID_WEIGHT")
            continue
        if not math.isfinite(value):
            reasons.append("NONFINITE_WEIGHT")
            continue
        if abs(value) > max_asset_weight + 1e-12:
            reasons.append("ASSET_WEIGHT_CAP")
        clean.append((symbol, value))

    if clean and sum(abs(weight) for _, weight in clean) > max_gross_exposure + 1e-12:
        reasons.append("GROSS_EXPOSURE_CAP")
    if not intent.no_trade and clean and all(abs(weight) <= 1e-12 for _, weight in clean):
        reasons.append("ALL_ZERO_WEIGHTS")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return AllocationReview(
        accepted_for_research=not unique_reasons,
        reasons=unique_reasons,
        validated_weights=tuple(sorted(clean)) if not unique_reasons else (),
    )
