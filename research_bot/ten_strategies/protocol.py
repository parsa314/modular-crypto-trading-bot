"""Frozen ten-rule scenario; never an execution authorization."""
from __future__ import annotations
from dataclasses import dataclass,asdict
from decimal import Decimal
from hashlib import sha256
from ..hypothesis import canonical_json

IDS=tuple("S"+str(i) for i in range(1,11))
NAMES=("Ichi Trend","Ichi BOS","Ichi OB Retest","Ichi FVG","Ichi Sweep","Ichi Brooks Reversal","Ichi BOS FVG","Ichi OB Sweep","BOS OB","Full Confluence")

@dataclass(frozen=True)
class TenConfig:
    fee: Decimal=Decimal("0.001")
    slippage: Decimal=Decimal("0.0002")
    allocation: Decimal=Decimal("0.10")
    stop: Decimal=Decimal("0.02")
    take: Decimal=Decimal("0.04")
    max_holding: int=48
    min_trades: int=100
    min_daily: int=1000
    pbo_blocks: int=8
    min_block_days: int=40
    boundary_guard_days: int=2
    def __post_init__(self) -> None:
        for key in ("fee","slippage","allocation","stop","take"):
            value=getattr(self,key)
            if not isinstance(value,Decimal) or not value.is_finite():raise ValueError("finite Decimal parameters required")
        if not 0<=self.fee<=Decimal(".01") or not 0<=self.slippage<=Decimal(".0025"):raise ValueError("bounded costs required")
        if not 0<self.allocation<=Decimal(".10") or not 0<self.stop<self.take<1:raise ValueError("risk ceiling or barriers invalid")
        for key in ("max_holding","min_trades","min_daily","pbo_blocks","min_block_days","boundary_guard_days"):
            if type(getattr(self,key)) is not int or getattr(self,key)<1:raise ValueError("positive integer protocol required")
        if self.pbo_blocks!=8 or self.boundary_guard_days*24<self.max_holding:raise ValueError("eight-block protocol or boundary guard invalid")
    def document(self) -> dict:
        return {"schema":"TEN_RULES_1.0.0","parameters":asdict(self),"ids":IDS,"names":NAMES,
            "detector":"R1_OPERATIONAL_2.0.0","signal_regime":"close above visible cloud and Tenkan >= Kijun; cross only S1",
            "brooks_amendment":"two-bar reversal per R1, not authentic L2/H2",
            "entry":"first bar open strictly after decision available_at","position":"one long per independent strategy; no combined allocation",
            "exit":"rule exit scheduled next observable open; intrabar hard barriers pessimistic stop-first",
            "terminal":"liquidation-cost marked inventory, no fabricated terminal fill",
            "cost_basis":"DECLARED_SCENARIO_NOT_MEASURED","expected_return_placeholder":False,
            "RESEARCH_ONLY":True,"PAPER_EXECUTION":False,"LIVE_EXECUTION":False,
            "preregistered":False,"evidence_state":"HISTORICAL_DIAGNOSTIC",
            "prior_search_history":"UNKNOWN_UNLESS_SEPARATELY_BOUND","paper_graduated":False}
    @property
    def digest(self) -> str:
        return sha256(canonical_json(self.document()).encode()).hexdigest()
