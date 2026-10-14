"""Three-state Gaussian HMM market-regime research (NOT a trading oracle).

States are unsupervised; TREND/RANGE/VOLATILE are deterministic *post-fit
descriptions* of fitted centroids, not supervised ground truth.
The HMM and RobustScaler are fit only with 2022-2023 bars. During 2024
prediction the state posterior is computed by a strictly FORWARD recursion
using observations available through that closed candle, never Viterbi /
forward-backward smoothed hindsight states.

Data and model are for noncommercial academic research ONLY.
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd
from scipy.special import logsumexp


TRAIN_END = pd.Timestamp("2024-01-01T00:00:00Z")
PERIOD = pd.Timedelta(minutes=15)
MIN_WINDOW=20


def atr_series(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder-style exponentially-smoothed true range using historical OHLC."""
    prev=frame.close.shift(1)
    tr=pd.concat([frame.high-frame.low,(frame.high-prev).abs(),
                  (frame.low-prev).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/period,adjust=False,min_periods=period).mean()


def causal_regime_features(frame: pd.DataFrame) -> pd.DataFrame:
    """At t requires only closed rows through t; resets features across gaps."""
    if not frame.timestamp.is_monotonic_increasing or not frame.timestamp.is_unique:
        raise ValueError("NONMONOTONIC_CANDLES")
    close=frame.close
    atr=atr_series(frame)
    ret=np.log(close/close.shift(1))
    absret=ret.abs().rolling(MIN_WINDOW,min_periods=MIN_WINDOW).mean()
    movement=ret.abs().rolling(MIN_WINDOW,min_periods=MIN_WINDOW).sum()
    efficiency=(np.log(close/close.shift(MIN_WINDOW)).abs()/movement.replace(0,np.nan)).clip(0,1)
    atr_pct=(atr/close).clip(lower=1e-12)
    feat=pd.DataFrame({"log_abs_return":np.log(absret.clip(lower=1e-8)),
                       "efficiency":efficiency,
                       "log_atr_pct":np.log(atr_pct)})
    # Each current feature must have a complete rolling clock window.
    complete=(frame.timestamp.diff()==PERIOD).astype(int).rolling(60,min_periods=60).sum()==60
    feat.loc[~complete,:]=np.nan
    return feat


@dataclass
class FittedRegime:
    model: object
    scaler: object
    mapping: dict[int,str]
    features: tuple[str,...]
    fitted_rows: int
    trained_through: str

    def causal_inference(self, df:pd.DataFrame) -> pd.DataFrame:
        """Forward-only state filtering, resetting probability after gaps."""
        x=causal_regime_features(df)
        columns=list(self.features)
        ok=x[columns].notna().all(axis=1).to_numpy()
        output=np.full((len(df),3),np.nan,dtype=float)
        post=np.asarray(self.model.startprob_,dtype=float).copy()
        transition=np.asarray(self.model.transmat_,dtype=float)
        means=np.asarray(self.model.means_,dtype=float)
        variance=np.asarray(self.model.covars_,dtype=float)
        if variance.ndim==3: variance=np.diagonal(variance,axis1=1,axis2=2)
        scaled=np.zeros((len(df),len(columns)),dtype=float)
        if ok.any():
            scaled[ok]=self.scaler.transform(x.loc[ok,columns])
        prev_ok=False
        for i in range(len(df)):
            if not ok[i]:
                prev_ok=False
                post=self.model.startprob_.copy()
                continue
            emission=-0.5*(np.log(2*np.pi*variance).sum(axis=1)+
                            (((scaled[i]-means)**2)/variance).sum(axis=1))
            if prev_ok:
                post=post@transition
            unnormalized=np.log(np.maximum(post,1e-300))+emission
            post=np.exp(unnormalized-logsumexp(unnormalized))
            output[i]=post
            prev_ok=True
        labels=np.full(len(df),"UNAVAILABLE",dtype=object)
        confident=np.zeros(len(df))
        valid_idx=np.flatnonzero(ok)
        for i in valid_idx:
            state=int(np.argmax(output[i]))
            labels[i]=self.mapping[state]
            confident[i]=float(output[i,state])
        # Unsupervised labels before 2024 use *fitted in-sample model parameters*.
        return pd.DataFrame({"regime":labels,"regime_confidence":confident,
                             "hmm_state":np.where(ok,np.nanargmax(np.where(
                                 np.isnan(output),-np.inf,output),axis=1),-1)},
                            index=df.index)


def fit_hmm_2022_2023(df:pd.DataFrame,seed:int=314)->FittedRegime:
    """Fit 3-state HMM on 2022-23 only; withhold ALL 2024 observations."""
    from hmmlearn.hmm import GaussianHMM
    from sklearn.preprocessing import RobustScaler
    if df.timestamp.dt.tz is None or len(df)<10000:
        raise ValueError("INSUFFICIENT_TIMESTAMPED_TRAINING_DATA")
    feature=causal_regime_features(df)
    valid=(df.timestamp<TRAIN_END)&feature.notna().all(axis=1)
    train=feature.loc[valid].copy()
    if len(train)<10000: raise ValueError("INSUFFICIENT_CAUSAL_HMM_TRAIN")
    scaler=RobustScaler(quantile_range=(20,80)).fit(train)
    x=scaler.transform(train)
    model=GaussianHMM(n_components=3,covariance_type="diag",
                       n_iter=30,tol=0.002,random_state=seed,
                       min_covar=1e-3,implementation="log")
    model.fit(x)
    states=model.predict(x)  # Used ONLY for naming fitted TRAIN centroids.
    stats=[]
    for j in range(3):
        subset=train.iloc[np.flatnonzero(states==j)]
        if len(subset)<50:raise ValueError("COLLAPSED_HMM_REGIME")
        stats.append((j,float(subset.log_atr_pct.median()),
                      float(subset.efficiency.median())))
    # Highest ATR relative to close is VOLATILE; greater signed efficiency
    # of the remaining two is TREND; last = RANGE.
    high=max(stats,key=lambda v:v[1])[0]
    rest=[x for x in stats if x[0]!=high]
    trending=max(rest,key=lambda v:v[2])[0]
    ranging=next(v[0] for v in rest if v[0]!=trending)
    return FittedRegime(model,scaler,
                        {high:"VOLATILE",trending:"TREND",ranging:"RANGE"},
                        tuple(train.columns),len(train),"2023-12-31T23:45:00Z")
