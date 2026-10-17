"""Persistent, risk-governed simulated cash/positions book; never contacts an exchange."""
from __future__ import annotations
from dataclasses import dataclass,field
from datetime import datetime,timezone
from pathlib import Path
import json,os,tempfile
from math import isfinite

@dataclass
class Portfolio:
    cash:float
    positions:dict[str,float]=field(default_factory=dict)
    marks:dict[str,float]=field(default_factory=dict)
    peak_nav:float=0
    daily_anchor:float=0
    day:str=""
    last_hour:str=""
    halted:bool=False
    halt_reason:str=""
    rsi_active:dict[str,bool]=field(default_factory=dict)
    pending:dict[str,float]=field(default_factory=dict)
    pending_time:str=""
    def nav(self):
        return self.cash+sum(q*self.marks[s] for s,q in self.positions.items())
    def snapshot(self):
        return {
           "cash":self.cash,"positions":self.positions,"marks":self.marks,
           "peak_nav":self.peak_nav,"daily_anchor":self.daily_anchor,
           "day":self.day,"last_hour":self.last_hour,
           "halted":self.halted,"halt_reason":self.halt_reason,
           "rsi_active":self.rsi_active,"pending":self.pending,
           "pending_time":self.pending_time,"nav":self.nav()}
    @classmethod
    def new(cls,capital):
        return cls(float(capital),peak_nav=float(capital),daily_anchor=float(capital))
    @classmethod
    def restore(cls,payload):
        if payload.get("unsettled_order_intent"):
            raise ValueError("UNRECONCILED_PAPER_TRANSACTION")
        allowed={k for k in cls.__dataclass_fields__}
        p=cls(**{k:v for k,v in payload.items() if k in allowed})
        if p.nav()<=0 or not isfinite(p.nav()):raise ValueError("INVALID_PERSISTED_PORTFOLIO")
        return p
    def mark(self,prices:dict[str,float],date:datetime,cfg):
        for s,px in prices.items():
            if not isfinite(px) or px<=0:raise ValueError("INVALID_MARK")
            self.marks[s]=float(px)
        if any(s not in self.marks for s in self.positions):
            raise ValueError("MISSING_HELD_MARK")
        now=date.astimezone(timezone.utc).date().isoformat()
        equity=self.nav()
        if equity<=0: self.halted=True;self.halt_reason="INSOLVENT_SIMULATION"
        if self.day!=now:
            self.day=now
            self.daily_anchor=equity
        self.peak_nav=max(self.peak_nav,equity)
        day_dd=1-equity/self.daily_anchor if self.daily_anchor>0 else 1.
        total_dd=1-equity/self.peak_nav if self.peak_nav>0 else 1.
        if day_dd>cfg.max_daily_drawdown:
            self.halted=True;self.halt_reason="DAILY_DRAWDOWN_EXCEEDED_5PCT"
        if total_dd>cfg.max_total_drawdown:
            self.halted=True;self.halt_reason="TOTAL_DRAWDOWN_EXCEEDED_15PCT"
        return {"nav":equity,"daily_drawdown":day_dd,"total_drawdown":total_dd,
                "halted":self.halted,"reason":self.halt_reason}
    def validate_weights(self,cfg):
        equity=self.nav()
        if self.cash < -1e-5:raise ValueError("NEGATIVE_SPOT_CASH")
        for sym,q in self.positions.items():
            if q<0:raise ValueError("SHORT_POSITION_REFUSED")
            if q*self.marks[sym]/equity>cfg.max_position_weight+1e-8:
                raise ValueError("POST_FILL_MAX_POSITION_WEIGHT")
    def write_atomic(self,path:Path):
        path.parent.mkdir(parents=True,exist_ok=True)
        data=self.snapshot()
        temp=path.with_name(path.name+".tmp")
        with open(temp,"w",encoding="utf-8") as f:
            json.dump(data,f,sort_keys=True,allow_nan=False)
            f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    @classmethod
    def load(cls,path:Path,initial_cash:float):
        return cls.restore(json.loads(path.read_text())) if path.exists() else cls.new(initial_cash)
