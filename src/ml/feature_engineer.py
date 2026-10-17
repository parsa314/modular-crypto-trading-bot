"""Causal technical features and train-only 3-state Gaussian-HMM filter."""
from __future__ import annotations
import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM
TRAIN_END="2023-01-01"

def build_features(p,btc):
    c=p.close
    previous=c.shift(1)
    tr=pd.concat([(p.high-p.low),(p.high-previous).abs(),
                  (p.low-previous).abs()],axis=1).max(axis=1)
    atr=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    r=c.diff()
    ga=r.clip(lower=0).ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    lo=(-r.clip(upper=0)).ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    rs=ga/lo.replace(0,np.nan)
    rsi=100-100/(1+rs)
    ema12=c.ewm(span=12,adjust=False).mean()
    ema26=c.ewm(span=26,adjust=False).mean()
    macd=ema12-ema26
    ma20=c.rolling(20,min_periods=20).mean()
    vol20=p.quote_volume.shift(1).rolling(20,min_periods=20).mean()
    dev=c.rolling(20,min_periods=20).std()
    ret=c.pct_change(fill_method=None)
    br=btc.close.pct_change(fill_method=None).reindex(p.index)
    corr=ret.rolling(30,min_periods=30).corr(br)
    daily=pd.DataFrame({
        "atr":atr,"atr_pct":atr/c,"atr20ratio":atr/atr.rolling(20).mean(),
        "rsi":rsi,"vol_ratio":p.quote_volume/vol20,
        "distance_sma50":c/c.rolling(50,min_periods=50).mean()-1,
        "distance_sma200":c/c.rolling(200,min_periods=200).mean()-1,
        "macd_pct":macd/c,
        "bb_position":(c-(ma20-2*dev))/(4*dev.replace(0,np.nan)),
        "corr_btc":corr,
        "donchian55":p.high.shift(1).rolling(55,min_periods=55).max(),
        "donchian20":p.high.shift(1).rolling(20,min_periods=20).max()
    },index=p.index)
    # Never permit gaps to contaminate an indicator through its lookback.
    continuity=p.index.to_series().diff().eq(pd.Timedelta(days=1))
    valid=continuity.rolling(201,min_periods=201).sum().eq(201)
    daily.loc[~valid,:]=np.nan
    return daily

FEATS=["confidence","atr_pct","atr20ratio","vol_ratio","rsi",
       "distance_sma50","distance_sma200","macd_pct","bb_position",
       "corr_btc","hour","day_of_week","btc_volatility"]
def add_btc_regime(data,btc):
    """Fit HMM to TRAIN (2019-22) only, infer causally with one-way forward filter.

    Unlike hmmlearn.predict(), forward-only inference does NOT use later
    observations to revise historical states (no future smoothing leakage).
    """
    btc_vol=btc.close.pct_change(fill_method=None).rolling(20).std()
    obs=btc_vol.dropna().loc[lambda x:x.index<pd.Timestamp(TRAIN_END,tz="UTC")]
    x=np.log(obs.clip(lower=1e-9).to_numpy()).reshape(-1,1)
    if len(x)<100 or np.std(x)<1e-10:
        raise RuntimeError("BTC_HMM_TRAIN_SERIES_TOO_SHORT")
    m=GaussianHMM(n_components=3,covariance_type="diag",
                  n_iter=200,tol=1e-4,random_state=126)
    m.fit(x)
    pri=np.asarray(m.startprob_,dtype=float)
    transition=np.asarray(m.transmat_,dtype=float)
    means=m.means_.ravel()
    var=np.asarray(m.covars_).reshape(3,-1)[:,0]
    if np.any(var<=0) or not np.isfinite(var).all():
        raise RuntimeError("HMM_INVALID_VARIANCE")
    alpha=pri.copy()
    states={}
    for i,(date,value) in enumerate(btc_vol.items()):
        if pd.isna(value):
            continue
        z=float(np.log(max(value,1e-9)))
        loglike=-0.5*(np.log(2*np.pi*var)+(z-means)**2/var)
        likelihood=np.exp(loglike-loglike.max())
        alpha=(pri if not states else alpha@transition)*likelihood
        den=alpha.sum()
        if den<=0 or not np.isfinite(den):
            raise RuntimeError("HMM_FILTER_NUMERIC_FAILURE")
        alpha/=den
        states[date]=int(np.argmax(alpha))
    # No information from the day of the proposed next-day entry.
    times=pd.to_datetime(data.signal_time,utc=True).dt.floor("D")-pd.Timedelta(days=1)
    state=pd.Series(states)
    data["vol_regime"]=state.reindex(times).to_numpy()
    data["vol_regime"]=data["vol_regime"].map(lambda v:f"HMM{int(v)}" if pd.notna(v) else "MISSING")
    data["btc_volatility"]=btc_vol.reindex(times).to_numpy()
    return {"method":"GaussianHMM 3-state, trained on 2019-22 BTC only; forward-only causal filter",
            "trained_points":len(x),"means":means.tolist(),
            "variances":var.tolist(),"transition":transition.tolist()}
