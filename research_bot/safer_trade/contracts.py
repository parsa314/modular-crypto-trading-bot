"""Allocation proposals supplement, rather than replace, canonical decisions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Mapping

from research_bot.contracts import Decision, EvidenceStamp, ResearchStatus, SignalEvidence
from research_bot.financial_decimal import decimal_value


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return value


@dataclass(frozen=True)
class TargetAllocation:
    decision_id: str
    as_of: datetime
    model_version: str
    target_weights: Mapping[str, Decimal]
    risk_budget: Decimal
    valid_until: datetime
    reason_code: str
    evidence: tuple[EvidenceStamp, ...]
    no_trade: bool = False
    status: ResearchStatus = ResearchStatus.HYPOTHESIS

    def __post_init__(self) -> None:
        aware(self.as_of); aware(self.valid_until)
        if not self.decision_id or not self.model_version or not self.reason_code:
            raise ValueError("allocation identity and reason required")
        if self.valid_until <= self.as_of:
            raise ValueError("nonpositive decision validity")
        if not Decimal(0) < decimal_value(self.risk_budget) <= Decimal('.0025'):
            raise ValueError("risk budget exceeds operational policy")
        weights={k:decimal_value(v,k) for k,v in self.target_weights.items()}
        if not weights or any(not k for k in weights) or any(abs(w)>Decimal('.35') for w in weights.values()):
            raise ValueError("invalid target / asset weight")
        if sum(map(abs,weights.values()),Decimal(0))>Decimal('.70') or sum(w!=0 for w in weights.values())>3:
            raise ValueError("gross exposure / position count breached")
        if not self.evidence or any(not e.is_available(self.as_of) for e in self.evidence):
            raise ValueError("missing or future evidence")
        # Copy caller mappings so a later model mutation cannot alter the proposal.
        from types import MappingProxyType
        object.__setattr__(self,'target_weights',MappingProxyType(weights))

    def validate_at(self, now: datetime) -> None:
        aware(now)
        if not self.as_of <= now <= self.valid_until:
            raise ValueError("expired or future allocation")

    def to_signal(self, symbol: str, *, expected_return: float | None = None,
                  expected_cost: float | None = None) -> SignalEvidence:
        """Unpriced allocation is NO_TRADE; a weight is not an alpha forecast."""
        import math
        from research_bot.decision import DecisionEngine
        if symbol not in self.target_weights:
            raise ValueError("symbol absent from allocation")
        metadata={'strategy_id':self.model_version,'decision_id':self.decision_id,
                  'target_weight':str(self.target_weights[symbol]),'execution_authorized':False,
                  'research_status':self.status.value}
        if self.no_trade or expected_return is None or expected_cost is None:
            return SignalEvidence(symbol.split('/')[0],self.as_of,0,0,0,0,0,Decision.NO_TRADE,
                ('ALLOCATION_REQUIRES_ECONOMIC_FORECAST_AND_RISK_APPROVAL',),self.evidence,metadata)
        if not all(math.isfinite(x) for x in (expected_return,expected_cost)) or expected_cost<0:
            raise ValueError("invalid forecast economics")
        return DecisionEngine().evaluate(asset=symbol.split('/')[0],timestamp=self.as_of,
            expected_return=expected_return,expected_cost=expected_cost,risk_penalty=0,
            uncertainty_penalty=0,confidence=0,currently_long=False,evidence=self.evidence,
            metadata=metadata)


@dataclass(frozen=True)
class InstrumentSpec:
    symbol: str
    venue: str
    kind: str
    quantity_step: Decimal
    price_tick: Decimal
    min_notional: Decimal
    contract_multiplier: Decimal = Decimal('1')
    shorts_supported: bool = False

    def __post_init__(self) -> None:
        if self.kind not in {'SPOT','LINEAR_PERPETUAL'} or not self.symbol or not self.venue:
            raise ValueError("unsupported instrument identity")
        if 'KRAKEN' in self.venue.upper():
            raise ValueError("Kraken holdout remains sealed")
        for name in ('quantity_step','price_tick','min_notional','contract_multiplier'):
            value=decimal_value(getattr(self,name),name)
            if value<=0:raise ValueError("positive instrument precision required")
            object.__setattr__(self,name,value)
        if self.kind=='SPOT' and (self.shorts_supported or self.contract_multiplier!=1):
            raise ValueError("Spot cannot silently inherit derivative contract semantics")


@dataclass(frozen=True)
class OrderProposal:
    decision_id: str
    symbol: str
    venue: str
    side: str
    quantity: Decimal
    reference_price: Decimal
    reduce_only: bool
    eligible_after: datetime
    valid_until: datetime
    execution_authorized: bool = False
