"""Selection-bias diagnostics: CSCV-style PBO and Deflated Sharpe Ratio.

PBO: six consecutive chronological *training-only* partitions, select best
candidate by in-sample Sharpe for each 3/3 CSCV split, measure its test rank.
Every split excludes 96 observations around segment boundaries as embargo,
but this does NOT establish event-time CPCV for trades with unbounded holding.
DSR: 2024 final holdout, N_eff estimated from cross-strategy correlations;
adjusts for skew/kurt and effective bar count from serial correlation.
These are diagnostics; invalid/insufficient evidence returns None, not 1.0.
"""
from __future__ import annotations

from itertools import combinations
from math import e
import numpy as np
from scipy.stats import norm, skew, kurtosis


def _score(x):
    x=np.asarray(x,dtype=float)
    if len(x)<100 or not np.isfinite(x).all():return -np.inf
    s=x.std(ddof=1)
    return float(x.mean()/s) if s>1e-12 else -np.inf


def cscv_pbo_6(
    config_train_returns:dict[int,np.ndarray],
    *,embargo_bars:int=96,
):
    keys=sorted(config_train_returns)
    if len(keys)<3:return {"pbo":None,"valid":False,"reason":"LESS_THAN_THREE_CANDIDATES"}
    shape={len(np.asarray(x)) for x in config_train_returns.values()}
    if len(shape)!=1:return {"pbo":None,"valid":False,"reason":"INCONSISTENT_TRIAL_PERIODS"}
    n=next(iter(shape))
    if n<6*250:return {"pbo":None,"valid":False,"reason":"INSUFFICIENT_TIME_BLOCKS"}
    parts=np.array_split(np.arange(n),6)
    # Remove embargo around group borders on ALL folds. This does not remove
    # every possible overlapping entry/exit event, so not full purged CPCV.
    for j in range(6):
        part=parts[j]
        parts[j]=part[embargo_bars:-embargo_bars]
        if len(parts[j])<200:return {"pbo":None,"valid":False,"reason":"INSUFFICIENT_AFTER_EMBARGO"}
    values={k:np.asarray(v,dtype=float) for k,v in config_train_returns.items()}
    splits=[]
    for chosen in combinations(range(6),3):
        inside=np.concatenate([parts[i] for i in chosen])
        outside=np.concatenate([parts[i] for i in range(6) if i not in chosen])
        train_scores={k:_score(v[inside]) for k,v in values.items()}
        out_scores={k:_score(v[outside]) for k,v in values.items()}
        if not all(np.isfinite(v) for v in [*train_scores.values(),*out_scores.values()]):
            return {"pbo":None,"valid":False,"reason":"UNDEFINED_CSCV_SHARPE"}
        selected=max(keys,key=lambda k:(train_scores[k],-k))
        other=np.array([out_scores[k] for k in keys])
        rank=1+int((other<out_scores[selected]).sum())
        # Explicit tie handling; rank with discrete midpoint among tied arms.
        rank+=.5*int((other==out_scores[selected]).sum()-1)
        percentile=rank/(len(keys)+1)
        splits.append({"selected":selected,"oos_rank_percentile":float(percentile),
                       "logit":float(np.log(percentile/(1-percentile)))})
    value=float(np.mean([v["logit"]<0 for v in splits]))
    return {"pbo":value,"valid":True,"candidate_thresholds":keys,
            "splits":len(splits),"embargo_bars":embargo_bars,
            "diagnostic_only":True,
            "warning":"CSCV is not time-ordered walk-forward or event-time-purged CPCV; trailing holding can exceed embargo",
            "logit_median":float(np.median([v["logit"] for v in splits]))}


def deflated_sharpe_2024(
    selected_returns:np.ndarray,
    trial_train_returns:dict[int,np.ndarray],
):
    """DSR probability on heldout 2024; fail closed if too little information.

    Quantile approximation to expected maximum Sharpe: Bailey/Lopez de Prado,
    JPM (2014) 40(5):94-107, DOI 10.3905/jpm.2014.40.5.094.
    Annualization is deliberately NOT done inside the test statistic.
    """
    x=np.asarray(selected_returns,dtype=float)
    keys=sorted(trial_train_returns)
    if len(x)<1000 or len(keys)<2:return {"dsr":None,"valid":False,"reason":"TOO_LITTLE_DATA_OR_TRIALS"}
    if not np.isfinite(x).all():return {"dsr":None,"valid":False,"reason":"NONFINITE_RETURN"}
    trials=np.column_stack([np.asarray(trial_train_returns[k],dtype=float) for k in keys])
    if not np.isfinite(trials).all():return {"dsr":None,"valid":False,"reason":"NONFINITE_TRIALS"}
    raw_trial_srs=np.array([_score(trials[:,j]) for j in range(len(keys))])
    if not np.isfinite(raw_trial_srs).all():return {"dsr":None,"valid":False,"reason":"UNDEFINED_TRIAL_SHARPE"}
    correlations=np.corrcoef(trials.T)
    mean_cor=float(np.mean(correlations[np.triu_indices(len(keys),1)]))
    if not np.isfinite(mean_cor):return {"dsr":None,"valid":False,"reason":"UNDEFINED_TRIAL_CORRELATION"}
    n_effective_trials=float(np.clip(1+(len(keys)-1)*(1-max(0.,mean_cor)),1.01,len(keys)))
    empirical_spread=float(np.std(raw_trial_srs,ddof=1))
    euler_gamma=0.5772156649015329
    quantile=(1-euler_gamma)*norm.ppf(1-1/n_effective_trials)+euler_gamma*norm.ppf(1-1/(n_effective_trials*e))
    noise_threshold=float(max(0.,empirical_spread*quantile))
    sr=_score(x)
    if not np.isfinite(sr):return {"dsr":None,"valid":False,"reason":"UNDEFINED_HOLDOUT_SHARPE"}
    # Effective sample size reduction for lagged serial correlation:
    # sum only positive autocorrelation in first 96 M15 bar lags; conservative.
    center=x-x.mean()
    denominator=float(center@center)
    auto=[]
    for k in range(1,min(97,len(x)//4)):
        a=float(np.dot(center[k:],center[:-k])/denominator) if denominator>1e-15 else 0.
        if a>0:auto.append(a)
    effective_n=float(len(x)/(1+2*sum(auto)))
    if effective_n<100:return {"dsr":None,"valid":False,"reason":"LOW_EFFECTIVE_SAMPLES"}
    sk=float(skew(x,bias=False));ku=float(kurtosis(x,fisher=False,bias=False))
    denom=float(1-sk*sr+(ku-1)*sr*sr/4)
    if not np.isfinite(denom) or denom<=0:
        return {"dsr":None,"valid":False,"reason":"INVALID_DSR_VARIANCE"}
    z=float((sr-noise_threshold)*np.sqrt(effective_n-1)/np.sqrt(denom))
    return {"dsr":float(norm.cdf(z)),"valid":True,"n_trials":len(keys),
            "n_effective_trials":n_effective_trials,
            "n_effective_observations":effective_n,
            "observed_unannualized_sharpe":sr,
            "expected_noise_max_sharpe":noise_threshold,
            "skew":sk,"pearson_kurtosis":ku,
            "warning":"Approximate DSR under correlated trials/returns; input strategy candidates selected in 2022-23"}
