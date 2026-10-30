"""Pure allocation-to-proposal conversion; never sends or approves an order."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from typing import Mapping

from research_bot.testnet_execution import floor_to_step
from research_bot.financial_decimal import decimal_value as D
from .contracts import InstrumentSpec, OrderProposal, TargetAllocation, aware


def plan_allocation(target: TargetAllocation, *, equity: Decimal,
                    current_quantities: Mapping[str, Decimal], prices: Mapping[str, Decimal],
                    instruments: Mapping[str, InstrumentSpec], now: datetime,
                    entry_halted: bool, account_reconciled: bool,
                    latency: timedelta = timedelta(microseconds=1)) -> tuple[OrderProposal,...]:
    target.validate_at(now)
    aware(now)
    if latency<=timedelta(0):raise ValueError("same-close execution forbidden")
    if max(now,target.as_of)+latency>target.valid_until:raise ValueError("decision expires before execution")
    if not account_reconciled:raise ValueError("account / broker state unresolved")
    capital=D(equity)
    if capital<=0:raise ValueError("invalid equity")
    if target.no_trade:return ()
    if set(current_quantities)-set(target.target_weights):
        raise ValueError("target must explicitly cover existing positions")
    orders=[]
    with localcontext() as ctx:
        ctx.prec=120
        for symbol,weight in target.target_weights.items():
            spec=instruments[symbol];price=D(prices[symbol]);held=D(current_quantities.get(symbol,0))
            if spec.symbol!=symbol or price<=0 or floor_to_step(price,spec.price_tick)!=price:
                raise ValueError("symbol identity / price tick mismatch")
            if not spec.shorts_supported and (weight<0 or held<0):
                raise ValueError("short not authorized for instrument")
            desired=floor_to_step(capital*abs(weight)/(price*spec.contract_multiplier),spec.quantity_step)
            if weight<0:desired=-desired
            # A reversal closes first. A new opening requires a fresh account read
            # and a separate approval; both legs are never submitted together.
            reversal=held*desired<0
            delta=-held if reversal else desired-held
            if delta==0:continue
            reducing=(held>0 and delta<0 and -delta<=held) or (held<0 and delta>0 and delta<=-held)
            if entry_halted and not reducing:continue
            quantity=floor_to_step(abs(delta),spec.quantity_step)
            if quantity==0:continue
            if reducing and quantity>abs(held):raise ValueError("reduction would reverse position")
            if quantity*price*spec.contract_multiplier<spec.min_notional:
                # Dust is explicit; silently declaring a flat position is forbidden.
                raise ValueError("MIN_NOTIONAL_DUST_REQUIRES_REVIEW")
            orders.append(OrderProposal(target.decision_id,symbol,spec.venue,'BUY' if delta>0 else 'SELL',
                quantity,price,reducing,max(now,target.as_of)+latency,target.valid_until))
    # Reductions precede openings. This list is a proposal, not a risk reservation.
    return tuple(sorted(orders,key=lambda order:(not order.reduce_only,order.symbol)))
