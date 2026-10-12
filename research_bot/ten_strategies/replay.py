"""Decimal OHLC scenario replay; no exchange, order API, or paper admission."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal,localcontext
from hashlib import sha256
import pandas as pd
from ..hypothesis import canonical_json
from ..completion.journal import JournalPort
from ..orchestrator import ResearchTradingOrchestrator
from .protocol import TenConfig,IDS
from .detectors import detect
from .rules import Anchor,entry_flags,entry_anchor,exit_reason,candidate

ZERO=Decimal(0);ONE=Decimal(1)
def decimal(value: object) -> Decimal:
    if isinstance(value,bool):raise ValueError("boolean money invalid")
    v=Decimal(str(value))
    if not v.is_finite():raise ValueError("nonfinite money invalid")
    return v

def empirical_es(returns: tuple[Decimal,...],tail: Decimal=Decimal(".05")) -> Decimal:
    if not returns or not ZERO<tail<=ONE:raise ValueError("ES sample required")
    mass=tail*len(returns);remaining=mass;total=ZERO
    for loss in sorted((-decimal(v) for v in returns),reverse=True):
        weight=min(ONE,remaining);total+=weight*loss;remaining-=weight
        if remaining<=ZERO:break
    return max(ZERO,total/mass)

@dataclass(frozen=True)
class ColumnResult:
    returns: tuple[Decimal,...]
    equity: tuple[Decimal,...]
    trades: tuple[dict,...]
    hypotheses: int
    risk_rejections: int
    remaining_base: Decimal
    pending_at_end: bool

def replay_column(view: pd.DataFrame,strategy: str,config: TenConfig,journal: JournalPort | None=None) -> ColumnResult:
    if strategy not in IDS or view.empty:raise ValueError("registered nonempty replay required")
    number=IDS.index(strategy)+1;cash=Decimal(10000);qty=ZERO;peak=cash;previous=cash
    anchor=Anchor();pending=None;entry=None;entry_index=-1;entry_cost=ZERO;entry_raw=ZERO
    stopped=False;curves=[];returns=[];trades=[];hypotheses=0;rejected=0
    orchestrator=ResearchTradingOrchestrator()
    close_values=[decimal(v) for v in view.close]
    def log(identity: str,kind: str,at: pd.Timestamp,payload: dict) -> None:
        if journal:journal.append(strategy+":"+identity,kind,at.to_pydatetime(),payload)
    def emit(row: pd.Series,index: int,action: str,new_anchor: Anchor,why: str) -> None:
        nonlocal hypotheses,pending
        h=candidate(strategy,row,config,action,new_anchor)
        outcome=orchestrator.process_candidate(h)
        if outcome.fill is not None or outcome.signal.confidence!=0:raise RuntimeError("canonical hypothesis became executable")
        log("hyp:"+h.hypothesis_id,"TEN_HYPOTHESIS",row.available_at,
            {"candidate":h.to_dict(),"decision":outcome.signal.decision.value,"reason":why,"execution_authorized":False})
        hypotheses+=1;pending=(action,h.timestamp,new_anchor,index,h.hypothesis_id)
    def sell(raw: Decimal,row: pd.Series,index: int,reason: str,ambiguous: bool,at: pd.Timestamp) -> None:
        nonlocal cash,qty,pending,entry
        realized_price=raw*(ONE-config.slippage)
        proceeds=qty*realized_price*(ONE-config.fee);cash+=proceeds
        trade={"strategy":strategy,"entry_time":entry,"exit_time":str(at),"entry_index":entry_index,
            "exit_index":index,"quantity":str(qty),"entry_budget":str(entry_cost),"proceeds":str(proceeds),
            "pnl":str(proceeds-entry_cost),"trade_net_return":str(proceeds/entry_cost-ONE),
            "raw_entry":str(entry_raw),"raw_exit":str(raw),"reason":reason,"ambiguous_ohlc_path":ambiguous,
            "record_class":"HYPOTHETICAL_OHLC_REPLAY_NOT_ORDER_FILL"}
        trades.append(trade);log("trade:"+str(entry_index)+":"+str(index),"TEN_REPLAY_TRADE",at,trade)
        qty=ZERO;pending=None;entry=None
    with localcontext() as ctx:
        ctx.prec=36
        for i in range(len(view)):
            row=view.iloc[i];opened=row.timestamp-pd.Timedelta(hours=1);raw_open=decimal(row.open)
            if pending and opened.to_pydatetime()>pending[1]:
                action,decision_at,planned_anchor,decision_index,hyp_id=pending;pending=None
                if action=="EXIT" and qty>ZERO:
                    sell(raw_open,row,i,"SCHEDULED_RULE_EXIT",False,opened)
                elif action=="ENTER" and qty==ZERO:
                    gap_invalid=planned_anchor.low is not None and raw_open<decimal(planned_anchor.low)
                    market=tuple(close_values[j]/close_values[j-1]-ONE for j in range(max(1,i-120),i)
                        if view.available_at.iloc[j]<opened)
                    if gap_invalid or len(market)<20 or empirical_es(tuple(v*config.allocation for v in market))>Decimal(".035") or stopped:
                        rejected+=1;log("reject:"+hyp_id,"TEN_REPLAY_REJECT",opened,
                            {"reason":"ANCHOR_GAP_INVALIDATED" if gap_invalid else "SCENARIO_RISK_REJECT"})
                    else:
                        budget=cash*config.allocation;price=raw_open*(ONE+config.slippage)
                        qty=budget/(price*(ONE+config.fee));cash-=budget;anchor=planned_anchor
                        entry=str(opened);entry_index=i;entry_cost=budget;entry_raw=raw_open
                        log("entry:"+hyp_id,"TEN_REPLAY_ENTRY",opened,{"quantity":str(qty),"budget":str(budget),
                            "decision_at":str(decision_at),"raw_price":str(raw_open),"execution_authorized":False})
            if qty>ZERO and opened>=pd.Timestamp(entry)+pd.Timedelta(hours=config.max_holding):
                sell(raw_open,row,i,"MAX_HOLDING",False,opened)
            if qty>ZERO:
                sl=entry_raw*(ONE-config.stop);tp=entry_raw*(ONE+config.take)
                low,high=decimal(row.low),decimal(row.high)
                if raw_open<=sl:sell(raw_open,row,i,"GAP_STOP",False,opened)
                elif low<=sl:sell(sl,row,i,"STOP_FIRST" if high>=tp else "STOP",high>=tp,row.available_at)
                elif high>=tp:sell(tp,row,i,"TAKE_PROFIT",False,row.available_at)
            equity=cash+qty*decimal(row.close)*(ONE-config.slippage)*(ONE-config.fee)
            if equity<=ZERO:raise ValueError("nonpositive scenario equity")
            peak=max(peak,equity);stopped=stopped or (ONE-equity/peak>=Decimal(".12"))
            returns.append(equity/previous-ONE);curves.append(equity);previous=equity
            if qty>ZERO and pending is None:
                why=exit_reason(number,row,anchor)
                if stopped:why="SCENARIO_DRAWDOWN_HALT"
                if why:emit(row,i,"EXIT",anchor,why)
            elif qty==ZERO and pending is None and not stopped and entry_flags(row)[number-1]:
                emit(row,i,"ENTER",entry_anchor(number,row,i),"RULE_ENTRY")
    if len(returns)!=len(view):raise RuntimeError("replay clock lost a bar")
    if journal:journal.checkpoint("ten:"+strategy,{"bars":len(view),"cash":str(cash),"remaining_base":str(qty),
        "terminal_equity":str(previous),"pending":pending is not None,"protocol_sha256":config.digest,"orders_placed":0})
    return ColumnResult(tuple(returns),tuple(curves),tuple(trades),hypotheses,rejected,qty,pending is not None)

def build_matrix(frame: pd.DataFrame,config: TenConfig=TenConfig(),journal: JournalPort | None=None,
                 implementation_sha256: str="UNBOUND") -> tuple[pd.DataFrame,dict]:
    view=detect(frame)
    dataset=sha256(canonical_json(view[["timestamp","available_at","symbol","open","high","low","close","volume"]].astype(str).to_dict("records")).encode()).hexdigest()
    if journal:journal.append("ten_protocol","TEN_PROTOCOL",view.available_at.iloc[0].to_pydatetime(),
        {"protocol":config.document(),"dataset_sha256":dataset,"implementation_sha256":implementation_sha256})
    if journal:
        for event in view.attrs["events"]:
            identity=sha256(canonical_json(event).encode()).hexdigest()
            journal.append("detector:"+identity,"TEN_DETECTOR_EVENT",pd.Timestamp(event["available_at"]).to_pydatetime(),event)
    rows={};columns={}
    for strategy in IDS:
        result=replay_column(view,strategy,config,journal);columns[strategy]=[float(v) for v in result.returns]
        pnl=[decimal(t["pnl"]) for t in result.trades];wins=sum((max(ZERO,v) for v in pnl),ZERO);losses=sum((max(ZERO,-v) for v in pnl),ZERO)
        peak=Decimal(10000);dd=ZERO
        for value in result.equity:peak=max(peak,value);dd=max(dd,ONE-value/peak)
        rows[strategy]={"hypotheses":result.hypotheses,"closed_trades":len(pnl),"net_total_return":str(result.equity[-1]/Decimal(10000)-ONE),
            "hit_rate":str(Decimal(sum(v>0 for v in pnl))/len(pnl)) if pnl else None,
            "profit_factor":str(wins/losses) if losses else None,"max_drawdown":str(dd),"es_95":str(empirical_es(result.returns)),
            "risk_rejections":result.risk_rejections,"remaining_base":str(result.remaining_base),
            "pending_at_end":result.pending_at_end,"trades":list(result.trades)}
    matrix=pd.DataFrame(columns,index=pd.DatetimeIndex(view.timestamp,name="timestamp_close"))
    report={"protocol_sha256":config.digest,"dataset_sha256":dataset,"implementation_sha256":implementation_sha256,
        "rows":len(matrix),"columns":list(IDS),"results":rows,"events":view.attrs["events"],
        "orders_placed":0,"paper_execution":False,"live_execution":False,"execution_authorized":False,
        "evidence_state":"HISTORICAL_DIAGNOSTIC","cost_model_id":"DECLARED_SCENARIO:"+config.digest[:16],"declared_costs":{"fee_per_side":str(config.fee),"slippage_per_side":str(config.slippage)},
        "economic_validation":False,"preregistered":False,"prospective_evidence":False}
    return matrix,report
