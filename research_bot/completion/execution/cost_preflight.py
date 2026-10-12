"""Journaled composition seam; never approves risk or resizes/places an order."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ...hypothesis import canonical_json
from ...jamalian_data import OrderBook
from ..journal import JournalPort
from ..models import MarketRules, ZERO
from .impact_estimator import ImpactDecision, ImpactEstimateResult, MarketSnapshot, SquareRootImpactEstimator
from .liquidity_gate import DepthAwareLiquidityGate, LiquidityCheckResult, LiquidityDecision


@dataclass(frozen=True)
class CostPreflightResult:
    impact: ImpactEstimateResult
    liquidity: LiquidityCheckResult

    @property
    def passed(self) -> bool:
        return self.impact.decision is ImpactDecision.OK and self.liquidity.decision is LiquidityDecision.APPROVED


class ExecutionCostPreflight:
    def __init__(self, estimator: SquareRootImpactEstimator, gate: DepthAwareLiquidityGate, journal: JournalPort) -> None:
        self.estimator, self.gate, self.journal = estimator, gate, journal

    def assess(self, assessment_id: str, snapshot: MarketSnapshot, book: OrderBook, side: str,
               quantity: Decimal, remaining_edge_bps: Decimal, current_position: Decimal, at: datetime,
               rules: MarketRules = MarketRules(), protection_price: Decimal | None = None) -> CostPreflightResult:
        impact = self.estimator.estimate(snapshot, quantity, remaining_edge_bps, at, rules)
        if snapshot.symbol != book.symbol:
            impact = ImpactEstimateResult(ImpactDecision.ABSTAIN, None, ZERO, ZERO, None, "SYMBOL_MISMATCH")
        liquidity = self.gate.validate(book, side, quantity, at, current_position, protection_price)
        result = CostPreflightResult(impact, liquidity)
        # Strings retain exact Decimal inputs; canonical JSON rejects nonfinite values.
        payload = {"snapshot": snapshot.to_dict(), "book": {"symbol": book.symbol,
            "bids": [[str(l.price), str(l.quantity)] for l in book.bids],
            "asks": [[str(l.price), str(l.quantity)] for l in book.asks],
            "observed_at": book.stamp.observed_at.isoformat(), "available_at": book.stamp.available_at.isoformat(),
            "source": book.stamp.source, "lineage": book.stamp.lineage},
            "side": side, "quantity": str(quantity), "remaining_edge_bps": str(remaining_edge_bps),
            "current_position": str(current_position), "protection_price": str(protection_price) if protection_price is not None else None,
            "rules": {k: str(v) for k, v in rules.__dict__.items()}, "estimator": self.estimator.to_dict(),
            "gate": self.gate.to_dict(), "impact": impact.to_dict(), "liquidity": liquidity.to_dict(),
            "passed": result.passed, "live_execution": False, "risk_approval_still_required": True}
        canonical_json(payload)
        self.journal.append("cost_preflight:" + assessment_id, "EXECUTION_COST_PREFLIGHT", at, payload)
        return result
