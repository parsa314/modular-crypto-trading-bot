"""Ten immutable Boolean rules; output only canonical research candidates."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
import pandas as pd
from ..contracts import EvidenceStamp
from ..hypothesis import HypothesisCandidate,HypothesisFamily,HypothesisSide,canonical_json
from .detectors import Zone,VERSION
from .protocol import TenConfig,IDS

@dataclass(frozen=True)
class Anchor:
    low: float | None=None
    zone_id: str | None=None
    kind: str="REGIME"
    confirmed: int=-1

def entry_flags(row: pd.Series) -> tuple[bool,...]:
    if not row.ready:return (False,)*10
    g=bool(row.regime);ob=row.ob_retest is not None;gap=row.fvg_recent is not None
    sweep=bool(row.sweep_low);brooks=bool(row.brooks_buy);bos=bool(row.bos_up)
    return (g and row.cross,g and bos,g and ob,g and gap,g and sweep,g and brooks,
        g and bos and row.fvg_new,g and ob and sweep,bos and ob,
        g and sum((ob,bool(row.fvg_new),sweep,brooks))>=2)

def entry_anchor(number: int,row: pd.Series,index: int) -> Anchor:
    ob=row.ob_retest;gap=row.fvg_recent
    if number in (3,8,9) and ob:return Anchor(ob.low,ob.identity,"OB",ob.confirmed)
    if number in (4,7) and gap:return Anchor(gap.low,gap.identity,"FVG",gap.confirmed)
    if number==5:return Anchor(float(row.sweep_low_price),None,"SWEEP",index)
    if number==6:return Anchor(float(row.low),None,"BROOKS",index)
    if number==10:
        pool=[]
        if ob:pool.append(Anchor(ob.low,ob.identity,"OB",ob.confirmed))
        if gap and row.fvg_new:pool.append(Anchor(gap.low,gap.identity,"FVG",gap.confirmed))
        if row.sweep_low:pool.append(Anchor(float(row.low),None,"SWEEP",index))
        if row.brooks_buy:pool.append(Anchor(float(row.low),None,"BROOKS",index))
        # Latest confirmation; ties BROOKS > SWEEP > FVG > OB, explicitly frozen.
        return max(pool,key=lambda a:(a.confirmed,("OB","FVG","SWEEP","BROOKS").index(a.kind))) if pool else Anchor()
    return Anchor()

def exit_reason(number: int,row: pd.Series,anchor: Anchor) -> str | None:
    below_k=row.close<row.kijun;below_cloud=row.close<row.cloud_bottom
    invalid=bool(anchor.low is not None and row.close<anchor.low) or anchor.zone_id in row.invalidated
    filled=anchor.zone_id in row.mitigated or bool(anchor.kind=="FVG" and anchor.low is not None and row.low<=anchor.low)
    rules=((below_k or below_cloud),(not row.regime or row.break_down),
        (invalid or below_k),(invalid or filled or below_k),(invalid or below_k),
        (row.close<row.ema20 or below_k),(row.break_down or invalid or filled or below_cloud),
        (invalid or below_cloud),(row.break_down or invalid),(below_cloud or invalid or (anchor.kind=="FVG" and filled)))
    return "RULE_EXIT" if rules[number-1] else None

def candidate(strategy: str,row: pd.Series,config: TenConfig,action: str,anchor: Anchor) -> HypothesisCandidate:
    if strategy not in IDS or action not in ("ENTER","EXIT"):raise ValueError("registered strategy/action required")
    at=row.available_at.to_pydatetime()
    lineage=sha256(canonical_json({"close":str(row.timestamp),"available":str(row.available_at),
        "ohlc":[str(row[k]) for k in ("open","high","low","close")],"detector":VERSION}).encode()).hexdigest()
    stamp=EvidenceStamp("CLOSED_BAR_OPERATIONAL_RULE",row.timestamp.to_pydatetime(),at,lineage)
    return HypothesisCandidate(strategy_id="TEN_RULES_1:"+strategy,family=HypothesisFamily.CONFLUENCE,
        symbol="BTC_USDT",side=HypothesisSide.BUY if action=="ENTER" else HypothesisSide.SELL,
        timestamp=at,earliest_executable=at+timedelta(microseconds=1),reasons=(strategy,action,"UNVALIDATED_RULE_HYPOTHESIS"),
        evidence=(stamp,),parameters={"protocol_sha256":config.digest,"detector_version":VERSION},
        metadata={"action":action,"anchor":{"low":anchor.low,"zone_id":anchor.zone_id,"kind":anchor.kind},
            "execution_authorized":False,"forecast_available":False,"expected_return":None,"evidence_state":"ENGINEERING_ONLY"})
