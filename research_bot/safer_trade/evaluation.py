"""Paired portfolio inference and search-aware gates, without model promotion."""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import math
from typing import Any,Mapping

import numpy as np
import pandas as pd

from research_bot.regime_robustness import probability_backtest_overfitting,deflated_sharpe_probability
from .protocol import StudyProtocol


@dataclass(frozen=True)
class CostBreakdown:
    """All entries are fractions of portfolio NAV, not raw fee currency."""
    fee: float
    spread: float
    impact_latency: float
    funding: float
    inference: float
    spread_in_execution_price: bool = False
    execution_cost_in_gross: bool = False

    def net(self,gross: float) -> float:
        values=(gross,self.fee,self.spread,self.impact_latency,self.funding,self.inference)
        if not all(math.isfinite(v) for v in values):raise ValueError("cost component unavailable / nonfinite")
        if min(self.fee,self.spread,self.impact_latency,self.inference)<0:raise ValueError("negative explicit non-funding cost")
        if self.spread_in_execution_price and self.spread!=0:raise ValueError("spread double counting")
        if self.execution_cost_in_gross and (self.spread!=0 or self.impact_latency!=0):raise ValueError("execution cost double counting")
        return gross-self.fee-self.spread-self.impact_latency-self.funding-self.inference


def stress_returns(gross: pd.DataFrame,turnover: pd.DataFrame, *, roundtrip_bps: float,
                   funding: pd.DataFrame,inference: pd.DataFrame) -> pd.DataFrame:
    """Replace explicit execution fees/costs by one declared one-way scenario.

    gross is frictionless mark-to-market NAV return. Funding/inference are kept;
    already charged fees/spread must not also be passed into this function.
    """
    if not math.isfinite(roundtrip_bps) or roundtrip_bps<0:raise ValueError("invalid stress cost")
    for other in (turnover,funding,inference):
        if not gross.index.equals(other.index) or not gross.columns.equals(other.columns):raise ValueError("cost panel identity mismatch")
        if not np.isfinite(other.to_numpy(float)).all():raise ValueError("cost panel incomplete")
    if not np.isfinite(gross.to_numpy(float)).all() or (turnover<0).any().any() or (inference<0).any().any():raise ValueError("invalid gross / turnover / inference")
    return gross-turnover*(roundtrip_bps/2/10000)-funding-inference


def validate_matrix(matrix: pd.DataFrame,trial_ids: tuple[str,...]) -> None:
    if matrix.empty or len(set(trial_ids))!=len(trial_ids) or list(matrix.columns)!=list(trial_ids):
        raise ValueError("complete ordered registered trial matrix required; losers cannot be dropped")
    if not isinstance(matrix.index,pd.DatetimeIndex) or matrix.index.tz is None:
        raise ValueError("timezone-aware daily portfolio index required")
    if not matrix.index.is_monotonic_increasing or matrix.index.has_duplicates:
        raise ValueError("nonunique / unsorted portfolio time")
    if len(matrix)>1 and not (matrix.index.to_series().diff().dropna()==pd.Timedelta(days=1)).all():
        raise ValueError("daily calendar coverage missing; never silently align/drop rows")
    if not np.isfinite(matrix.to_numpy(float)).all():raise ValueError("missing trial results; zero imputation forbidden")
    if (matrix<-1).any().any():raise ValueError("return below minus 100 percent")


def portfolio_metrics(values: pd.Series, *, trade_pnl: list[float] | None=None) -> dict[str,Any]:
    r=values.to_numpy(float)
    if not len(r) or not np.isfinite(r).all() or (r<-1).any():raise ValueError("invalid portfolio returns")
    wealth=np.r_[1.,np.cumprod(1+r)]
    peak=np.maximum.accumulate(wealth)
    std=float(np.std(r,ddof=1)) if len(r)>1 else 0.
    downside=float(np.sqrt(np.mean(np.minimum(r,0)**2)))
    var=float(np.quantile(-r,.95))
    tail=(-r)[-r>=var]
    pf=None
    if trade_pnl is not None:
        p=np.asarray(trade_pnl,dtype=float)
        if not np.isfinite(p).all():raise ValueError("invalid trade PnL")
        loss=-p[p<0].sum()
        pf=float(p[p>0].sum()/loss) if loss>0 else None
    mdd=float(np.max(1-wealth/peak))
    annualized=float(wealth[-1]**(365/len(r))-1) if wealth[-1]>0 else -1.
    return {'n_daily_periods':len(r),'n_trades':None if trade_pnl is None else len(trade_pnl),
        'mean_net':float(r.mean()),'hit_rate_daily':float(np.mean(r>0)),
        'sharpe':float(r.mean()/std*np.sqrt(365)) if std>0 else None,
        'sortino':float(r.mean()/downside*np.sqrt(365)) if downside>0 else None,
        'max_drawdown':mdd,'net_total_return':float(wealth[-1]-1),
        'calmar':annualized/mdd if mdd>0 else None,'cvar_95':float(tail.mean()),
        'profit_factor':pf,'profit_factor_status':'NOT_RUN_NO_TRADE_LEDGER' if trade_pnl is None else 'COMPUTED_OR_UNDEFINED',
        'annualization':'365_DAILY_CRYPTO_PERIODS_DESCRIPTIVE_NOT_IID_PROOF'}


def holm_adjust(p_values: Mapping[str,float]) -> dict[str,float]:
    if any(not math.isfinite(v) or not 0<=v<=1 for v in p_values.values()):raise ValueError("invalid p-value")
    ordered=sorted(p_values,key=lambda key:(p_values[key],key))
    result={};running=0.
    for rank,key in enumerate(ordered):
        running=max(running,min(1.,p_values[key]*(len(ordered)-rank)))
        result[key]=running
    return result


def paired_block_test(candidate: pd.Series,baseline: pd.Series, *, block: int,
                      samples: int,alpha: float=.05,seed: int=42) -> dict[str,Any]:
    if not candidate.index.equals(baseline.index):raise ValueError("paired timestamps differ")
    c,b=candidate.to_numpy(float),baseline.to_numpy(float)
    if not np.isfinite(c).all() or not np.isfinite(b).all():raise ValueError("incomplete pair")
    if block<1 or len(c)<3*block or samples<500:return {'status':'UNDERPOWERED','n':len(c)}
    diff=c-b;observed=float(diff.mean())
    if np.std(diff,ddof=1)==0:
        return {'status':'INCONCLUSIVE','n':len(c),'reason':'DEGENERATE_PAIRED_DIFFERENCE',
                'mean_diff':observed,'p_value':None,'ci':None}
    rng=np.random.default_rng(seed)
    means=[];null_means=[]
    centered=diff-observed
    for _ in range(samples):
        starts=rng.integers(0,len(diff),size=math.ceil(len(diff)/block))
        idx=np.concatenate([(start+np.arange(block))%len(diff) for start in starts])[:len(diff)]
        means.append(float(diff[idx].mean()));null_means.append(float(centered[idx].mean()))
    return {'status':'COMPUTED','n':len(diff),'block_days':block,'samples':samples,
            'mean_diff':observed,'ci':np.quantile(means,[alpha/2,1-alpha/2]).tolist(),
            'p_value':float((1+np.sum(np.asarray(null_means)>=observed))/(samples+1)),
            'null':'CENTERED_CIRCULAR_BLOCK_ONE_SIDED_MEAN_DIFFERENCE'}


def selection_diagnostics(matrix: pd.DataFrame, *, selected_trial: str | None,
                          num_search_trials: int | None=None,
                          training_trial_sharpe_std: float | None=None,
                          iid_moment_assumptions_validated: bool=False) -> dict[str,Any]:
    """Use repo-native CSCV/DSR with conservative validity guards."""
    if matrix.shape[1]==1:
        pbo={'status':'NOT_APPLICABLE','reason':'ONE_VARIANT','value':None}
    elif len(matrix)<120:
        pbo={'status':'UNDERPOWERED','value':None}
    else:
        # The legacy CSCV implementation assumes nondegenerate untied ranks.
        # Do not silently select an arbitrary tied winner or discard a trial.
        groups=np.array_split(np.arange(len(matrix)),6)
        invalid=False
        for subset in combinations(range(6),3):
            idx=np.concatenate([groups[i] for i in subset]);part=matrix.iloc[idx]
            std=part.std(ddof=1)
            sr=part.mean()/std.replace(0,np.nan)
            if not np.isfinite(sr.to_numpy()).all() or sr.duplicated().any():invalid=True;break
        if invalid:pbo={'status':'INCONCLUSIVE','value':None,'reason':'DEGENERATE_OR_TIED_CSCV_RANKS'}
        else:
            legacy=probability_backtest_overfitting(matrix,n_groups=6)
            pbo={'status':'COMPUTED','value':legacy['pbo'],'splits':legacy['splits'],
                 'method':'REPO_NATIVE_CSCV_6_GROUPS_NOT_CPCV'}
    dsr={'status':'NOT_RUN','value':None,'reason':'DEVELOPMENT_SELECTION_AND_DEPENDENCE_ASSUMPTIONS_REQUIRED'}
    if (selected_trial in matrix.columns and num_search_trials is not None
            and num_search_trials>=matrix.shape[1] and num_search_trials>1
            and training_trial_sharpe_std is not None and math.isfinite(training_trial_sharpe_std)
            and training_trial_sharpe_std>0 and iid_moment_assumptions_validated):
        probability=deflated_sharpe_probability(matrix[selected_trial],num_search_trials,training_trial_sharpe_std)
        if math.isfinite(probability):
            dsr={'status':'DESCRIPTIVE_IID_APPROXIMATION','value':probability,
                 'reason':'SERIAL_DEPENDENCE_VALIDATION_REQUIRED_FOR_INFERENCE',
                 'selected_trial':selected_trial,'num_search_trials':num_search_trials}
    return {'pbo':pbo,'dsr':dsr}


def evaluate_panel(matrix: pd.DataFrame, *, protocol: StudyProtocol,
                   selected_trial: str | None=None, regimes: pd.Series | None=None,
                   sample_role: str='DEVELOPMENT') -> dict[str,Any]:
    protocol.validate();validate_matrix(matrix,protocol.trial_ids)
    if sample_role not in {'DEVELOPMENT','HOLDOUT'}:raise ValueError("unknown sample role")
    if selected_trial is not None and selected_trial not in protocol.trial_ids:raise ValueError("selection identity")
    metrics={name:portfolio_metrics(matrix[name]) for name in matrix}
    paired={}
    p_values={}
    for name in matrix:
        if name==protocol.baseline_id:continue
        paired[name]={}
        for block in protocol.block_lengths:
            test=paired_block_test(matrix[name],matrix[protocol.baseline_id],block=block,
                samples=protocol.bootstrap_samples,alpha=protocol.alpha,seed=protocol.seeds[0])
            paired[name][str(block)]=test
            if test.get('p_value') is not None:p_values[name+'|'+str(block)]=test['p_value']
    adjusted=holm_adjust(p_values)
    decisions={}
    selection=(selection_diagnostics(matrix,selected_trial=selected_trial) if sample_role=='DEVELOPMENT'
               else {'pbo':{'status':'NOT_RUN','value':None,'reason':'CSCV_REQUIRES_DEVELOPMENT_MATRIX'},
                     'dsr':{'status':'NOT_RUN','value':None,'reason':'LOCKED_DEVELOPMENT_SELECTION_REQUIRED'}})
    for name in matrix:
        if name==protocol.baseline_id:decisions[name]='BASELINE';continue
        tests=paired[name]
        if len(matrix)<protocol.min_daily_periods:state='UNDERPOWERED'
        elif metrics[name]['mean_net']<=0:state='REJECTED_NONPOSITIVE_MEAN_NET'
        elif any(x.get('status')!='COMPUTED' for x in tests.values()):state='INCONCLUSIVE'
        elif any(x['ci'][0]<=0 or adjusted[name+'|'+block]>protocol.alpha for block,x in tests.items()):state='REJECTED_NO_ROBUST_PAIRED_EDGE'
        elif selection['pbo']['status']=='COMPUTED' and selection['pbo']['value']>=protocol.max_pbo:state='REJECTED_PBO'
        else:state='INCONCLUSIVE_SELECTION_AND_FRESH_EVIDENCE_REQUIRED'
        decisions[name]=state
    regime_report={}
    if regimes is not None:
        if not regimes.index.equals(matrix.index) or regimes.isna().any():raise ValueError("regime alignment / missing labels")
        for regime in sorted(regimes.unique()):
            part=matrix.loc[regimes==regime]
            regime_report[str(regime)]={'status':'DESCRIPTIVE' if len(part)>=protocol.min_regime_periods else 'UNDERPOWERED',
                'n_daily_periods':len(part),'metrics':{name:portfolio_metrics(part[name]) for name in part}}
    return {'evidence_state':'HISTORICAL_DIAGNOSTIC','protocol_sha256':protocol.fingerprint(),
        'metrics':metrics,'paired_tests':paired,'holm_adjusted_p':adjusted,'selection':selection,
        'regimes':regime_report,'decisions':decisions,'execution_authorized':False,
        'research_status':'NO_MODEL_PROMOTED','n_daily_periods':len(matrix),
        'sample_role':sample_role,'v51_scientific_result':None}
