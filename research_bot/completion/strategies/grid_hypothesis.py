from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Mapping

from ...contracts import EvidenceStamp
from ...hypothesis import HypothesisCandidate, HypothesisFamily, HypothesisSide, canonical_json, utc_text
from ...jamalian_data import currencies, decimal_value, available
from ..models import Inventory, MarketRules, ScenarioEconomics, ZERO, ONE, floor_step, seconds


@dataclass(frozen=True)
class GridParameters:
    symbol: str
    lower: Decimal
    upper: Decimal
    levels: int
    quantity: Decimal
    spacing: str = "arithmetic"
    fee_rate: Decimal = Decimal("0.001")
    spread_fraction: Decimal = Decimal("0.001")
    inventory_penalty: Decimal = Decimal("0.002")
    uncertainty_penalty: Decimal = Decimal("0.001")
    recenter_after_snapshots: int = 2
    rules: MarketRules = MarketRules()
    execution_delay: timedelta = timedelta(milliseconds=20)
    max_age: timedelta = timedelta(seconds=30)

    def __post_init__(self) -> None:
        currencies(self.symbol)
        for name in ("lower", "upper", "quantity", "fee_rate", "spread_fraction", "inventory_penalty", "uncertainty_penalty"):
            object.__setattr__(self, name, decimal_value(getattr(self, name), positive=name in {"lower", "upper", "quantity"}))
        if self.upper <= self.lower or self.fee_rate >= ONE or self.spacing not in {"arithmetic", "geometric"}:
            raise ValueError("invalid grid bounds, spacing or fee")
        if type(self.levels) is not int or self.levels < 2 or type(self.recenter_after_snapshots) is not int or self.recenter_after_snapshots < 1:
            raise ValueError("integer grid levels/recenter threshold required")
        if self.quantity % self.rules.quantity_step or self.execution_delay <= timedelta(0) or self.max_age <= timedelta(0):
            raise ValueError("invalid lot size or timing")


@dataclass(frozen=True)
class GridLevel:
    level_id: str
    price: Decimal
    side: str
    reserved_currency: str
    reservation: Decimal
    status: str = "PLANNED"


class GridHypothesisGenerator:
    """Deterministic proposal state; actual inventory comes only from input/fills.

PLANNED reservations are local proposals. SUBMITTED reservations belong to the
simulator's input Inventory and are not subtracted a second time. An out-of-range
snapshot suspends proposals; recenter waits for terminal releases and no external
capital reservation. This generator performs no I/O or order submission.
"""
    def __init__(self, parameters: GridParameters) -> None:
        self.parameters = parameters
        self.lower, self.upper = parameters.lower, parameters.upper
        self.epoch, self.outside_count = 0, 0
        self._last_outside_snapshot = ""
        self.levels: dict[str, GridLevel] = {}
        self.status = "READY"
        self.pending_cancel_ids: tuple[str, ...] = ()
        self._cache_key = ""
        self._cache: tuple[HypothesisCandidate, ...] = ()
        self._prices()  # Reject rounded geometric grids with duplicate levels.

    def _prices(self) -> tuple[Decimal, ...]:
        p = self.parameters
        if p.spacing == "arithmetic":
            raw = [self.lower + (self.upper - self.lower) * i / (p.levels - 1) for i in range(p.levels)]
        else:
            ratio = (self.upper / self.lower) ** (ONE / (p.levels - 1))
            raw = [self.lower * ratio ** i for i in range(p.levels)]
            raw[-1] = self.upper
        prices = tuple(floor_step(v, p.rules.price_tick) for v in raw)
        if prices[0] <= ZERO or len(set(prices)) != p.levels:
            raise ValueError("rounded grid must have positive distinct levels")
        return prices

    def mark_submitted(self, level_id: str) -> None:
        level = self.levels[level_id]
        if level.status == "PLANNED":
            self.levels[level_id] = replace(level, status="SUBMITTED")
        self._cache_key = ""

    def release(self, level_id: str, terminal_state: str) -> None:
        if terminal_state not in {"FILLED", "CANCELLED", "REJECTED", "EXPIRED"}:
            raise ValueError("only confirmed terminal simulator events release a level")
        self.levels[level_id] = replace(self.levels[level_id], status=terminal_state)
        self._cache_key = ""

    def checkpoint(self) -> dict:
        return {"lower": str(self.lower), "upper": str(self.upper), "epoch": self.epoch,
            "outside_count": self.outside_count, "status": self.status,
            "last_outside_snapshot": self._last_outside_snapshot,
            "pending_cancel_ids": list(self.pending_cancel_ids),
            "levels": [{**v.__dict__, "price": str(v.price), "reservation": str(v.reservation)} for v in self.levels.values()]}

    def restore(self, payload: dict) -> None:
        self.lower, self.upper = decimal_value(payload["lower"], positive=True), decimal_value(payload["upper"], positive=True)
        self.epoch, self.outside_count = int(payload["epoch"]), int(payload["outside_count"])
        self._last_outside_snapshot = payload.get("last_outside_snapshot", "")
        self.status, self.pending_cancel_ids = payload["status"], tuple(payload["pending_cancel_ids"])
        self.levels = {row["level_id"]: GridLevel(**{**row, "price": decimal_value(row["price"], positive=True),
            "reservation": decimal_value(row["reservation"])}) for row in payload["levels"]}
        self._cache_key, self._cache = "", ()
        self._prices()

    def generate(self, mid_price: Decimal, inventory: Inventory, reserved_capital: Decimal,
                 stamp: EvidenceStamp, at: datetime, first_seen: Mapping | None = None) -> list[HypothesisCandidate]:
        p = self.parameters
        mid, reserved = decimal_value(mid_price, positive=True), decimal_value(reserved_capital)
        available(stamp, at, p.max_age)
        available(inventory.stamp, at, p.max_age)
        base, quote = currencies(p.symbol)
        key = canonical_json([str(mid), str(reserved), utc_text(at), stamp.lineage,
            dict(inventory.balances), dict(inventory.reserved), self.epoch, first_seen or {}])
        if key == self._cache_key:
            return list(self._cache)
        if mid < self.lower or mid > self.upper:
            observation = canonical_json([str(mid), utc_text(stamp.observed_at), stamp.source, stamp.lineage])
            if observation != self._last_outside_snapshot:
                self.outside_count += 1
                self._last_outside_snapshot = observation
            self.status = "SUSPENDED_OUTSIDE_RANGE"
            self.pending_cancel_ids = tuple(k for k, v in self.levels.items() if v.status in {"PLANNED", "SUBMITTED"})
            if self.outside_count >= p.recenter_after_snapshots and not self.pending_cancel_ids and reserved == ZERO:
                # Preserve proportional width; never widen allocation/risk limits.
                half_width = (self.upper - self.lower) / (self.upper + self.lower)
                self.lower, self.upper = mid * (ONE - half_width), mid * (ONE + half_width)
                self.epoch += 1
                self.levels = {}
                self.outside_count, self.status = 0, "RECENTERED"
                self._prices()
            else:
                self._cache_key, self._cache = key, ()
                return []
        else:
            self.outside_count = 0
            self._last_outside_snapshot = ""
            self.status = "IN_RANGE"
        self.pending_cancel_ids = ()
        total = inventory.balances.get(base, ZERO) * mid + inventory.balances.get(quote, ZERO)
        if total <= ZERO:
            self.status = "EMPTY_INVENTORY"
            self._cache_key, self._cache = key, ()
            return []
        base_weight = inventory.balances.get(base, ZERO) * mid / total
        skew = abs(2 * base_weight - ONE)
        free_quote = max(ZERO, inventory.balances.get(quote, ZERO) - max(reserved, inventory.reserved.get(quote, ZERO)))
        free_base = inventory.free(base)
        for level in self.levels.values():
            if level.status == "PLANNED":
                if level.reserved_currency == quote:
                    free_quote -= level.reservation
                else:
                    free_base -= level.reservation
        result = []
        for i, price in enumerate(self._prices()):
            level_id = f"grid:{self.epoch}:{i}"
            if price == mid or (level_id in self.levels and self.levels[level_id].status in {"PLANNED", "SUBMITTED"}):
                continue
            buy = price < mid
            currency, required = (quote, price * p.quantity) if buy else (base, p.quantity)
            if price * p.quantity < p.rules.min_notional or required > (free_quote if buy else free_base):
                continue
            if buy:
                free_quote -= required
            else:
                free_base -= required
            distance = abs(mid - price) / price
            concentration = base_weight if buy else ONE - base_weight
            confidence = min(ONE, abs(mid - price) / ((self.upper - self.lower) / 2)) * (ONE - skew / 2)
            economics = ScenarioEconomics(distance, 2 * p.fee_rate + p.spread_fraction + p.inventory_penalty * skew,
                p.inventory_penalty * concentration, p.uncertainty_penalty, confidence)
            self.levels[level_id] = GridLevel(level_id, price, "BUY" if buy else "SELL", currency, required)
            details = {"level_id": level_id, "price": str(price), "quantity": str(p.quantity),
                "market_spread_fraction": str(p.spread_fraction), "depth_slippage_fraction": "0",
                "reserved_currency": currency, "reservation": str(required), "skew": str(skew),
                "economics": economics.to_dict(), "first_seen": dict(first_seen or {}),
                "prospective_evidence": bool(first_seen and first_seen.get("prospective_clean")),
                "mean_reversion_assumption": True, "execution_authorized": False,
                "plan": [{"symbol": p.symbol, "side": "BUY" if buy else "SELL", "quantity": str(p.quantity),
                    "limit_price": str(price), "quantity_step": str(p.rules.quantity_step), "price_tick": str(p.rules.price_tick),
                    "min_notional": str(p.rules.min_notional)}]}
            result.append(HypothesisCandidate("JAMALIAN_GRID_COMPLETION_V1", HypothesisFamily.GRID, p.symbol,
                HypothesisSide.BUY if buy else HypothesisSide.SELL, at, at + p.execution_delay,
                ("GRID_MEAN_REVERSION_MODEL_SCENARIO",), (stamp, inventory.stamp),
                {"lower": str(self.lower), "upper": str(self.upper), "levels": p.levels,
                    "spacing": p.spacing, "epoch": self.epoch}, details))
        self._cache_key, self._cache = key, tuple(result)
        return result
