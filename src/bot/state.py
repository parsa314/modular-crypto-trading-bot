"""Recoverable single-writer paper ledger. State is never a real brokerage account."""
from __future__ import annotations
from dataclasses import dataclass,field,asdict
from pathlib import Path
from math import isfinite
import json,os

@dataclass
class State:
    cash:float=1000.
    positions:dict[str,float]=field(default_factory=dict)
    marks:dict[str,float]=field(default_factory=dict)
    peak_nav:float=1000.
    daily_anchor:float=1000.
    day:str=""
    last_close_ms:int=0
    last_fill_ms:int=0
    pending:dict[str,float]=field(default_factory=dict)
    pending_for_ms:int=0
    heartbeat_utc:str=""
    trades:int=0
    errors:int=0
    halted:bool=False
    halt_reason:str=""
    schema:int=1

    def nav(self)->float:
        return self.cash+sum(q*self.marks[s] for s,q in self.positions.items())
    def snapshot(self)->dict:
        return {**asdict(self),"nav":self.nav()}
    def persist(self,path:Path)->None:
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_name(path.name+".tmp")
        with temporary.open("w",encoding="utf-8") as handle:
            json.dump(self.snapshot(),handle,allow_nan=False,sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary,path)
    @classmethod
    def restore(cls,path:Path)->"State":
        if not path.exists():
            # Never silently reset a previously started bot whose state vanished.
            if path.parent.exists() and any(path.parent.glob("*.jsonl")):
                raise RuntimeError("STATE_MISSING_WITH_EXISTING_AUDIT")
            return cls()
        obj=json.loads(path.read_text(encoding="utf-8"))
        if obj.get("schema")!=1:raise RuntimeError("STATE_SCHEMA_MISMATCH")
        permitted=set(cls.__dataclass_fields__)
        if set(obj)-permitted-{"nav"}:raise RuntimeError("STATE_UNKNOWN_FIELDS")
        state=cls(**{k:v for k,v in obj.items() if k in permitted})
        if state.cash < -1e-7 or not isfinite(state.nav()) or state.nav()<=0:
            raise RuntimeError("INVALID_PAPER_LEDGER")
        if abs(state.nav()-float(obj["nav"]))>0.01:
            raise RuntimeError("PAPER_LEDGER_CHECKSUM_MISMATCH")
        return state
