"""Causal CLOSED-bar features. ICT/SMC/Brooks names denote operational proxies."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import numpy as np
import pandas as pd

from ..hypothesis import canonical_json


@dataclass(frozen=True)
class FeatureConfig:
    interval: pd.Timedelta = pd.Timedelta(hours=1)
    higher_multiple: int = 4
    swing_radius: int = 2
    ob_lookback: int = 12

    def __post_init__(self) -> None:
        if not isinstance(self.interval,pd.Timedelta) or pd.isna(self.interval) or self.interval <= pd.Timedelta(0):
            raise ValueError("positive base interval required")
        for name in ("higher_multiple", "swing_radius", "ob_lookback"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 2:
                raise ValueError("integer feature windows >=2 required")


BASE_FAMILIES = {
    "ICHIMOKU": ("ichi_tk", "ichi_kijun_distance", "ichi_cloud_now_width", "ichi_visible_distance", "ichi_causal_lag"),
    "SMC": ("smc_bos", "smc_choch", "smc_ob_side", "smc_ob_distance"),
    "ICT": ("ict_sweep", "ict_fvg", "ict_gap_distance", "ict_hour_sin", "ict_hour_cos"),
    "BROOKS": ("brooks_body", "brooks_close_location", "brooks_inside", "brooks_outside", "brooks_second_entry"),
}


def feature_families() -> dict[str, tuple[str, ...]]:
    return {k: tuple(v) + tuple("htf_" + name for name in v) for k, v in BASE_FAMILIES.items()}


def validate_bars(frame: pd.DataFrame, config: FeatureConfig = FeatureConfig()) -> pd.DataFrame:
    required = ("timestamp", "available_at", "symbol", "open", "high", "low", "close", "volume")
    if any(k not in frame for k in required) or frame.empty:
        raise ValueError("closed OHLCV, symbol and explicit availability required")
    x = frame.loc[:, required].copy().reset_index(drop=True)
    for name in ("timestamp", "available_at"):
        if any(pd.Timestamp(v).tzinfo is None for v in x[name]):
            raise ValueError("timezone-aware source times required")
        x[name] = pd.to_datetime(x[name], utc=True)
    for name in ("open", "high", "low", "close", "volume"):
        if any(isinstance(v, (bool, np.bool_)) for v in x[name]):
            raise ValueError("boolean OHLCV invalid")
        x[name] = pd.to_numeric(x[name], errors="raise")
    values = x[["open", "high", "low", "close", "volume"]].to_numpy(float)
    if not np.isfinite(values).all() or (values[:, :4] <= 0).any() or (values[:, 4] < 0).any():
        raise ValueError("finite positive OHLC and nonnegative volume required")
    if (x.high < x[["open", "close", "low"]].max(axis=1)).any() or (x.low > x[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("invalid OHLC geometry")
    if x.symbol.nunique() != 1 or not isinstance(x.symbol.iloc[0], str) or "_" not in x.symbol.iloc[0]:
        raise ValueError("one explicit asset pair per dataset required")
    if x.timestamp.duplicated().any() or not x.timestamp.is_monotonic_increasing:
        raise ValueError("ordered unique bar closes required; no silent sorting/deduplication")
    if len(x)>1 and not x.timestamp.diff().iloc[1:].eq(config.interval).all():
        raise ValueError("missing or irregular base bars")
    lag = x.available_at - x.timestamp
    if (lag < pd.Timedelta(0)).any() or (lag >= config.interval).any() or not x.available_at.is_monotonic_increasing:
        raise ValueError("ordered availability within the next base close required")
    if (x.timestamp.dt.as_unit('ns').astype('int64') % config.interval.value != 0).any():
        raise ValueError("bar closes must align to UTC interval boundaries")
    return x


def _one_timeframe(x: pd.DataFrame, config: FeatureConfig) -> pd.DataFrame:
    o,h,l,c = (x[k].astype(float) for k in ("open","high","low","close"))
    out = pd.DataFrame(index=x.index)
    tenkan=(h.rolling(9).max()+l.rolling(9).min())/2
    kijun=(h.rolling(26).max()+l.rolling(26).min())/2
    a,b=(tenkan+kijun)/2,(h.rolling(52).max()+l.rolling(52).min())/2
    top=pd.concat([a.shift(26),b.shift(26)],axis=1).max(axis=1,skipna=False)
    out['ichi_tk']=(tenkan-kijun)/c
    out['ichi_kijun_distance']=(c-kijun)/c
    out['ichi_cloud_now_width']=(a-b).abs()/c
    out['ichi_visible_distance']=(c-top)/c
    out['ichi_causal_lag']=c/c.shift(26)-1
    # Pivot belongs to t-k but is first usable only at confirmation t. Never
    # backdate a swing. Breakouts use levels confirmed before the current bar.
    k=config.swing_radius
    sh=h.shift(k).where(h.shift(k).eq(h.rolling(2*k+1).max())).ffill().shift(1)
    sl=l.shift(k).where(l.shift(k).eq(l.rolling(2*k+1).min())).ffill().shift(1)
    up=(c>sh)&(c.shift(1)<=sh);down=(c<sl)&(c.shift(1)>=sl)
    bos=up.astype(int)-down.astype(int)
    previous=bos.replace(0,np.nan).ffill().shift(1).fillna(0)
    out['smc_bos']=bos
    out['smc_choch']=bos.where((bos*previous)<0,0)
    out['ict_sweep']=((l<sl)&(c>sl)).astype(int)-((h>sh)&(c<sh)).astype(int)
    bullish_gap=l>h.shift(2);bearish_gap=h<l.shift(2)
    out['ict_fvg']=np.where(bullish_gap,(l-h.shift(2))/c,np.where(bearish_gap,-(l.shift(2)-h)/c,0))
    ob_side=[];ob_distance=[];gap_distance=[];second=[]
    ob=None;gap=None;direction=0;legs=0;pullback=False
    ema=c.ewm(span=20,adjust=False).mean()
    for i in range(len(x)):
        if ob is not None and ((ob[0]>0 and c.iloc[i]<ob[1]) or (ob[0]<0 and c.iloc[i]>ob[2])): ob=None
        if gap is not None and ((gap[0]>0 and l.iloc[i]<=gap[1]) or (gap[0]<0 and h.iloc[i]>=gap[2])): gap=None
        if bos.iloc[i]:
            sign=int(bos.iloc[i])
            for j in range(i-1,max(-1,i-config.ob_lookback-1),-1):
                if (c.iloc[j]-o.iloc[j])*sign<0:
                    ob=(sign,l.iloc[j],h.iloc[j]);break
        if bullish_gap.iloc[i]:gap=(1,h.iloc[i-2],l.iloc[i])
        elif bearish_gap.iloc[i]:gap=(-1,h.iloc[i],l.iloc[i-2])
        ob_side.append(ob[0] if ob else 0)
        ob_distance.append((c.iloc[i]-(ob[1]+ob[2])/2)/c.iloc[i] if ob else 0)
        gap_distance.append((c.iloc[i]-(gap[1]+gap[2])/2)/c.iloc[i] if gap else 0)
        trend=1 if c.iloc[i]>=ema.iloc[i] else -1
        if trend!=direction:direction=trend;legs=0;pullback=False
        fire=0
        if i:
            retracing=l.iloc[i]<l.iloc[i-1] if trend>0 else h.iloc[i]>h.iloc[i-1]
            recovery=h.iloc[i]>h.iloc[i-1] if trend>0 else l.iloc[i]<l.iloc[i-1]
            if retracing and not pullback:legs+=1;pullback=True
            elif recovery:pullback=False
            if legs>=2 and (c.iloc[i]>h.iloc[i-1] if trend>0 else c.iloc[i]<l.iloc[i-1]):
                fire=trend;legs=0;pullback=False
        second.append(fire)
    out['smc_ob_side']=ob_side;out['smc_ob_distance']=ob_distance;out['ict_gap_distance']=gap_distance
    out['ict_hour_sin']=np.sin(2*np.pi*x.timestamp.dt.hour/24)
    out['ict_hour_cos']=np.cos(2*np.pi*x.timestamp.dt.hour/24)
    width=(h-l).replace(0,np.nan)
    out['brooks_body']=(c-o)/width
    out['brooks_close_location']=(c-l)/width
    out['brooks_inside']=((h<=h.shift(1))&(l>=l.shift(1))).astype(int)
    out['brooks_outside']=((h>h.shift(1))&(l<l.shift(1))).astype(int)
    out['brooks_second_entry']=second
    return out.replace([np.inf,-np.inf],np.nan)


def build_features(frame: pd.DataFrame, config: FeatureConfig = FeatureConfig()) -> pd.DataFrame:
    x=validate_bars(frame,config)
    base=_one_timeframe(x,config)
    rule=config.interval*config.higher_multiple
    grouped=x.set_index('timestamp').resample(rule,label='right',closed='right',origin='epoch')
    higher=grouped.agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum','available_at':'max'})
    count=grouped['close'].count()
    higher=higher.loc[count.eq(config.higher_multiple)].reset_index()
    if higher.empty:raise ValueError("no completed higher timeframe")
    hf=_one_timeframe(higher,config).add_prefix('htf_')
    hf['htf_available_at']=higher.available_at;hf['htf_closed_at']=higher.timestamp
    joined=pd.merge_asof(x[['available_at']],hf,left_on='available_at',right_on='htf_available_at',direction='backward')
    # Refuse stale HTF context, rather than forward-filling indefinitely.
    stale=x.timestamp-joined.htf_closed_at>=rule
    joined.loc[stale,list(hf.columns[:-2])]=np.nan
    out=pd.concat([x,base,joined.drop(columns='available_at')],axis=1)
    out['setup']=(out.ichi_tk>0)&((out.smc_bos>0)|(out.ict_sweep>0)|
        (out.brooks_second_entry>0)|(out.brooks_close_location>=.7))
    # Dataset identity is audit provenance, not prospective/first-seen evidence.
    out.attrs['dataset_sha256']=sha256(canonical_json(x.astype(str).to_dict('records')).encode()).hexdigest()
    out.attrs['families']=feature_families()
    return out
