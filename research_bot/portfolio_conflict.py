"""ADR-JAM-002: deterministic offline capital planning, outside frozen v0.51.

Admission is resource feasibility only, never economic validation or an order.
All exposure and reservation amounts use one explicitly specified quote unit.
Callers must carry forward result.state; this is not a broker or live allocator.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal

from .contracts import EvidenceStamp
from .hypothesis import HypothesisCandidate, HypothesisSide
from .jamalian_data import available, aware, currencies, decimal_value


@dataclass(frozen=True)
class ArbiterConfig:
    experiment_id: str
    max_concurrent_positions: int = 3
    max_gross_exposure_fraction: Decimal = Decimal("1")
    max_age: timedelta = timedelta(days=1)

    def __post_init__(self):
        if not isinstance(self.experiment_id, str) or not self.experiment_id.strip():
            raise ValueError("independent experiment_id required")
        if self.experiment_id.startswith(("v0.50", "v0.51", "overlap_arbitration_v51")):
            raise ValueError("frozen experiment namespace is not permitted")
        if type(self.max_concurrent_positions) is not int or self.max_concurrent_positions < 1:
            raise ValueError("positive integer position limit required")
        cap = decimal_value(self.max_gross_exposure_fraction, positive=True)
        if cap > 1 or self.max_age <= timedelta(0):
            raise ValueError("invalid unlevered exposure fraction or evidence age")
        object.__setattr__(self, "max_gross_exposure_fraction", cap)


@dataclass(frozen=True)
class PortfolioState:
    quote_currency: str
    available_capital: Decimal  # Already excludes all existing reservations.
    equity: Decimal
    open_gross_exposure: Decimal  # Quote-valued open exposure AND prior reservations.
    active_hypothesis_ids: tuple[str, ...]
    occupied_symbols: tuple[str, ...]
    stamp: EvidenceStamp

    def __post_init__(self):
        currencies("ASSET_" + self.quote_currency)
        for name in ("available_capital", "equity", "open_gross_exposure"):
            object.__setattr__(self, name, decimal_value(getattr(self, name)))
        if self.available_capital > self.equity:
            raise ValueError("available capital cannot exceed equity")
        ids, symbols = tuple(self.active_hypothesis_ids), tuple(self.occupied_symbols)
        if len(set(ids)) != len(ids) or len(set(symbols)) != len(symbols) or len(ids) != len(symbols):
            raise ValueError("unique one-symbol phase-one position identities required")
        if not all(isinstance(i, str) and i.strip() for i in ids):
            raise ValueError("active identities required")
        for symbol in symbols:
            if currencies(symbol)[1] != self.quote_currency:
                raise ValueError("position quote currency mismatch")
        object.__setattr__(self, "active_hypothesis_ids", ids)
        object.__setattr__(self, "occupied_symbols", symbols)


@dataclass(frozen=True)
class CapitalProposal:
    candidate: HypothesisCandidate
    amount: Decimal  # Includes any caller-specified fee/contingency reservation.
    gross_exposure: Decimal

    def __post_init__(self):
        if not isinstance(self.candidate, HypothesisCandidate):
            raise TypeError("HypothesisCandidate required")
        object.__setattr__(self, "amount", decimal_value(self.amount, positive=True))
        object.__setattr__(self, "gross_exposure", decimal_value(self.gross_exposure, positive=True))
        if self.gross_exposure > self.amount:
            raise ValueError("unlevered proposal exposure cannot exceed its reservation")


@dataclass(frozen=True)
class ArbitrationResult:
    experiment_id: str
    timestamp: datetime
    admitted: tuple[CapitalProposal, ...]
    rejected: tuple[tuple[str, str], ...]
    state: PortfolioState
    execution_authorized: bool = False
    policy: str = "EARLIEST_FIRST_THEN_ID_RESOURCE_FEASIBILITY_ONLY"


class PortfolioConflictArbiter:
    def __init__(self, config: ArbiterConfig):
        self.config = config

    def resolve(self, proposals: tuple[CapitalProposal, ...], state: PortfolioState,
                at: datetime) -> ArbitrationResult:
        aware(at)
        available(state.stamp, at, self.config.max_age)
        unique = {}
        for proposal in proposals:
            identity = proposal.candidate.hypothesis_id
            if identity in unique:
                raise ValueError("duplicate hypothesis proposal")
            if currencies(proposal.candidate.symbol)[1] != state.quote_currency:
                raise ValueError("capital and exposure must share the portfolio quote unit")
            unique[identity] = proposal
        # Rank BEFORE capacity filtering; never filter in caller input order.
        ranked = sorted(unique.values(), key=lambda p: (p.candidate.timestamp, p.candidate.hypothesis_id))
        capital, exposure = state.available_capital, state.open_gross_exposure
        ids, symbols = list(state.active_hypothesis_ids), list(state.occupied_symbols)
        admitted, rejected = [], []
        for proposal in ranked:
            h = proposal.candidate
            reason = None
            if h.timestamp > at or h.earliest_executable > at:
                reason = "NOT_YET_ADMISSIBLE"
            elif at - h.timestamp > self.config.max_age:
                reason = "STALE_HYPOTHESIS"
            elif h.side is not HypothesisSide.LONG:
                reason = "NO_LONG_BASELINE_CANDIDATE"
            elif h.hypothesis_id in ids:
                reason = "ALREADY_RESERVED"
            elif h.symbol in symbols:
                reason = "SYMBOL_CONFLICT"
            elif len(ids) >= self.config.max_concurrent_positions:
                reason = "MAX_CONCURRENT_POSITIONS"
            elif proposal.amount > capital:
                reason = "INSUFFICIENT_AVAILABLE_CAPITAL"
            elif exposure + proposal.gross_exposure > self.config.max_gross_exposure_fraction * state.equity:
                reason = "GROSS_EXPOSURE_LIMIT"
            if reason:
                rejected.append((h.hypothesis_id, reason))
                continue
            admitted.append(proposal)
            capital -= proposal.amount
            exposure += proposal.gross_exposure
            ids.append(h.hypothesis_id)
            symbols.append(h.symbol)
        next_state = replace(state, available_capital=capital, open_gross_exposure=exposure,
            active_hypothesis_ids=tuple(ids), occupied_symbols=tuple(symbols))
        return ArbitrationResult(self.config.experiment_id, at, tuple(admitted), tuple(rejected), next_state)
