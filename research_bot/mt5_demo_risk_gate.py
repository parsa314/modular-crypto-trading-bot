from __future__ import annotations

"""Explicit fail-closed pre-submit risk constitution for MT5 DEMO only.

Day-start equity MUST be supplied as a same-UTC-day, externally verified
account snapshot. An absent/stale anchor blocks DEMO order submission.
No synthetic balances or implicit reset at reconnect are permitted.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


def _positive(value: object, name: str) -> Decimal:
    try:
        d = Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise ValueError(f"{name} is invalid") from exc
    if not d.is_finite() or d <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return d


def _nonnegative(value: object, name: str) -> Decimal:
    try:
        d = Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise ValueError(f"{name} is invalid") from exc
    if not d.is_finite() or d < 0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return d


@dataclass(frozen=True)
class DemoRiskLimits:
    max_trade_risk_fraction: Decimal = Decimal("0.02")
    max_daily_drawdown_fraction: Decimal = Decimal("0.10")
    stressed_roundtrip_bps: Decimal = Decimal("50")

    def __post_init__(self) -> None:
        if not Decimal("0") < self.max_trade_risk_fraction <= Decimal("0.02"):
            raise ValueError("trade risk hard cap is 2%")
        if not Decimal("0") < self.max_daily_drawdown_fraction <= Decimal("0.10"):
            raise ValueError("daily drawdown hard cap is 10%")
        if not self.stressed_roundtrip_bps.is_finite() or self.stressed_roundtrip_bps < 0:
            raise ValueError("invalid cost stress assumption")


@dataclass(frozen=True)
class DemoRiskVerdict:
    approved: bool
    kill_switch: bool
    reasons: tuple[str, ...]
    estimated_stop_loss_currency: Decimal | None = None
    estimated_stop_loss_fraction: Decimal | None = None


def check_demo_pretrade(
    *,
    equity: object,
    day_start_equity: object | None,
    day_start_date_utc: str | None,
    now: datetime,
    side: str,
    entry_price: object,
    stop_price: object,
    requested_notional: object,
    declared_risk_fraction: object,
    limits: DemoRiskLimits | None = None,
) -> DemoRiskVerdict:
    """Check new-entry admission, not broker stop guarantees or gap-risk limits.

    This deliberately NEVER transmits an order. Equity anchors are independent
    externally observed inputs; no guessed or synthetic history is permitted.
    """
    cfg = limits or DemoRiskLimits()
    if now.tzinfo is None or now.utcoffset() is None:
        return DemoRiskVerdict(False, True, ("NAIVE_DECISION_TIMESTAMP",))
    today = now.astimezone(timezone.utc).date().isoformat()
    if not day_start_date_utc or day_start_date_utc != today or day_start_equity is None:
        return DemoRiskVerdict(False, True, ("MISSING_OR_STALE_DAY_START_EQUITY",))
    try:
        eq = _positive(equity, "equity")
        anchor = _positive(day_start_equity, "day_start_equity")
        entry = _positive(entry_price, "entry_price")
        stop = _positive(stop_price, "stop_price")
        notional = _positive(requested_notional, "requested_notional")
        declared = _positive(declared_risk_fraction, "declared_risk_fraction")
    except ValueError:
        return DemoRiskVerdict(False, True, ("INVALID_PRETRADE_ACCOUNT_OR_ORDER_DATA",))

    if side not in {"BUY", "SELL"}:
        return DemoRiskVerdict(False, True, ("INVALID_ORDER_SIDE",))
    if (side == "BUY" and stop >= entry) or (side == "SELL" and stop <= entry):
        return DemoRiskVerdict(False, True, ("INVALID_STOP_DIRECTION",))

    daily_loss_fraction = max(Decimal(0), (anchor - eq) / anchor)
    if daily_loss_fraction >= cfg.max_daily_drawdown_fraction:
        return DemoRiskVerdict(False, True, ("DAILY_DRAWDOWN_KILL_SWITCH",))

    stop_risk = notional * abs(entry - stop) / entry
    cost_stress = notional * cfg.stressed_roundtrip_bps / Decimal("10000")
    total_risk = stop_risk + cost_stress
    risk_fraction = total_risk / eq
    if declared > cfg.max_trade_risk_fraction or risk_fraction > cfg.max_trade_risk_fraction:
        return DemoRiskVerdict(
            False, False, ("TRADE_LOSS_BUDGET_EXCEEDED",),
            total_risk, risk_fraction,
        )

    return DemoRiskVerdict(True, False, (), total_risk, risk_fraction)
