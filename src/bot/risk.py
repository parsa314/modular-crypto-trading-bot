"""Independent risk veto (no exchange API)."""
from __future__ import annotations
from datetime import datetime,timezone
from math import isfinite

def mark_and_veto(state,prices,at,config):
    previous_nav=state.nav()
    if any(not isfinite(float(p)) or p<=0 for p in prices.values()):
        raise RuntimeError("INVALID_PAPER_MARK")
    state.marks.update({s:float(p) for s,p in prices.items()})
    if set(state.positions)-set(state.marks):
        raise RuntimeError("HELD_POSITION_MISSING_MARK")
    nav=state.nav()
    if nav<=0:raise RuntimeError("PAPER_INSOLVENCY")
    now=at.astimezone(timezone.utc).date().isoformat()
    if now!=state.day:
        state.day=now
        state.daily_anchor=previous_nav
    state.peak_nav=max(state.peak_nav,nav)
    daily=1-nav/state.daily_anchor
    total=1-nav/state.peak_nav
    if daily>config.daily_kill:
        state.halted=True;state.halt_reason="DAILY_DRAWDOWN"
    if total>config.total_kill:
        state.halted=True;state.halt_reason="TOTAL_DRAWDOWN"
    return {"nav":nav,"daily_drawdown":daily,"total_drawdown":total,"risk_veto":state.halted}
