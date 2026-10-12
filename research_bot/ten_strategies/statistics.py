"""Search-aware return diagnostics. DSR and PBO are never classifier scores."""
from __future__ import annotations
from itertools import combinations
from math import comb
from statistics import NormalDist
import numpy as np
import pandas as pd
from scipy.stats import skew,kurtosis,rankdata
from .protocol import TenConfig,IDS

def _array(values: object) -> np.ndarray:
    x=np.asarray(values,dtype=float)
    if x.ndim!=2 or len(x)<2 or x.shape[1]<1 or not np.isfinite(x).all() or np.any(x<=-1):raise ValueError("finite aligned net returns > -1 required")
    return x

def daily_returns(matrix: pd.DataFrame) -> pd.DataFrame:
    if tuple(matrix.columns)!=IDS or not isinstance(matrix.index,pd.DatetimeIndex) or matrix.index.tz is None:
        raise ValueError("ordered S1..S10 UTC return clock required")
    if matrix.index.has_duplicates or not matrix.index.is_monotonic_increasing or (len(matrix)>1 and not matrix.index.to_series().diff().iloc[1:].eq(pd.Timedelta(hours=1)).all()):
        raise ValueError("continuous unique hourly return clock required")
    _array(matrix)
    # Right-labelled daily intervals (D-1,D], so the midnight close is not moved backwards.
    g=matrix.resample("1D",label="right",closed="right",origin="epoch")
    return g.apply(lambda x:np.prod(1+x)-1).loc[g.size().eq(24)]

def pbo_cscv(matrix: np.ndarray,blocks: int=8,guard: int=2,min_rows: int=40) -> dict:
    x=_array(matrix);n,m=x.shape
    if m<2:return {"status":"NOT_APPLICABLE","reason":"ONE_CONFIGURATION"}
    if blocks<2 or blocks%2 or type(guard) is not int or guard<0:raise ValueError("even CSCV blocks and nonnegative guard required")
    groups=np.array_split(np.arange(n),blocks)
    if any(len(g)-2*guard<min_rows for g in groups):return {"status":"NOT_RUN","reason":"INSUFFICIENT_BLOCK_SUPPORT"}
    groups=[g[guard:len(g)-guard if guard else None] for g in groups]
    losses=[];logits=[]
    for selected in combinations(range(blocks),blocks//2):
        train=x[np.concatenate([groups[i] for i in selected])]
        test=x[np.concatenate([groups[i] for i in range(blocks) if i not in selected])]
        a,b=train.std(axis=0,ddof=1),test.std(axis=0,ddof=1)
        if np.any(a<=1e-15) or np.any(b<=1e-15):return {"status":"NOT_RUN","reason":"DEGENERATE_VARIANT_OR_SPLIT"}
        sr=train.mean(axis=0)/a;out=test.mean(axis=0)/b
        winners=np.flatnonzero(np.isclose(sr,sr.max(),rtol=0,atol=1e-12))
        ranks=rankdata(out,method="average")/(m+1) # Bailey et al. relative rank, NOT rank / M.
        omega=ranks[winners];lam=np.log(omega/(1-omega))
        losses.append(float(np.mean(lam<=0)));logits.extend(lam.tolist())
    return {"status":"DIAGNOSTIC_COMPUTED","method":"BAILEY_CSCV","pbo":float(np.mean(losses)),
        "splits":len(losses),"expected_splits":comb(blocks,blocks//2),"logits":logits,
        "ties":"average OOS rank; average over tied IS maxima","guard_days":guard,
        "scope":"registered family only; not proof of clean prior search or prospective evaluation"}

def dsr_bailey(returns: np.ndarray,trial_count: int,trial_sharpe_sd: float,min_rows: int=1000) -> dict:
    x=np.asarray(returns,dtype=float)
    if x.ndim!=1 or not np.isfinite(x).all() or np.any(x<=-1):raise ValueError("finite net return series required")
    if type(trial_count) is not int or trial_count<2 or not np.isfinite(trial_sharpe_sd) or trial_sharpe_sd<=0:
        return {"status":"NOT_RUN","reason":"MISSING_TRIAL_ACCOUNTING_OR_DISPERSION"}
    if len(x)<min_rows or x.std(ddof=1)<=1e-15:return {"status":"NOT_RUN","reason":"INSUFFICIENT_OR_CONSTANT_RETURNS"}
    sr=float(x.mean()/x.std(ddof=1));s=float(skew(x,bias=False));k=float(kurtosis(x,fisher=False,bias=False))
    normal=NormalDist();gamma=.5772156649015329
    threshold=trial_sharpe_sd*((1-gamma)*normal.inv_cdf(1-1/trial_count)+gamma*normal.inv_cdf(1-1/(trial_count*np.e)))
    variance=1-s*sr+(k-1)*sr*sr/4
    if not np.isfinite(variance) or variance<=0:return {"status":"NOT_RUN","reason":"INVALID_MOMENT_DENOMINATOR"}
    value=normal.cdf((sr-threshold)*np.sqrt(len(x)-1)/np.sqrt(variance))
    return {"status":"IID_DIAGNOSTIC_ONLY","method":"BAILEY_LDP_EQ2","probability":value,
        "per_period_sharpe":sr,"selection_threshold":threshold,"n":len(x),"trials":trial_count,
        "skew":s,"pearson_kurtosis":k,"assumptions":"IID-style sampling and declared complete trial dispersion"}

def diagnostics(matrix: pd.DataFrame,config: TenConfig=TenConfig()) -> dict:
    daily=daily_returns(matrix)
    pbo=pbo_cscv(daily.to_numpy(),config.pbo_blocks,config.boundary_guard_days,config.min_block_days) if len(daily)>1 else {"status":"NOT_RUN","reason":"INSUFFICIENT_DAILY_RETURNS"}
    per_variant={}
    if len(daily)>1:
        sd=daily.std(ddof=1)
        sr=daily.mean()/sd.replace(0,np.nan);dispersion=float(sr.std(ddof=1))
        for strategy in IDS:
            result=dsr_bailey(daily[strategy].to_numpy(),10,dispersion,config.min_daily)
            per_variant[strategy]=result
    else:per_variant={s:{"status":"NOT_RUN","reason":"INSUFFICIENT_DAILY_RETURNS"} for s in IDS}
    # Prior search is explicitly unknown; a family-only value cannot become an official selected-strategy DSR.
    return {"daily_rows":len(daily),"pbo_status":pbo["status"],"pbo":pbo,
        "dsr_status":"NOT_RUN_UNKNOWN_PRIOR_SEARCH_AND_DEPENDENCE","family_dsr_iid_diagnostics":per_variant,
        "inference":"NO_PAPER_GRADUATION; family diagnostics do not establish alpha",
        "prior_search_complete":False,"all_10_columns_retained":True}

def rejection_gates(report: dict,stats: dict,config: TenConfig=TenConfig(),synthetic: bool=False) -> dict:
    gates={}
    for s in IDS:
        r=report["results"][s];reasons=[]
        if synthetic:reasons.append("R1_SYNTHETIC_NOT_MARKET_EVIDENCE")
        reasons.append("R2_NO_CLEAN_PREREGISTERED_HOLDOUT")
        if r["closed_trades"]<config.min_trades:reasons.append("R3_INSUFFICIENT_TRADES")
        if float(r["net_total_return"])<=0:reasons.append("R4_NONPOSITIVE_NET_PERFORMANCE")
        reasons.append("R5_MEASURED_EXECUTION_COSTS_UNVERIFIED")
        reasons.append("R6_PRIOR_SEARCH_AND_DSR_ASSUMPTIONS_UNBOUND")
        if stats["pbo"].get("pbo") is None:reasons.append("R7_PBO_UNAVAILABLE")
        elif stats["pbo"]["pbo"]>.10:reasons.append("R7_PBO_ABOVE_DECLARED_SCREEN")
        reasons.append("R8_FIRST_SEEN_AND_PORTFOLIO_RISK_NOT_VALIDATED")
        gates[s]={"status":"FAIL" if "R4_NONPOSITIVE_NET_PERFORMANCE" in reasons else "INCONCLUSIVE",
            "reason_codes":reasons,"paper_candidate":False}
    return gates
