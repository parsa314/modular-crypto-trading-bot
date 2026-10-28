"""Opt-in Spot H4 operational policy; frozen research defaults stay separate."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from .risk import RiskDecision, RiskEngine, RiskLimits, RiskSnapshot


POLICY_ID = "RECOVERY_SPOT_H4_DEMO_V1"
SYMBOLS = frozenset({"BTCUSDT", "ETHUSDT"})


@dataclass(frozen=True)
class BrokerRiskContext:
    """Externally reconciled inputs; this object does not itself prove a broker read."""

    account_id: str
    symbol: str
    reconciled_at: datetime
    quote_at: datetime
    position_quantity: Decimal
    reserved_sell_quantity: Decimal = Decimal("0")
    reconciled: bool = False
    unresolved_orders: bool = True
    day_start_equity: Decimal | None = None
    day_start_date_utc: str | None = None
    open_positions: int = 0
    same_direction_positions: int = 0
    last_entry_at: datetime | None = None


def _decimal(value: object) -> Decimal:
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("nonfinite risk input")
    return number


def _aware(value: datetime) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


class OperationalRiskEngine(RiskEngine):
    """Independent admission for new entries and verified long-position reductions.

    This profile neither changes frozen research limits nor submits orders.
    Order reservation, daily/peak anchor persistence and broker reconciliation
    must be supplied by the OMS; their end-to-end verification remains gated.
    """

    policy_id = POLICY_ID

    def __init__(self) -> None:
        super().__init__(RiskLimits(max_drawdown=0.05, max_gross_exposure=0.70))

    def evaluate(self, snapshot: RiskSnapshot) -> RiskDecision:
        # Decimal ratio avoids a binary-float miss at exactly the 5% boundary.
        try:
            equity, peak = _decimal(snapshot.equity), _decimal(snapshot.peak_equity)
            compatible = replace(snapshot, equity=float(equity), peak_equity=float(peak))
            result = super().evaluate(compatible)
            breached = peak > 0 and (peak - equity) / peak >= Decimal("0.05")
        except (InvalidOperation, ValueError, TypeError, OverflowError):
            return RiskDecision(False, True, ("INVALID_RISK_SNAPSHOT",), None)
        if breached and "MAX_DRAWDOWN_BREACH" not in result.reasons:
            return RiskDecision(False, True, result.reasons + ("MAX_DRAWDOWN_BREACH",), result.cvar_95)
        return result

    def evaluate_order(
        self, snapshot: RiskSnapshot, *, symbol: str, side: str,
        quantity: object, price: object, now: datetime,
        context: BrokerRiskContext | None, stop_price: object | None,
        roundtrip_cost_bps: object,
    ) -> RiskDecision:
        def reject(reason: str, hard: bool = True) -> RiskDecision:
            return RiskDecision(False, hard, (reason,), None)

        if not _aware(now) or context is None:
            return reject("MISSING_OPERATIONAL_RECONCILIATION")
        normalized = symbol.replace("/", "")
        if normalized not in SYMBOLS or normalized != context.symbol.replace("/", ""):
            return reject("OPERATIONAL_SYMBOL_MISMATCH")
        if side not in {"BUY", "SELL"}:
            return reject("INVALID_OPERATIONAL_SIDE")
        if not isinstance(context.account_id, str) or not context.account_id.strip() or context.reconciled is not True or context.unresolved_orders is not False:
            return reject("ACCOUNT_NOT_RECONCILED")
        for observed in (context.reconciled_at, context.quote_at):
            if not _aware(observed) or not timedelta(0) <= now - observed <= timedelta(seconds=120):
                return reject("STALE_OR_FUTURE_OPERATIONAL_INPUT")
        try:
            qty, ref = _decimal(quantity), _decimal(price)
            held = _decimal(context.position_quantity)
            reserved = _decimal(context.reserved_sell_quantity)
            equity, peak = _decimal(snapshot.equity), _decimal(snapshot.peak_equity)
            if min(qty, ref, equity, peak) <= 0 or min(held, reserved) < 0 or reserved > held:
                return reject("INVALID_OPERATIONAL_ACCOUNT_OR_ORDER")
        except (ValueError, TypeError, InvalidOperation):
            return reject("INVALID_OPERATIONAL_ACCOUNT_OR_ORDER")

        if side == "SELL":
            if qty > held - reserved:
                return reject("REDUCTION_EXCEEDS_RECONCILED_POSITION")
            # Independent risk approval: no opening a short or ignoring pending
            # reservations. Entry DD/exposure limits do not obstruct reduction.
            return RiskDecision(True, False, ("VERIFIED_POSITION_REDUCTION",), None)

        base = self.evaluate(snapshot)
        if not base.approved:
            return base
        try:
            anchor = _decimal(context.day_start_equity)
            today = now.astimezone(timezone.utc).date().isoformat()
            if anchor <= 0 or context.day_start_date_utc != today:
                return reject("MISSING_OR_STALE_DAY_ANCHOR")
            if (anchor - equity) / anchor >= Decimal("0.02"):
                return reject("DAILY_NEW_ENTRY_HALT")
            for count in (context.open_positions, context.same_direction_positions):
                if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                    return reject("INVALID_POSITION_COUNTS")
            if context.open_positions >= 5 or context.same_direction_positions >= 3:
                return reject("POSITION_COUNT_LIMIT")
            if context.last_entry_at is not None:
                if not _aware(context.last_entry_at) or now - context.last_entry_at < timedelta(minutes=15):
                    return reject("ENTRY_COOLDOWN")
            stop, cost = _decimal(stop_price), _decimal(roundtrip_cost_bps)
            if stop <= 0 or stop >= ref or cost < 0:
                return reject("INVALID_STOP_OR_COST")
            notional = qty * ref
            loss = qty * (ref - stop) + notional * cost / Decimal("10000")
            if loss > equity * Decimal("0.0025"):
                return reject("STOP_AND_COST_RISK_BUDGET", False)
            added_weight = notional / equity
            if _decimal(snapshot.asset_weight) + added_weight > Decimal("0.35"):
                return reject("PROJECTED_ASSET_WEIGHT")
            if _decimal(snapshot.gross_exposure) + added_weight > Decimal("0.70"):
                return reject("PROJECTED_GROSS_EXPOSURE")
        except (ValueError, TypeError, InvalidOperation):
            return reject("INVALID_STOP_COST_OR_DAY_ANCHOR")
        return RiskDecision(True, False, (POLICY_ID,), base.cvar_95)
