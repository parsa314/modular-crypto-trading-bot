"""Strict closed-bar and multi-timeframe boundary for independent research."""
from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from research_bot.features import add_features, FEATURE_COLUMNS
from research_bot.point_in_time import point_in_time_asof_join, assert_no_future_availability
from .contracts import aware


def verified_file(path: Path, sha256: str) -> bytes:
    content=path.read_bytes()
    if len(sha256)!=64 or hashlib.sha256(content).hexdigest()!=sha256:
        raise ValueError("dataset artifact checksum mismatch")
    return content


def closed_frame(frame: pd.DataFrame, *, as_of: datetime, timeframe: str) -> pd.DataFrame:
    aware(as_of)
    required={'timestamp','available_at','symbol','venue','open','high','low','close','volume'}
    if not required.issubset(frame.columns):raise ValueError("OHLCV identity / PIT fields missing")
    x=frame.copy()
    for col in ('timestamp','available_at'):
        x[col]=pd.to_datetime(x[col],utc=True,errors='raise')
        if x[col].isna().any():raise ValueError("missing timestamp")
    delta=pd.Timedelta(timeframe)
    if delta<=pd.Timedelta(0):raise ValueError("invalid timeframe")
    # Do not let a future price affect past features or fit any transformer.
    x=x.loc[x.available_at<=pd.Timestamp(as_of)].copy()
    if x.empty:raise ValueError("DATA_UNAVAILABLE: no closed available bars")
    if (x.available_at<x.timestamp+delta).any():raise ValueError("bar visible before close")
    if x.venue.astype(str).str.upper().str.contains('KRAKEN').any():raise ValueError("sealed Kraken holdout")
    if x.duplicated(['venue','symbol','timestamp']).any():raise ValueError("duplicate bar identity")
    if x[['symbol','venue']].isna().any().any() or (x[['symbol','venue']]=='').any().any():
        raise ValueError("missing market identity")
    for _,g in x.groupby(['venue','symbol'],sort=False):
        if not g.timestamp.is_monotonic_increasing:raise ValueError("non-monotonic bars")
        if len(g)>1 and not (g.timestamp.diff().dropna()==delta).all():
            raise ValueError("coverage gap: no silent bar filling")
    cols=['open','high','low','close','volume']
    x[cols]=x[cols].apply(pd.to_numeric,errors='raise')
    if not np.isfinite(x[cols].to_numpy(float)).all():raise ValueError("nonfinite OHLCV")
    if (x[cols[:4]]<=0).any().any() or (x.volume<0).any():raise ValueError("invalid price / volume")
    if (x.low>x[['open','close']].min(axis=1)).any() or (x.high<x[['open','close']].max(axis=1)).any() or (x.low>x.high).any():
        raise ValueError("inconsistent OHLC")
    return x.sort_values(['timestamp','venue','symbol']).reset_index(drop=True)


def causal_features(frame: pd.DataFrame, *, as_of: datetime, timeframe: str) -> pd.DataFrame:
    bars=closed_frame(frame,as_of=as_of,timeframe=timeframe)
    parts=[]
    for _,group in bars.groupby(['venue','symbol'],sort=False):
        parts.append(add_features(group.reset_index(drop=True)))
    return pd.concat(parts,ignore_index=True).sort_values(['timestamp','venue','symbol']).reset_index(drop=True)


def join_higher_timeframe(decisions: pd.DataFrame, htf_features: pd.DataFrame,
                          *, tolerance: str='4h') -> pd.DataFrame:
    for frame in (decisions,htf_features):
        if not {'venue','symbol'}.issubset(frame.columns):raise ValueError("HTF join identity missing")
    if not {'timestamp','available_at'}.issubset(htf_features.columns):raise ValueError("HTF timing missing")
    opened=pd.to_datetime(htf_features['timestamp'],utc=True,errors='raise')
    available=pd.to_datetime(htf_features['available_at'],utc=True,errors='raise')
    if opened.isna().any() or available.isna().any() or (available<opened+pd.Timedelta('4h')).any():
        raise ValueError("HTF bar available before close")
    result=point_in_time_asof_join(decisions,htf_features,by=['venue','symbol'],
        decision_time_col='decision_time',available_time_col='available_at',
        tolerance=tolerance,feature_prefix='htf_')
    assert_no_future_availability(result,decision_time_col='decision_time')
    return result


def purged_walk_forward(events: pd.DataFrame, *, min_train_times: int,
                        test_times: int, embargo: pd.Timedelta) -> list[tuple[np.ndarray,np.ndarray]]:
    """Expanding splits on global decision time; purge actual label intervals."""
    required={'decision_time','label_end','feature_available_at'}
    if not required.issubset(events.columns):raise ValueError("label interval / availability missing")
    if min_train_times<2 or test_times<1 or embargo<pd.Timedelta(0):raise ValueError("invalid split contract")
    x=events.copy()
    for col in required:x[col]=pd.to_datetime(x[col],utc=True,errors='raise')
    if x[list(required)].isna().any().any() or (x.label_end<x.decision_time).any() or (x.feature_available_at>x.decision_time).any():
        raise ValueError("future feature or invalid label interval")
    times=x.decision_time.sort_values().unique()
    folds=[]
    for start in range(min_train_times,len(times),test_times):
        test_index=np.flatnonzero(x.decision_time.isin(times[start:start+test_times]))
        cutoff=pd.Timestamp(times[start])-embargo
        train_index=np.flatnonzero((x.decision_time<cutoff)&(x.label_end<cutoff))
        if len(train_index) and len(test_index):folds.append((train_index,test_index))
    if not folds:raise ValueError("UNDERPOWERED: no purged folds")
    return folds


def freeze_regime_thresholds(training: pd.DataFrame) -> dict[str,float]:
    """Train-only fixed thresholds; no future HMM smoothing or refit on test."""
    if len(training)<30:raise ValueError("UNDERPOWERED regime training")
    values=training[['mom_24','vol_24']].dropna()
    if len(values)<30 or not np.isfinite(values.to_numpy(float)).all():raise ValueError("invalid regime training")
    return {'trend_lower':float(values.mom_24.quantile(.33)),
            'trend_upper':float(values.mom_24.quantile(.67)),
            'vol_median':float(values.vol_24.median())}


def classify_regimes(features: pd.DataFrame, thresholds: dict[str,float]) -> pd.Series:
    trend=np.where(features.mom_24>thresholds['trend_upper'],'UP',
                   np.where(features.mom_24<thresholds['trend_lower'],'DOWN','SIDEWAYS'))
    volatility=np.where(features.vol_24>thresholds['vol_median'],'HIGH_VOL','LOW_VOL')
    result=pd.Series([a+'|'+b for a,b in zip(trend,volatility)],index=features.index)
    return result.mask(features[['mom_24','vol_24']].isna().any(axis=1),'UNAVAILABLE')
