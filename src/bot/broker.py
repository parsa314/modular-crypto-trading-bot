"""All simulated spot fills at observed public tick prices, never live orders."""
from __future__ import annotations
import math

def fill_targets(state,targets,prices,liquidity,config):
    if state.halted:raise RuntimeError("RISK_VETO_NO_FILL")
    if any(k not in config.symbols or not 0<=v<=config.max_position
           for k,v in targets.items()):
        raise RuntimeError("INVALID_TARGET_WEIGHTS")
    if sum(targets.values())>1+1e-9:raise RuntimeError("LEVERAGE_NOT_ALLOWED")
    if (set(state.positions)|set(targets))-set(prices):
        raise RuntimeError("UNKNOWN_FILL_PRICE")
    nav=state.nav()
    if nav<=0:raise RuntimeError("INVALID_NAV")
    fills=[]
    def friction(s):
        q=liquidity.get(s,0)
        slip=config.slip_bps if q>=100e6 else max(15.,config.slip_bps) if q>=25e6 else max(40.,config.slip_bps)
        return float(slip)
    for s in sorted(set(targets)|set(state.positions)):
        current=state.positions.get(s,0.)
        price=prices[s]
        target_value=targets.get(s,0)*nav*.998
        if targets.get(s,0)>0 and current*price<=config.max_position*nav:
            # Existing trades remain untouched unless exposure breaches hard cap.
            continue
        over=max(0.,current*price-target_value)
        if over<.05 or current<=0:continue
        qty=min(current,over/price)
        bp=friction(s)
        executed=price*(1-bp/10000)
        gross=qty*executed
        fee=gross*config.fee_bps/10000
        state.cash+=gross-fee
        remaining=current-qty
        if remaining<1e-12:state.positions.pop(s,None)
        else:state.positions[s]=remaining
        fills.append({"symbol":s,"side":"SELL","quantity":qty,"price":executed,
                      "reference":price,"commission_usdt":fee,
                      "slippage_model_bps":bp,"paper_only":True})
    for s in sorted(targets):
        if targets[s]<=0 or state.positions.get(s,0)>0:continue
        price=prices[s]
        desired=max(0.,targets[s]*nav*.998-state.positions.get(s,0)*price)
        if desired<.05:continue
        bp=friction(s)
        executed=price*(1+bp/10000)
        notional=min(desired,state.cash/(1+config.fee_bps/10000))
        qty=notional/executed
        if qty<1e-12:continue
        fee=notional*config.fee_bps/10000
        state.cash-=notional+fee
        state.positions[s]=state.positions.get(s,0)+qty
        fills.append({"symbol":s,"side":"BUY","quantity":qty,"price":executed,
                      "reference":price,"commission_usdt":fee,
                      "slippage_model_bps":bp,"paper_only":True})
    final=state.nav()
    if state.cash< -1e-6 or final<=0:raise RuntimeError("SIMULATED_CASH_FAILURE")
    for s,q in state.positions.items():
        if not math.isfinite(q) or q<0 or q*state.marks[s]/final>config.max_position+1e-5:
            raise RuntimeError("POSITION_CAP_BREACHED")
    state.trades+=len(fills)
    return fills
