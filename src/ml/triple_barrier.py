"""Triple-barrier long signal label: TP 2*ATR, SL 1*ATR, H=20, STOP_FIRST."""
from __future__ import annotations
import math
import numpy as np
import pandas as pd
COST_SIDE=.0015
HORIZON=20

def label_event(p,position,entry_index,signal_at,stop_distance,
                strategy,confidence,feat,kind="D1",direction=1):
    """STOP_FIRST: if both intrabar barriers hit, count stop. Net fee/slippage."""
    if entry_index+HORIZON >=len(p):
        return None
    if not math.isfinite(stop_distance) or stop_distance<=0:return None
    entry=float(p.open.iloc[entry_index])
    stop=entry-direction*stop_distance
    target=entry+direction*(2.0*stop_distance)
    outcome="TIMEOUT";exit_i=entry_index+HORIZON
    exitpx=float(p.close.iloc[exit_i])
    for j in range(entry_index,entry_index+HORIZON+1):
        low=float(p.low.iloc[j]);high=float(p.high.iloc[j])
        if direction==1:
            stop_hit=low<=stop;target_hit=high>=target
            if stop_hit:
                exit_i=j;exitpx=min(float(p.open.iloc[j]),stop);outcome="SL";break
            if target_hit:
                exit_i=j;exitpx=target;outcome="TP";break
        else:
            stop_hit=high>=stop;target_hit=low<=target
            if stop_hit:
                exit_i=j;exitpx=max(float(p.open.iloc[j]),stop);outcome="SL";break
            if target_hit:
                exit_i=j;exitpx=target;outcome="TP";break
    if direction==1:
        net=exitpx*(1-COST_SIDE)/(entry*(1+COST_SIDE))-1
    else:
        # Short event label is research-only, no funding or borrow modeled;
        # confine ML dataset to spot executable long signals (BUY only).
        raise ValueError("ONLY_LONG_SPOT_META_LABEL")
    if isinstance(p.index,pd.DatetimeIndex):
        ts=p.index[entry_index];te=p.index[exit_i]
    elif "timestamp" in p.columns:
        ts=pd.Timestamp(p.timestamp.iloc[entry_index])
        te=pd.Timestamp(p.timestamp.iloc[exit_i])
    else:
        raise ValueError("EVENT_TIME_INDEX_MISSING")
    if pd.Timedelta(te-ts)>pd.Timedelta(days=HORIZON+(kind=="M15")):
        raise RuntimeError("EVENT_HORIZON")
    values={k:float(v) if np.isfinite(v) else np.nan for k,v in feat.items()}
    return {"symbol":position,"strategy_id":strategy,"timeframe":kind,
            "signal_time":signal_at,"entry_time":ts,"event_end":te,
            "entry_price":entry,"exit_price":exitpx,
            "barrier":outcome,"net_pnl":float(net),"label":int(net>0),
            "confidence":float(confidence),**values}

