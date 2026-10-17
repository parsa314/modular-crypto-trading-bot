"""Cash-funded paper fills with 10bps fee and 5/15/40bps estimated impact.

All fills are simulations at the next observed completed hourly close; no
actual fill, order-book impact, or exchange-side execution is claimed.
"""
from __future__ import annotations
import math

def rebalance(state,targets:dict[str,float],prices:dict[str,float],tier:dict[str,int],cfg):
    if state.halted:raise ValueError("RISK_HALT_NO_NEW_ORDERS")
    if sum(targets.values())>1+1e-12 or any(
        w<0 or w>cfg.max_position_weight+1e-12 for w in targets.values()
    ):raise ValueError("TARGET_POSITION_LIMIT")
    universe=set(state.positions)|set(targets)
    if universe-set(prices):raise ValueError("UNKNOWN_PAPER_FILL_PRICE")
    if universe-set(tier):raise ValueError("UNKNOWN_COST_TIER_FAIL_CLOSED")
    if any(tier[s] not in (15,25,50) for s in universe):
        raise ValueError("UNVERIFIED_LIQUIDITY_TIER")
    value=state.cash+sum(state.positions.get(s,0)*prices[s] for s in universe)
    if value<=0:raise ValueError("NEGATIVE_NAV")
    fills=[]
    # Sell excess first; never borrow cash.
    for s in sorted(universe):
        quote=float(prices[s])
        current=state.positions.get(s,0.)
        target_value=targets.get(s,0.)*value
        sell=max(0.,current-target_value/quote)
        if sell>1e-12:
            slip=(tier[s]-cfg.fee_bps)/10000
            if slip<0:raise ValueError("NEGATIVE_SIMULATED_SLIPPAGE")
            effective=quote*(1-slip)
            gross=sell*effective;fee=gross*cfg.fee_bps/10000
            state.cash+=gross-fee
            remaining=current-sell
            if remaining<1e-10:state.positions.pop(s,None)
            else:state.positions[s]=remaining
            fills.append({"symbol":s,"side":"SELL","quantity":sell,"reference_price":quote,
                          "simulated_price":effective,"fee_usdt":fee,
                          "estimated_slippage_bps":slip*10000,"tier_total_bps":tier[s],
                          "notional_usdt":sell*quote})
    for s in sorted(universe):
        quote=float(prices[s])
        current=state.positions.get(s,0.)
        target_value=targets.get(s,0.)*value
        desired=max(0.,target_value-current*quote)
        if desired<1e-9:continue
        slip=(tier[s]-cfg.fee_bps)/10000
        effective=quote*(1+slip)
        qty=min(desired/(effective*(1+cfg.fee_bps/10000)),
                state.cash/(effective*(1+cfg.fee_bps/10000)))
        if qty<=1e-12:continue
        gross=qty*effective;fee=gross*cfg.fee_bps/10000
        state.cash-=gross+fee
        state.positions[s]=current+qty
        fills.append({"symbol":s,"side":"BUY","quantity":qty,"reference_price":quote,
                      "simulated_price":effective,"fee_usdt":fee,
                      "estimated_slippage_bps":slip*10000,"tier_total_bps":tier[s],
                      "notional_usdt":qty*quote})
    state.validate_weights(cfg)
    return fills
