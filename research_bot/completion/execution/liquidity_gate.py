"""Strict spot preflight using immutable, stamped, price-bounded book depth."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Mapping

from ...hypothesis import freeze, plain
from ...jamalian_data import OrderBook, available, decimal_value
from ..models import ZERO, ONE


class LiquidityDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED_DEPTH_INSUFFICIENT = "REJECTED_DEPTH_INSUFFICIENT"
    REJECTED_EXIT_LIQUIDITY_LOW = "REJECTED_EXIT_LIQUIDITY_LOW"
    REJECTED_SPREAD_TOO_WIDE = "REJECTED_SPREAD_TOO_WIDE"
    REJECTED_INVALID_INPUT = "REJECTED_INVALID_INPUT"


@dataclass(frozen=True)
class LiquidityCheckResult:
    decision: LiquidityDecision
    reason_code: str
    details: Mapping
    approved_quantity_base: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", freeze(self.details))

    def to_dict(self) -> dict:
        return {"decision": self.decision.value, "reason_code": self.reason_code,
            "details": plain(self.details), "approved_quantity_base": str(self.approved_quantity_base)}


class DepthAwareLiquidityGate:
    def __init__(self, max_consumption_ratio: Decimal = Decimal("0.10"),
                 min_exit_depth_multiple: Decimal = Decimal(2), max_spread_bps: Decimal = Decimal(35),
                 depth_lookback_levels: int = 5, max_depth_distance: Decimal = Decimal("0.01"),
                 max_age: timedelta = timedelta(seconds=30)) -> None:
        self.max_cons_ratio = decimal_value(max_consumption_ratio, positive=True)
        self.min_exit_mult = decimal_value(min_exit_depth_multiple, positive=True)
        self.max_spread = decimal_value(max_spread_bps, positive=True)
        self.band = decimal_value(max_depth_distance, positive=True)
        self.levels, self.max_age = depth_lookback_levels, max_age
        if self.max_cons_ratio > ONE or self.min_exit_mult < ONE or self.max_spread > Decimal(35) or self.band >= ONE:
            raise ValueError("invalid limits; spread cannot exceed existing 35 bps ceiling")
        if type(self.levels) is not int or self.levels < 1 or max_age <= timedelta(0):
            raise ValueError("positive depth level count and freshness required")

    def to_dict(self) -> dict:
        return {"max_consumption_ratio": str(self.max_cons_ratio), "min_exit_depth_multiple": str(self.min_exit_mult),
            "max_spread_bps": str(self.max_spread), "depth_lookback_levels": self.levels,
            "max_depth_distance": str(self.band), "max_age": str(self.max_age),
            "model_status": "VISIBLE_DEPTH_BUFFER_NOT_GUARANTEED_EXIT"}

    def validate(self, ob_snapshot: OrderBook, side: str, requested_qty_base: Decimal, at: datetime,
                 current_position_qty_base: Decimal = ZERO, protection_price: Decimal | None = None) -> LiquidityCheckResult:
        try:
            qty = decimal_value(requested_qty_base, positive=True)
            position = decimal_value(current_position_qty_base)
            protection = decimal_value(protection_price, positive=True) if protection_price is not None else None
            if side not in {"BUY", "SELL"}:
                raise ValueError("explicit BUY or SELL required")
            available(ob_snapshot.stamp, at, self.max_age)
            if side == "SELL" and qty > position:
                return LiquidityCheckResult(LiquidityDecision.REJECTED_INVALID_INPUT, "NAKED_SPOT_SELL", {}, ZERO)
            mid = (ob_snapshot.bids[0].price + ob_snapshot.asks[0].price) / Decimal(2)
            spread = (ob_snapshot.asks[0].price - ob_snapshot.bids[0].price) / mid * Decimal(10000)
            details = {"spread_bps": str(spread), "observed_at": ob_snapshot.stamp.observed_at.isoformat(),
                "available_at": ob_snapshot.stamp.available_at.isoformat(), "lineage": ob_snapshot.stamp.lineage}
            if spread > self.max_spread:
                return LiquidityCheckResult(LiquidityDecision.REJECTED_SPREAD_TOO_WIDE, "SPREAD_EXCEEDS_LIMIT", details, ZERO)
            # BUY consumes asks; SELL consumes bids. Base units avoid mixed-price quote proxies.
            entry = ob_snapshot.asks if side == "BUY" else ob_snapshot.bids
            entry = tuple(level for level in entry[:self.levels] if abs(level.price / mid - ONE) <= self.band
                and (protection is None or (level.price <= protection if side == "BUY" else level.price >= protection)))
            entry_qty = sum((level.quantity for level in entry), ZERO)
            details.update({"entry_side": "ASK" if side == "BUY" else "BID", "entry_depth_base": str(entry_qty),
                "entry_depth_quote": str(sum((l.price * l.quantity for l in entry), ZERO)),
                "requested_quantity_base": str(qty), "current_position_base": str(position)})
            if entry_qty == ZERO or qty > entry_qty * self.max_cons_ratio:
                return LiquidityCheckResult(LiquidityDecision.REJECTED_DEPTH_INSUFFICIENT, "ENTRY_CONSUMPTION_RATIO_EXCEEDED", details, ZERO)
            details["entry_consumption"] = str(qty / entry_qty)
            # A spot SELL reduces a long; it does not create a short requiring ASK coverage.
            if side == "BUY":
                exit_qty = sum((l.quantity for l in ob_snapshot.bids[:self.levels] if abs(l.price / mid - ONE) <= self.band), ZERO)
                exposure = position + qty
                details.update({"exit_side": "BID", "exit_depth_base": str(exit_qty), "post_trade_position_base": str(exposure),
                    "exit_coverage": str(exit_qty / exposure)})
                if exit_qty < self.min_exit_mult * exposure:
                    return LiquidityCheckResult(LiquidityDecision.REJECTED_EXIT_LIQUIDITY_LOW, "INSUFFICIENT_EXIT_DEPTH_BUFFER", details, ZERO)
            else:
                details.update({"post_trade_position_base": str(position - qty), "exit_check": "SPOT_LONG_REDUCTION"})
            return LiquidityCheckResult(LiquidityDecision.APPROVED, "LIQUIDITY_CHECKS_PASSED", details, qty)
        except (ValueError, TypeError, ArithmeticError, AttributeError):
            return LiquidityCheckResult(LiquidityDecision.REJECTED_INVALID_INPUT, "INVALID_OR_UNAVAILABLE_INPUT", {}, ZERO)
