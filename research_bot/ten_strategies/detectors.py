"""Closed-bar event detectors implementing the R1 operational choices."""
from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
import numpy as np
import pandas as pd
from ..four_systems.features import validate_bars
from ..hypothesis import canonical_json

VERSION="R1_OPERATIONAL_2.0.0"

@dataclass(frozen=True)
class Zone:
    identity: str
    kind: str
    side: int
    low: float
    high: float
    origin: int
    confirmed: int
    def payload(self) -> dict:
        return {"id":self.identity,"kind":self.kind,"side":self.side,"low":self.low,"high":self.high,"origin":self.origin,"confirmed":self.confirmed}

def _detect(x: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    o,h,l,c=(x[k].to_numpy(float) for k in ("open","high","low","close"))
    hs,ls,cs=(pd.Series(v) for v in (h,l,c))
    tk=(hs.rolling(9).max()+ls.rolling(9).min())/2
    kj=(hs.rolling(26).max()+ls.rolling(26).min())/2
    ca=((tk+kj)/2).shift(26);cb=((hs.rolling(52).max()+ls.rolling(52).min())/2).shift(26)
    top=pd.concat([ca,cb],axis=1).max(axis=1,skipna=False).to_numpy()
    bottom=pd.concat([ca,cb],axis=1).min(axis=1,skipna=False).to_numpy()
    ema=cs.ewm(span=20,adjust=False).mean().to_numpy()
    tr=np.maximum(h-l,np.maximum(np.abs(h-np.r_[np.nan,c[:-1]]),np.abs(l-np.r_[np.nan,c[:-1]])))
    atr=np.full(len(x),np.nan)
    for i in range(14,len(x)):
        atr[i]=np.mean(tr[1:15]) if i==14 else (13*atr[i-1]+tr[i])/14
    bodies=c-o;width=h-l
    ratio=np.divide(bodies,width,out=np.full(len(x),np.nan),where=width>0)
    location=np.divide(c-l,width,out=np.full(len(x),np.nan),where=width>0)
    zones=[];pivots={1:None,-1:None};break_used=set();sweep_used=set();direction=0;rows=[];events=[];zone_state={};trend_counts={1:0,-1:0}
    def create(kind: str,side: int,low: float,high: float,origin: int,at: int) -> Zone:
        low,high=float(low),float(high)
        identity=sha256(canonical_json([VERSION,timeframe,kind,side,str(x.timestamp.iloc[origin]),str(x.timestamp.iloc[at]),low,high]).encode()).hexdigest()
        return Zone(identity,kind,side,low,high,origin,at)
    for i in range(len(x)):
        invalid=[];mitigated=[];retests=[];updated=[]
        for z in zones:
            age=i-z.confirmed
            broken=(c[i]<z.low if z.side==1 else c[i]>z.high)
            full=(l[i]<=z.low if z.side==1 else h[i]>=z.high)
            touched=h[i]>=z.low and l[i]<=z.high
            if broken or age>50:
                invalid.append(z.identity);state="INVALIDATED" if broken else "EXPIRED"
            elif z.kind=="FVG" and full:
                mitigated.append(z.identity);state="MITIGATED"
            else:
                updated.append(z)
                state="TOUCHED" if touched else "ACTIVE"
                if z.kind=="OB" and z.side==1 and touched and c[i]>z.high:retests.append(z)
            fraction=max(0.,min(1.,(z.high-l[i])/(z.high-z.low) if z.side==1 else (h[i]-z.low)/(z.high-z.low)))
            old_state,old_fraction=zone_state.get(z.identity,("ACTIVE",0.))
            fraction=float(max(old_fraction,fraction))
            if z.kind=="FVG" and state in ("TOUCHED","ACTIVE"):
                state="PARTIALLY_MITIGATED" if fraction>0 else "ACTIVE"
            elif z.kind=="OB" and old_state=="TOUCHED" and state=="ACTIVE":state="TOUCHED"
            if (state,fraction)!=(old_state,old_fraction):
                events.append({**z.payload(),"state":state,"age_bars":age,"mitigation_fraction":fraction,
                    "available_at":str(x.available_at.iloc[i]),"confirmed_available_at":str(x.available_at.iloc[z.confirmed])})
            zone_state[z.identity]=(state,fraction)
        zones=updated
        breaks=[]
        for side in (1,-1):
            p=pivots[side]
            if i and p and p[0] not in break_used:
                crossed=c[i-1]<=p[1]<c[i] if side==1 else c[i]<p[1]<=c[i-1]
                if crossed:breaks.append(side)
        bos=0;choch=0;break_kind='NONE'
        if len(breaks)==1:
            bos=breaks[0];p=pivots[bos];break_used.add(p[0])
            choch=bos if direction and direction!=bos else 0
            break_kind='INITIAL_BREAK' if not direction else 'BOS' if direction==bos else 'CHOCH'
            direction=bos
            if i>=10:
                avg=float(np.mean(np.abs(bodies[i-10:i])))
                if avg>0 and abs(bodies[i])>2*avg and bodies[i]*bos>0:
                    for j in range(i-1,max(-1,i-13),-1):
                        if bodies[j]*bos<0:
                            low,high=sorted((o[j],c[j]))
                            if high>low and (c[i]>high if bos==1 else c[i]<low):
                                z=create("OB",bos,low,high,j,i);zones.append(z)
                                events.append({**z.payload(),"state":"CREATED","available_at":str(x.available_at.iloc[i])})
                            break
        sweeps=[]
        for side in (1,-1):
            p=pivots[-side]
            if p and p[0] not in sweep_used:
                hit=l[i]<p[1]<c[i] if side==1 else c[i]<p[1]<h[i]
                if hit:sweeps.append(side)
        sweep=sweeps[0] if len(sweeps)==1 else 0
        if sweep:sweep_used.add(pivots[-sweep][0])
        fvg_new=0
        if i>=2:
            if l[i]>h[i-2]:fvg_new=1;low,high=h[i-2],l[i]
            elif h[i]<l[i-2]:fvg_new=-1;low,high=h[i],l[i-2]
            if fvg_new:
                z=create("FVG",fvg_new,low,high,i-2,i);zones.append(z)
                events.append({**z.payload(),"state":"CREATED","available_at":str(x.available_at.iloc[i])})
        def nearest(pool: list[Zone]) -> Zone | None:
            return min(pool,key=lambda z:(abs(c[i]-(z.low+z.high)/2),-z.confirmed,z.identity)) if pool else None
        ob=nearest(retests)
        gap=nearest([z for z in zones if z.kind=="FVG" and z.side==1 and i-z.confirmed<=1])
        reversal=bool(i and ratio[i-1]<=-.5 and ratio[i]>=.5 and c[i]>h[i-1])
        tk_i=float(tk.iloc[i]);kj_i=float(kj.iloc[i])
        cross=bool(i and tk_i>=kj_i and tk.iloc[i-1]<kj.iloc[i-1])
        context=1 if c[i]>ema[i] else -1 if c[i]<ema[i] else 0
        for side in (1,-1):
            higher=bool(i and h[i]>h[i-1] and l[i]>l[i-1]) if side==1 else bool(i and h[i]<h[i-1] and l[i]<l[i-1])
            trend_counts[side]=trend_counts[side]+1 if context==side and higher else 0
        rows.append({"ready":bool(np.isfinite(top[i]) and np.isfinite(atr[i]) and atr[i]>0),"tk":tk_i,"kijun":kj_i,
            "cloud_top":top[i],"cloud_bottom":bottom[i],"ema20":ema[i],"atr14":atr[i],
            "regime":bool(c[i]>top[i] and tk_i>=kj_i),"cross":cross,"bos_up":bos==1 and break_kind=="BOS","bos_down":bos==-1 and break_kind=="BOS","break_up":bos==1,"break_down":bos==-1,"break_kind":break_kind,"choch":choch,
            "sweep_low":sweep==1,"sweep_low_price":float(l[i]),"ob_retest":ob,"fvg_recent":gap,
            "fvg_new":bool(fvg_new==1),"brooks_buy":reversal,"body":ratio[i],"close_location":location[i],"trend_count":trend_counts[context] if context else 0,
            "inside":bool(i and h[i]<=h[i-1] and l[i]>=l[i-1] and (h[i]<h[i-1] or l[i]>l[i-1])),
            "outside":bool(i and h[i]>h[i-1] and l[i]<l[i-1]),"invalidated":tuple(invalid),"mitigated":tuple(mitigated)})
        # Newly confirmed pivots are not available to current-bar breaks/sweeps.
        if i>=4:
            j=i-2
            for side,values in ((1,h),(-1,l)):
                others=np.delete(values[j-2:j+3],2)
                qualifies=np.all(values[j]>others) if side==1 else np.all(values[j]<others)
                if qualifies:pivots[side]=(j,float(values[j]))
    out=pd.concat([x.reset_index(drop=True),pd.DataFrame(rows)],axis=1)
    out.attrs["events"]=events;out.attrs["detector_version"]=VERSION
    return out

def detect(frame: pd.DataFrame) -> pd.DataFrame:
    x=validate_bars(frame)
    if not x.symbol.eq("BTC_USDT").all():raise ValueError("registered BTC_USDT market required")
    return _detect(x,"1h")

def higher_context(frame: pd.DataFrame) -> pd.DataFrame:
    x=validate_bars(frame)
    if not x.symbol.eq("BTC_USDT").all():raise ValueError("registered BTC_USDT context required")
    grouped=x.set_index("timestamp").resample("4h",label="right",closed="right",origin="epoch")
    h=grouped.agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum","available_at":"max"})
    h=h.loc[grouped.close.count().eq(4)].reset_index();h["symbol"]="BTC_USDT"
    if h.empty:raise ValueError("no complete 4h context")
    return _detect(h,"4h")
