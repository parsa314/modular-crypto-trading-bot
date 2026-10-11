"""Scenario cost budget using daily-return volatility and quote notional volume.

The coefficient is supplied explicitly. No default is called calibrated or
conservative. This component does not fill orders or add costs to book VWAP.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum

from ...contracts import EvidenceStamp
from ...jamalian_data import available, currencies, decimal_value
from ..models import MarketRules, ZERO, floor_step, signed_decimal

BPS = Decimal(10000)


class ImpactDecision(str, Enum):
    OK = "OK"
    REDUCE = "REDUCE"
    ABSTAIN = "ABSTAIN"


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    mid_price: Decimal
    volatility_24h: Decimal  # Standard deviation of DAILY returns, not annualized.
    daily_volume_quote: Decimal  # Same trailing 24h window; quote NOTIONAL.
    stamp: EvidenceStamp

    def __post_init__(self) -> None:
        currencies(self.symbol)
        for name in ("mid_price", "volatility_24h", "daily_volume_quote"):
            object.__setattr__(self, name, decimal_value(getattr(self, name), positive=name == "mid_price"))

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "mid_price": str(self.mid_price),
            "volatility_24h": str(self.volatility_24h), "daily_volume_quote": str(self.daily_volume_quote),
            "volatility_basis": "DAILY_RETURN_STD", "volume_basis": "TRAILING_24H_QUOTE_NOTIONAL",
            "observed_at": self.stamp.observed_at.isoformat(), "available_at": self.stamp.available_at.isoformat(),
            "source": self.stamp.source, "lineage": self.stamp.lineage}


@dataclass(frozen=True)
class ImpactEstimateResult:
    decision: ImpactDecision
    estimated_impact_bps: Decimal | None
    max_viable_quantity_base: Decimal
    recommended_quantity_base: Decimal
    recommended_impact_bps: Decimal | None
    reason_code: str

    def to_dict(self) -> dict:
        return {key: value.value if isinstance(value, Enum) else str(value) if isinstance(value, Decimal) else value
            for key, value in self.__dict__.items()}


class SquareRootImpactEstimator:
    def __init__(self, k_coefficient: Decimal, max_impact_bps_threshold: Decimal = Decimal(25),
                 reduce_target_bps: Decimal = Decimal(20), max_participation_ratio: Decimal = Decimal("0.01"),
                 min_residual_edge_bps: Decimal = Decimal("0.1"), max_age: timedelta = timedelta(seconds=30)) -> None:
        self.k = decimal_value(k_coefficient, positive=True)
        self.max_impact_bps = decimal_value(max_impact_bps_threshold, positive=True)
        self.reduce_target_bps = decimal_value(reduce_target_bps, positive=True)
        self.max_participation_ratio = decimal_value(max_participation_ratio, positive=True)
        self.min_residual_edge_bps = decimal_value(min_residual_edge_bps, positive=True)
        self.max_age = max_age
        # These are ceilings from the unchanged default RiskGate, never overrides.
        if self.max_impact_bps > Decimal(25) or self.reduce_target_bps > self.max_impact_bps:
            raise ValueError("impact budget cannot exceed existing 25 bps ceiling")
        if self.max_participation_ratio > Decimal(1) or max_age <= timedelta(0):
            raise ValueError("invalid participation or freshness limit")

    def to_dict(self) -> dict:
        return {"k": str(self.k), "max_impact_bps": str(self.max_impact_bps),
            "reduce_target_bps": str(self.reduce_target_bps), "max_participation_ratio": str(self.max_participation_ratio),
            "min_residual_edge_bps": str(self.min_residual_edge_bps), "max_age": str(self.max_age),
            "model_status": "UNCALIBRATED_SCENARIO", "formula": "k*sigma24h*sqrt(Qbase*P/Vquote24h)*10000"}

    def _cost(self, snapshot: MarketSnapshot, quantity: Decimal) -> Decimal:
        return self.k * snapshot.volatility_24h * (quantity * snapshot.mid_price / snapshot.daily_volume_quote).sqrt() * BPS

    def _quantity(self, snapshot: MarketSnapshot, budget: Decimal) -> Decimal:
        # V/P occurs once. Squaring sqrt(P/V) and then multiplying V/P again is wrong.
        return (snapshot.daily_volume_quote / snapshot.mid_price) * (budget / (BPS * self.k * snapshot.volatility_24h)) ** 2

    def estimate(self, snapshot: MarketSnapshot, requested_qty_base: Decimal, net_edge_bps_available: Decimal,
                 at: datetime, rules: MarketRules = MarketRules()) -> ImpactEstimateResult:
        """Remaining edge must ALREADY subtract fees, spread and other modeled costs.

        REDUCE is a proposal requiring a new hypothesis and risk approval, never
        permission to mutate an approved order. Invalid inputs fail closed.
        """
        try:
            quantity = decimal_value(requested_qty_base, positive=True)
            edge = signed_decimal(net_edge_bps_available)
            available(snapshot.stamp, at, self.max_age)
            if snapshot.volatility_24h == ZERO or snapshot.daily_volume_quote == ZERO:
                return ImpactEstimateResult(ImpactDecision.ABSTAIN, None, ZERO, ZERO, None, "MISSING_MARKET_STATISTICS")
            original = self._cost(snapshot, quantity)
            participation_quantity = self.max_participation_ratio * snapshot.daily_volume_quote / snapshot.mid_price
            target = min(self.max_impact_bps, self.reduce_target_bps, edge - self.min_residual_edge_bps)
            if target <= ZERO:
                return ImpactEstimateResult(ImpactDecision.ABSTAIN, original, ZERO, ZERO, None, "NO_RESIDUAL_EDGE_BUDGET")
            # Max viable includes the positive residual-edge margin, not only a hard cost cap.
            viable = floor_step(min(self._quantity(snapshot, min(self.max_impact_bps, edge - self.min_residual_edge_bps)),
                participation_quantity), rules.quantity_step)
            if (quantity <= viable and quantity == floor_step(quantity, rules.quantity_step)
                    and quantity * snapshot.mid_price >= rules.min_notional and original < edge):
                return ImpactEstimateResult(ImpactDecision.OK, original, viable, quantity, original, "WITHIN_COST_BUDGET")
            recommended = floor_step(min(quantity, self._quantity(snapshot, target), participation_quantity), rules.quantity_step)
            if recommended == ZERO or recommended * snapshot.mid_price < rules.min_notional:
                return ImpactEstimateResult(ImpactDecision.ABSTAIN, original, viable, ZERO, None, "BELOW_MINIMUM_VIABLE_LOT")
            reduced_cost = self._cost(snapshot, recommended)
            if reduced_cost >= edge or reduced_cost > self.max_impact_bps:
                return ImpactEstimateResult(ImpactDecision.ABSTAIN, original, viable, ZERO, None, "NO_POSITIVE_POST_COST_EDGE")
            return ImpactEstimateResult(ImpactDecision.REDUCE, original, viable, recommended, reduced_cost, "REQUIRES_NEW_SIZED_HYPOTHESIS")
        except (ValueError, TypeError, ArithmeticError, AttributeError):
            return ImpactEstimateResult(ImpactDecision.ABSTAIN, None, ZERO, ZERO, None, "INVALID_OR_UNAVAILABLE_INPUT")
