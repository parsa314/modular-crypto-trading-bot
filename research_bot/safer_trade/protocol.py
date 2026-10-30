"""Explicit study registry and stage gates; calendar dates do not prove novelty."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any


GAPS = {
 'G1':('ML_RL_INCREMENTAL_VALUE','PRIMARY'),
 'G2':('SELECTION_AND_COST_ROBUSTNESS','PRIMARY'),
 'G3':('SIMULATION_EXECUTION_GAP','ENGINEERING'),
 'G4':('REPRODUCIBILITY_LINEAGE','ENGINEERING'),
 'G5':('CROSS_MARKET_TRANSFER','DEFERRED'),
 'G6':('REGIME_AND_DECAY','PRIMARY'),
 'G7':('MULTI_AGENT_INCREMENTAL_VALUE','OPTIONAL_GATED'),
 'G8':('INDEPENDENT_RISK_CONTROL','ENGINEERING'),
 'G9':('CAPACITY_AND_IMPACT','DATA_GATED'),
 'G10':('TOTAL_COST_AND_GOVERNANCE','TELEMETRY'),
}


def canonical(value: Any) -> str:
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class StudyProtocol:
    study_id: str
    code_sha: str
    venue: str | None = None
    instrument_kind: str = 'LINEAR_PERPETUAL'
    assets: tuple[str,...] = ('BTC/USDT','ETH/USDT','SOL/USDT')
    timeframe: str = '1h'
    higher_timeframe: str = '4h'
    dataset_sha256: str | None = None
    dataset_id: str | None = None
    train_start: str = '2021-01-01T00:00:00+00:00'
    train_end: str = '2024-01-01T00:00:00+00:00'
    development_end: str = '2026-01-01T00:00:00+00:00'
    evaluation_end: str | None = None
    evaluation_consumption: str = 'UNKNOWN'
    registered_at: str | None = None
    mode: str = 'HISTORICAL_DIAGNOSTIC'
    engine_id: str = 'NAUTILUS_1_231_0_REFERENCE'
    baseline_id: str = 'B0'
    trial_ids: tuple[str,...] = ('B0','B1_LOGISTIC','B1_HGB','B1_LIGHTGBM','B2_PPO','B3_AGENTS')
    seeds: tuple[int,...] = (11,23,42,77,101)
    roundtrip_cost_bps: tuple[float,...] = (0,24,36,50)
    primary_cost_bps: float = 24
    min_daily_periods: int = 120
    min_regime_periods: int = 30
    block_lengths: tuple[int,...] = (5,10,20)
    bootstrap_samples: int = 2000
    alpha: float = .05
    max_pbo: float = .30
    min_dsr_probability: float = .95
    label_horizon_bars: int = 1
    embargo_bars: int = 1
    model_hashes: dict[str,str] = field(default_factory=dict)
    universe_rationale: str = 'PROPOSED_LIQUID_UNIVERSE_NOT_SURVIVORSHIP_FREE_CLAIM'

    def validate(self) -> None:
        if not self.study_id or not re.fullmatch(r'[0-9a-f]{40}',self.code_sha):raise ValueError("exact source SHA required")
        if self.venue and 'KRAKEN' in self.venue.upper():raise ValueError("Kraken sealed")
        if self.instrument_kind not in {'SPOT','LINEAR_PERPETUAL'}:raise ValueError("instrument contract")
        if self.timeframe not in {'1h','4h'} or self.higher_timeframe!='4h':raise ValueError("independent timeframe scope")
        if not self.assets or len(set(self.assets))!=len(self.assets):raise ValueError("asset identity")
        if len(set(self.trial_ids))!=len(self.trial_ids) or self.baseline_id not in self.trial_ids:raise ValueError("trial registry mismatch")
        if self.mode not in {'HISTORICAL_DIAGNOSTIC','PROSPECTIVE_INDEPENDENT'}:raise ValueError("unknown evidence mode")
        if self.evaluation_consumption not in {'UNKNOWN','CONSUMED','ATTESTED_UNUSED'}:raise ValueError("holdout consumption state")
        if self.dataset_sha256 and not re.fullmatch(r'[0-9a-f]{64}',self.dataset_sha256):raise ValueError("invalid dataset digest")
        dates=[self.train_start,self.train_end,self.development_end]
        if self.evaluation_end:dates.append(self.evaluation_end)
        parsed=[datetime.fromisoformat(x) for x in dates]
        if any(d.tzinfo is None for d in parsed) or any(a>=b for a,b in zip(parsed,parsed[1:])):raise ValueError("noncausal study calendar")
        if self.min_daily_periods<120 or self.min_regime_periods<30 or self.bootstrap_samples<500:
            raise ValueError("underpowered protocol defaults cannot be silently weakened")
        if not self.block_lengths or any(b<1 or b*3>self.min_daily_periods for b in self.block_lengths):raise ValueError("invalid bootstrap block design")
        if self.label_horizon_bars<1 or self.embargo_bars<self.label_horizon_bars:raise ValueError("embargo below declared horizon")
        if not 0<self.alpha<=.05 or not 0<self.max_pbo<=.3 or not .95<=self.min_dsr_probability<1:raise ValueError("invalid acceptance thresholds")
        if not {0,24,36,50}.issubset(self.roundtrip_cost_bps) or self.primary_cost_bps not in self.roundtrip_cost_bps or self.primary_cost_bps==0:raise ValueError("cost protocol incomplete")
        if len(set(self.seeds))<5:raise ValueError("multi-seed protocol requires at least five seeds")

    def fingerprint(self) -> str:
        self.validate()
        return digest(asdict(self))

    def readiness(self) -> dict[str,Any]:
        self.validate()
        missing=[]
        for key in ('venue','dataset_id','dataset_sha256','evaluation_end','registered_at'):
            if not getattr(self,key):missing.append(key.upper()+'_NOT_LOCKED')
        if self.evaluation_consumption!='ATTESTED_UNUSED':missing.append('UNCONSUMED_EVALUATION_NOT_ESTABLISHED')
        if self.registered_at:
            at=datetime.fromisoformat(self.registered_at)
            if at.tzinfo is None or at>datetime.now(timezone.utc):missing.append('INVALID_REGISTRATION_TIME')
            if self.mode=='PROSPECTIVE_INDEPENDENT' and at>=datetime.fromisoformat(self.development_end):
                missing.append('REGISTRATION_AFTER_EVALUATION_START')
        missing += [trial+'_MODEL_NOT_LOCKED' for trial in self.trial_ids if trial not in self.model_hashes]
        return {'status':'BLOCKED' if missing else 'PRE_REGISTERED_DESIGN_PENDING_EVIDENCE',
                'evidence_state':'ENGINEERING','protocol_sha256':self.fingerprint(),'reasons':missing,
                'execution_authorized':False,'v51_result':None}


def arm_readiness(arm: str, *, baseline_economic_gate: str,
                  rl_protocol_locked: bool=False, pit_text_available: bool=False,
                  rl_economic_gate: str='BLOCKED') -> dict[str,str]:
    if arm.startswith('B2') and (baseline_economic_gate!='PASS' or not rl_protocol_locked):
        return {'status':'BLOCKED','reason':'SIMPLER_BASELINE_AND_LOCKED_RL_PROTOCOL_REQUIRED'}
    if arm.startswith('B3') and (baseline_economic_gate!='PASS' or not rl_protocol_locked or rl_economic_gate!='PASS' or not pit_text_available):
        return {'status':'BLOCKED','reason':'RL_ACCEPTANCE_AND_PIT_TEXT_REQUIRED'}
    return {'status':'ELIGIBLE_FOR_OFFLINE_RESEARCH','reason':'NO_EXECUTION_PROMOTION'}


def gap_matrix(evidence: dict[str,dict[str,Any]] | None=None) -> list[dict[str,Any]]:
    evidence=evidence or {}
    return [{'id':key,'question':value[0],'role':value[1],
             'status':evidence.get(key,{}).get('status','BLOCKED'),
             'evidence':evidence.get(key,{}).get('artifact'),
             'reason':evidence.get(key,{}).get('reason','REQUIRED_EVIDENCE_NOT_COLLECTED')}
            for key,value in GAPS.items()]
