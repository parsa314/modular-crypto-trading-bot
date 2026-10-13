"""Deterministic decimal financial calculations, not a valuation data source."""
from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Iterable


def _finite(values: Iterable[Decimal]) -> None:
    if not all(isinstance(v, Decimal) and v.is_finite() and abs(v) <= Decimal("1e30") for v in values):
        raise ValueError("finite bounded Decimal inputs required")


def ttm(annual: Decimal, ytd: Decimal, previous_ytd: Decimal) -> Decimal:
    """Caller must separately prove comparable period, scope and revisions."""
    _finite((annual, ytd, previous_ytd))
    with localcontext() as context:
        context.prec = 64
        return annual + ytd - previous_ytd


def ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    _finite((numerator, denominator))
    with localcontext() as context:
        context.prec = 64
        return numerator / denominator if denominator > 0 else None


def margin_of_safety(value: Decimal, price: Decimal) -> tuple[Decimal, Decimal]:
    _finite((value, price))
    if value <= 0 or price <= 0: raise ValueError("positive per-share value/price required")
    with localcontext() as context:
        context.prec = 64
        return (value - price) / value, value / price - 1


def dcf(fcffs: list[Decimal], wacc: Decimal, terminal_growth: Decimal, net_debt: Decimal, other_claims: Decimal, shares: Decimal, non_operating_assets: Decimal = Decimal(0)) -> dict[str, Decimal]:
    """Nominal annual IRR FCFF; all monetary inputs IRR, shares unscaled.

    Terminal FCFF must reflect a justified steady-state reinvestment policy.
    Returns ESTIMATED scenario values, never evidence of a company's worth.
    """
    _finite((*fcffs, wacc, terminal_growth, net_debt, other_claims, shares, non_operating_assets))
    if not 1 <= len(fcffs) <= 20 or shares <= 0 or not 0 < wacc < 1 or not Decimal(-1) < terminal_growth < wacc:
        raise ValueError("invalid DCF horizon, growth, discount rate or shares")
    with localcontext() as context:
        context.prec = 36
        discount = Decimal(1) + wacc
        pv = sum((cash / discount ** year for year, cash in enumerate(fcffs, 1)), Decimal(0))
        terminal = fcffs[-1] * (1 + terminal_growth) / (wacc - terminal_growth)
        ev = pv + terminal / discount ** len(fcffs)
        equity = ev - net_debt - other_claims + non_operating_assets
        return {"enterprise_value": ev, "equity_value": equity, "equity_per_share": equity / shares}
