"""Deterministic hourly signals, frozen weekly 50/50 momentum and RSI sleeves."""
from __future__ import annotations
from dataclasses import dataclass
from collections import deque
import math

@dataclass(frozen=True)
class Proposal:
    weights:dict[str,float]
    eligible:tuple[str,...]
    scores:dict[str,dict]
    levels:dict[str,int]
    note:str

def rsi14(closes:list[float],period:int=14):
    if len(closes)<period+2:return None
    # Wilder recursive RSI; initialize using first full period.
    changes=[closes[i]-closes[i-1] for i in range(1,len(closes))]
    gain=sum(max(v,0) for v in changes[:period])/period
    loss=sum(max(-v,0) for v in changes[:period])/period
    for v in changes[period:]:
        gain=(gain*(period-1)+max(v,0))/period
        loss=(loss*(period-1)+max(-v,0))/period
    if loss==0:return 100. if gain>0 else 50.
    return 100-100/(1+gain/loss)

def propose(history:dict, active:dict[str,bool],cfg)->Proposal:
    eligible=[]
    values={}
    momentum_hours=cfg.momentum_days*24
    for sym in cfg.symbols:
        bars=list(history.get(sym,[]))
        if len(bars)<momentum_hours+1:continue
        # 24h USD volume: quote_volume_min is either official Binance
        # quote or a provable lower bound sum(base_volume*bar_low).
        volume=sum(b.quote_volume_min for b in bars[-24:])
        if volume<=cfg.min_quote_volume_24h:continue
        ret=bars[-1].close/bars[-1-momentum_hours].close-1
        rsi=rsi14([b.close for b in bars],cfg.rsi_period)
        if rsi is None or not math.isfinite(rsi):continue
        eligible.append(sym)
        values[sym]={"momentum_28d":ret,"rsi14":rsi,
                     "quote_volume_24h_min":volume,
                     "quote_volume_origin":bars[-1].volume_origin}
        # Update RSI state hourly. Between thresholds retain the last state.
        if rsi<30:active[sym]=True
        elif rsi>70:active[sym]=False
    if len(eligible)<5:
        return Proposal({},tuple(sorted(eligible)),values,{},
                        "CASH: fewer than five liquid assets; BTC-only parquet never claims a quintile")
    ranked=sorted(eligible,key=lambda s:(-values[s]["momentum_28d"],s))
    k=math.ceil(len(ranked)/5)
    top=ranked[:k]
    oversold=[s for s in eligible if active.get(s,False)]
    weights={}
    for sym in top:weights[sym]=weights.get(sym,0)+.5/len(top)
    if oversold:
        for sym in oversold:weights[sym]=weights.get(sym,0)+.5/len(oversold)
    weights={sym:min(float(w),cfg.max_position_weight) for sym,w in weights.items()}
    tiers={s:(15 if s in {"BTCUSDT","ETHUSDT"}
             else 25 if rank<=20 else 50)
           for rank,s in enumerate(sorted(eligible,key=lambda s:(-values[s]["quote_volume_24h_min"],s)),start=1)}
    return Proposal(weights,tuple(sorted(eligible)),values,tiers,
                    "weekly rebalance, 50% momentum top quintile + 50% RSI stateful sleeve, excess cash")
