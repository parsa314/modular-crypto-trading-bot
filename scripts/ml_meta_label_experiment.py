"""Actual price-data signal collection, causal triple barriers and XGBoost meta-labeling.

2019-2022 training, 2023 validation, 2024-2026 reuse-as-test. 2024-26
HAS ALREADY BEEN EXPOSED in earlier v6 experiments: NEVER call virgin OOS.
The asset roster is retrospectively chosen, NOT historically exhaustive/PIT.
No real-order interfaces or API keys. No invented event counts.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import time
import warnings
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

DATA_START="2019-01-01"
TRAIN_END="2023-01-01"
VALID_END="2024-01-01"
TEST_END="2026-10-01"
EPOCH=lambda d:int(pd.Timestamp(d,tz="UTC").timestamp()*1000)
COST_SIDE=.0015
HORIZON=20
UNIVERSE = """
BTC ETH BNB SOL XRP ADA DOGE DOT LINK LTC AVAX SHIB ATOM UNI ETC XLM BCH TRX FIL AAVE
ALGO EOS XTZ DASH ZEC COMP MKR CRV SUSHI SNX YFI BAL ENJ CHZ NEAR GRT
AXS SAND MANA ICP VET HBAR THETA EGLD RUNE KAVA ONE ZIL BAT NEO
""".split()
SYMBOLS=[a+"USDT" for a in UNIVERSE]
if len(SYMBOLS)!=50 or len(set(SYMBOLS))!=50:
    raise RuntimeError("50_SYMBOL_REGISTRY_INVARIANT")

def read_public_klines(symbol):
    """Paginate read-only Binance daily historical klines; no account data."""
    start,stop=EPOCH(DATA_START),EPOCH(TEST_END)
    cursor=start
    found={}
    endpoints=set()
    while cursor<stop:
        q=urlencode({"symbol":symbol,"interval":"1d","startTime":cursor,
                     "endTime":stop-1,"limit":1000})
        content=None;errors=[]
        for endpoint in ("https://data-api.binance.vision","https://api.binance.com"):
            for attempt in range(2):
                try:
                    with urlopen(Request(endpoint+"/api/v3/klines?"+q,headers={
                        "User-Agent":"AcademicResearchOnly/1.0"}),timeout=35) as response:
                        content=json.loads(response.read())
                    if not isinstance(content,list):raise RuntimeError("NON_LIST_BINANCE_RESPONSE")
                    endpoints.add(endpoint)
                    break
                except Exception as exc:
                    errors.append(str(exc)[:150])
                    time.sleep(.6+attempt)
            if content is not None:break
        if content is None:raise RuntimeError(f"PUBLIC_PRICE_UNAVAILABLE {symbol} {errors[-2:]}")
        if not content:break
        for a in content:
            t=int(a[0])
            if t<start or t>=stop:continue
            row=(float(a[1]),float(a[2]),float(a[3]),float(a[4]),
                 float(a[5]),float(a[7]))
            if not all(math.isfinite(x) for x in row) or min(row[:4])<=0 or row[4]<0 or row[5]<0:
                raise ValueError(f"INVALID_OHLCV {symbol} {t}")
            if row[1]<max(row[0],row[2],row[3]) or row[2]>min(row[0],row[3]):
                raise ValueError(f"INVALID_HILO {symbol} {t}")
            if t in found and found[t]!=row:raise RuntimeError("BINANCE_DUPLICATE_CONFLICT")
            found[t]=row
        cursor2=int(content[-1][0])+86400000
        if cursor2<=cursor:raise RuntimeError("STALLED_DOWNLOAD")
        cursor=cursor2
        time.sleep(.10)
    if not found:
        return None,{"symbol":symbol,"status":"NO_HISTORY","rows":0}
    times=sorted(found)
    gaps=sum(times[i]-times[i-1]!=86400000 for i in range(1,len(times)))
    if gaps:
        # Gaps inside a known listing must not be ffilled or silently crossed.
        print("REAL_BAR_GAPS",symbol,gaps,flush=True)
    p=pd.DataFrame([found[t] for t in times],
        columns=["open","high","low","close","volume","quote_volume"])
    p["timestamp"]=pd.to_datetime(times,unit="ms",utc=True)
    p=p.set_index("timestamp")
    h=hashlib.sha256(json.dumps([(t,*found[t]) for t in times],
                       separators=(",",":")).encode()).hexdigest()
    manifest={"symbol":symbol,"first":str(p.index[0]),"last":str(p.index[-1]),
              "rows":len(p),"internal_gaps":gaps,"sha256_rows":h,
              "api_endpoints":sorted(endpoints),"status":"AVAILABLE"}
    return p,manifest

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

def label_event(p,position,entry_index,signal_at,stop_distance,
                strategy,confidence,feat,kind="D1",direction=1):
    """STOP_FIRST: if both intrabar barriers hit, count stop. Net fee/slippage."""
    if entry_index+HORIZON >=len(p):
        return None
    if not math.isfinite(stop_distance) or stop_distance<=0:return None
    entry=float(p.open.iloc[entry_index])
    stop=entry-direction*stop_distance
    target=entry+direction*stop_distance
    outcome="TIMEOUT";exit_i=entry_index+HORIZON
    exitpx=float(p.close.iloc[exit_i])
    for j in range(entry_index,entry_index+HORIZON+1):
        low=float(p.low.iloc[j]);high=float(p.high.iloc[j])
        if direction==1:
            stop_hit=low<=stop;target_hit=high>=target
            if stop_hit:
                exit_i=j;exitpx=min(float(p.open.iloc[j]),stop);outcome="SL";break
            if target_hit:
                exit_i=j;exitpx=target;outcome="TP";break
        else:
            stop_hit=high>=stop;target_hit=low<=target
            if stop_hit:
                exit_i=j;exitpx=max(float(p.open.iloc[j]),stop);outcome="SL";break
            if target_hit:
                exit_i=j;exitpx=target;outcome="TP";break
    if direction==1:
        net=exitpx*(1-COST_SIDE)/(entry*(1+COST_SIDE))-1
    else:
        # Short event label is research-only, no funding or borrow modeled;
        # confine ML dataset to spot executable long signals (BUY only).
        raise ValueError("ONLY_LONG_SPOT_META_LABEL")
    ts=p.index[entry_index];te=p.index[exit_i]
    if pd.Timedelta(te-ts)>pd.Timedelta(days=HORIZON+(kind=="M15")):
        raise RuntimeError("EVENT_HORIZON")
    values={k:float(v) if np.isfinite(v) else np.nan for k,v in feat.items()}
    return {"symbol":position,"strategy_id":strategy,"timeframe":kind,
            "signal_time":signal_at,"entry_time":ts,"event_end":te,
            "entry_price":entry,"exit_price":exitpx,
            "barrier":outcome,"net_pnl":float(net),"label":int(net>0),
            "confidence":float(confidence),**values}

FEATS=["confidence","atr_pct","atr20ratio","vol_ratio","rsi",
       "distance_sma50","distance_sma200","macd_pct","bb_position",
       "corr_btc","hour","day_of_week","btc_volatility"]
def day_signals(s,p,daily):
    candidates=[]
    for i in range(202,len(p)-HORIZON-1):
        f=daily.iloc[i]
        if not np.isfinite(f[["atr","atr_pct","rsi","vol_ratio",
                "donchian55","donchian20","distance_sma200"]].to_numpy(float)).all():
            continue
        if (p.index[i+1]-p.index[i])!=pd.Timedelta(days=1) or (
                p.index[i+HORIZON+1]-p.index[i])!=pd.Timedelta(days=HORIZON+1):
            continue
        conf={}
        if p.close.iloc[i] > f.donchian55 and p.index[i+1].weekday()==0:
            conf["v4_donchian55"]=1.0
        if f.rsi<30:
            conf["v5_rsi14_meanrev"]=float((30-f.rsi)/30)
        if p.close.iloc[i] > f.donchian20+0.25*f.atr:
            conf["v6_atr_breakout"]=float(np.clip((p.close.iloc[i]-f.donchian20)/f.atr,0,10)/10)
        if not conf:continue
        features={k:float(f.get(k,np.nan)) for k in FEATS if k!="confidence"}
        features["hour"]=0.
        features["day_of_week"]=float(p.index[i].weekday())
        for name,c in conf.items():
            row=label_event(p,s,i+1,p.index[i]+pd.Timedelta(days=1),
                            2*float(f.atr),name,c,features)
            if row is not None:candidates.append(row)
    return candidates

def m15_v1_events(filename,manifest_file,btc_daily):
    """Authentic original v1 BUY transitions, not a daily-price imitation."""
    from scripts.backtest_btcusdt_2022_2024 import read_verified,confluence_positions
    d,_=read_verified(filename,manifest_file)
    position,_=confluence_positions(d)
    idx=np.flatnonzero((position==1)&(np.r_[0,position[:-1]]==0))
    valid=pd.DataFrame({"timestamp":btc_daily.index,"close":btc_daily.close}).set_index("timestamp")
    rows=[]
    for entry in idx:
        signal=entry-2
        if signal<200 or entry+21>=len(d):continue
        window=d.iloc[signal-199:signal+1]
        if (window.timestamp.diff().iloc[1:]!=pd.Timedelta(minutes=15)).any():continue
        atr=window.assign(prev=window.close.shift(1))
        tr=pd.concat([(atr.high-atr.low),
                      (atr.high-atr.prev).abs(),(atr.low-atr.prev).abs()],axis=1).max(axis=1)
        a=float(tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean().iloc[-1])
        if not math.isfinite(a) or a<=0:continue
        if (d.timestamp.iloc[entry+20]-d.timestamp.iloc[entry])!=pd.Timedelta(minutes=15*20):
            continue
        time=d.timestamp.iloc[signal]
        daily_t=time.floor("D")
        bt=btc_daily.loc[:daily_t-pd.Timedelta(days=1)]
        bt_rsi=50.
        if len(bt)>15:
            change=bt.close.diff()
            gain=change.clip(lower=0).ewm(alpha=1/14,adjust=False,min_periods=14).mean().iloc[-1]
            loss=-change.clip(upper=0).ewm(alpha=1/14,adjust=False,min_periods=14).mean().iloc[-1]
            bt_rsi=100-100/(1+gain/loss) if loss>0 else 100
        features={"atr_pct":a/d.close.iloc[signal],"rsi":bt_rsi,
                  "hour":float(time.hour),"day_of_week":float(time.dayofweek),
                  "vol_ratio":d.volume.iloc[signal]/max(1e-12,d.volume.iloc[signal-20:signal].mean())}
        # Intra-day history uses 20 M15 bars, not 20 DAYS.
        row=label_event(d,"BTCUSDT",entry,time+pd.Timedelta(minutes=15),
                        2*a,"v1_original_four_style",1.0,features,kind="M15")
        if row is not None:rows.append(row)
    return rows

def add_btc_regime(data,btc):
    """3-bin 20-day realized BTC volatility proxy, NOT an HMM."""
    btc_vol=btc.close.pct_change(fill_method=None).rolling(20).std()
    train=btc_vol[btc_vol.index<pd.Timestamp(TRAIN_END,tz="UTC")].dropna()
    limits=np.quantile(train,[1/3,2/3]).tolist()
    signal=pd.to_datetime(data.signal_time,utc=True)
    v=btc_vol.reindex(signal.dt.floor("D")-pd.Timedelta(days=1)).to_numpy()
    data["btc_volatility"]=v
    data["vol_regime"]=np.where(v<limits[0],"LOW",np.where(v<limits[1],"MID","HIGH"))
    return {"method":"train-only BTC 20d volatility tercile proxy, NOT HMM","train_cutoffs":limits}

def research_metrics(e):
    """Event-weighted diagnostics; no pretend portfolio Sharpe here."""
    n=len(e)
    if not n:return {"n":0,"win_rate":None,"mean_net_per_event":None}
    return {"n":n,"win_rate":float(e.label.mean()),
            "mean_net_per_event":float(e.net_pnl.mean())}

def purged_folds(train):
    """Calendar-based TRAIN-ONLY expanding folds, purge 20 days, embargo 5."""
    times=pd.to_datetime(train.signal_time,utc=True)
    lo=times.min();hi=times.max()
    boundaries=pd.date_range(lo,hi,periods=7)
    for k in range(1,6):
        valid_start=boundaries[k]
        valid_end=boundaries[k+1]
        tr=np.flatnonzero((times<valid_start-pd.Timedelta(days=25))&
                         (pd.to_datetime(train.event_end,utc=True)<
                          valid_start-pd.Timedelta(days=5)))
        va=np.flatnonzero((times>=valid_start)&(times<valid_end))
        if len(tr)>=100 and len(va)>=30:yield tr,va

def ml_experiment(events):
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder
    from xgboost import XGBClassifier
    data=events.sort_values(["signal_time","symbol","strategy_id"]).reset_index(drop=True)
    t=pd.to_datetime(data.signal_time,utc=True)
    # All labels must FINISH before corresponding split boundary.
    train=data[(t<pd.Timestamp(TRAIN_END,tz="UTC"))&
               (pd.to_datetime(data.event_end,utc=True)<pd.Timestamp(TRAIN_END,tz="UTC"))].copy()
    valid=data[(t>=pd.Timestamp(TRAIN_END,tz="UTC"))&(t<pd.Timestamp(VALID_END,tz="UTC"))&
               (pd.to_datetime(data.event_end,utc=True)<pd.Timestamp(VALID_END,tz="UTC"))].copy()
    test=data[(t>=pd.Timestamp(VALID_END,tz="UTC"))&
              (t<pd.Timestamp(TEST_END,tz="UTC"))&
              (pd.to_datetime(data.event_end,utc=True)<pd.Timestamp(TEST_END,tz="UTC"))].copy()
    cats=["strategy_id","vol_regime"]
    nums=[c for c in FEATS if c in data.columns]
    missing=set(FEATS)-set(nums)
    if missing:raise RuntimeError(f"MISSING_FEATURES {missing}")
    def make_model():
        encoder=ColumnTransformer([
           ("numeric",SimpleImputer(strategy="median",add_indicator=False),nums),
           ("category",OneHotEncoder(handle_unknown="ignore"),cats)])
        classifier=XGBClassifier(n_estimators=200,max_depth=3,learning_rate=.04,
             subsample=.8,colsample_bytree=.8,reg_lambda=10.,objective="binary:logistic",
             tree_method="hist",n_jobs=2,random_state=126,eval_metric="logloss")
        return Pipeline([("preprocessor",encoder),("model",classifier)])
    counts={"total":len(data),"train":len(train),"validation":len(valid),"test_reused":len(test),
            "by_strategy":{str(k):int(v) for k,v in data.strategy_id.value_counts().items()},
            "class_train":{str(k):int(v) for k,v in train.label.value_counts().items()},
            "class_validation":{str(k):int(v) for k,v in valid.label.value_counts().items()},
            "class_test_reused":{str(k):int(v) for k,v in test.label.value_counts().items()}}
    if min(len(train),len(valid),len(test))<50 or train.label.nunique()<2:
        return counts,{"status":"INSUFFICIENT_TRAIN_OR_VALIDATION_FOR_MODEL"}
    cv=[]
    for a,b in purged_folds(train):
        fit=train.iloc[a]
        if fit.label.nunique()<2:continue
        v=train.iloc[b]
        m=make_model();m.fit(fit[nums+cats],fit.label)
        pred=m.predict_proba(v[nums+cats])[:,1]
        n=max(1,int(math.ceil(.1*len(pred))))
        top=np.argsort(pred)[-n:]
        cv.append({"n_train":len(fit),"n_validation":len(v),
                   "precision_top_decile":float(v.label.to_numpy()[top].mean())})
    model=make_model();model.fit(train[nums+cats],train.label)
    val_p=model.predict_proba(valid[nums+cats])[:,1]
    # Exact threshold chosen ONLY on the validation probability distribution.
    cutoff=float(np.quantile(val_p,.9))
    test_p=model.predict_proba(test[nums+cats])[:,1]
    test["p_positive"]=test_p
    test["selected"]=test_p>=cutoff
    val_top=valid.label.to_numpy()[np.argsort(val_p)[-max(1,math.ceil(.1*len(val_p))):]]
    hold_top=test.loc[test.selected]
    before=research_metrics(test)
    after=research_metrics(hold_top)
    # Simplified equal-notional realized-event daily benchmark, not NAV Sharpe.
    daily_returns=pd.Series(0.,index=pd.date_range(
        pd.Timestamp(VALID_END,tz="UTC"),pd.Timestamp(TEST_END,tz="UTC")-pd.Timedelta(days=1),freq="D"))
    def daily_metric(subset):
        s=daily_returns.copy()
        if len(subset):
            for _,row in subset.iterrows():
                when=pd.Timestamp(row.event_end).floor("D")
                if when in s.index:s.loc[when]+=float(row.net_pnl)*.01
        x=s.to_numpy(float)
        std=x.std(ddof=1)
        return {"realized_pnl_daily_proxy_sharpe":float(np.sqrt(365)*x.mean()/std) if std>1e-15 else None,
                "approx_sum_risk_allocated_event_net_return":float(x.sum()),
                "warning":"Event exit-date book at 1% fixed notional, NOT tradable mark-to-market NAV; overlapping positions can exceed capital."}
    full=daily_metric(test);filtered=daily_metric(hold_top)
    delta=(filtered["realized_pnl_daily_proxy_sharpe"]-full["realized_pnl_daily_proxy_sharpe"]
           if filtered["realized_pnl_daily_proxy_sharpe"] is not None and
           full["realized_pnl_daily_proxy_sharpe"] is not None else None)
    return counts,{"status":"TRAINED_EXPLORATORY",
       "train_positive_fraction":float(train.label.mean()),
       "training_cv":cv,
       "validation_precision_top_decile":float(val_top.mean()),
       "validation_score_threshold":cutoff,
       "test_is_NOT_virgin":True,
       "test_raw":before,"test_filtered":after,
       "test_precision_top_decile":float(test.label.to_numpy()[
            np.argsort(test_p)[-max(1,math.ceil(.1*len(test_p))):]].mean()),
       "event_based_raw_daily_proxy":full,
       "event_based_filtered_daily_proxy":filtered,
       "sharpe_delta_proxy":delta,
       "model_adds_value_under_0_3_proxy_rule":bool(delta is not None and delta>=.3),
       "ml_meets_0_55_validation_precision":bool(val_top.mean()>.55),
       "note":"This is a research-only preliminary experiment. A raw trade selection edge and daily realized-PnL proxy do NOT prove a portfolio Sharpe gain, execution, or production readiness."}

def main():
    out=Path("results")
    out.mkdir(exist_ok=True)
    manifest=[]
    market={}
    for symbol in SYMBOLS:
        p,record=read_public_klines(symbol)
        manifest.append(record)
        if p is not None and len(p)>=280:market[symbol]=p
        print("HISTORICAL_DATA",symbol,record["status"],record["rows"],flush=True)
    if "BTCUSDT" not in market:raise RuntimeError("BTC_REFERENCE_UNAVAILABLE")
    btc=market["BTCUSDT"]
    events=[]
    for s,p in market.items():
        z=build_features(p,btc)
        fresh=day_signals(s,p,z)
        events.extend(fresh)
        print("CAUSAL_SIGNALS",s,len(fresh),flush=True)
    original=m15_v1_events(
        Path("data/btc_usdt_m15_2022_2024.parquet"),
        Path("data/btc_usdt_m15_2022_2024.manifest.json"),btc)
    events.extend(original)
    print("AUTHENTIC_V1_BTC_M15_BUY_ENTRIES",len(original),flush=True)
    data=pd.DataFrame(events)
    if data.empty:raise RuntimeError("NO_REAL_MARKET_SIGNALS")
    regime=add_btc_regime(data,btc)
    data["hour"]=pd.to_datetime(data.signal_time,utc=True).dt.hour.astype(float)
    data["day_of_week"]=pd.to_datetime(data.signal_time,utc=True).dt.dayofweek.astype(float)
    data.to_parquet(out/"signals.parquet",index=False)
    counts,result=ml_experiment(data)
    counts["historical_universe_requested"]=50
    counts["historical_universe_with_sufficient_bars"]=len(market)
    counts["historical_manifest"]=manifest
    counts["v1_authentic_M15_BTC_events"]=len(original)
    counts["minimum_5000_rows_met"]=len(data)>=5000
    counts["regime"]=regime
    counts["sample_limitations"]=[
      "Asset roster is static/retrospectively selected, not all-ever-listed or historical PIT top50",
      "v4/v5/v6 genuine stated daily rules, but independent overlapping ENTRY EVENTS, not portfolio fills",
      "v1 uses genuine BTC 2022-24 M15 four-arm model BUY transitions only, not fabricated M15 signals for 50 coins",
      "STOP_FIRST daily OHLC ambiguous intrabar path; 20-bar triple barrier is research-only",
      "Test 2024-26 has been observed repeatedly in prior research; NOT unexposed OOS"
    ]
    (out/"signal_count.json").write_text(json.dumps(counts,indent=2,default=str,allow_nan=False)+"\n")
    decision={"n_total":len(data),"n_labeled":int(data.label.notna().sum()),
              "n_positive":int(data.label.sum()),
              "n_negative":int((1-data.label).sum()),
              "minimum_5000_rows_met":len(data)>=5000,
              "decision":"ML_TRAINED_EXPLORATORY" if result.get("status")=="TRAINED_EXPLORATORY" else "INSUFFICIENT_DATA_FOR_ML",
              "independence":"2024-26 ALREADY EXPOSED in earlier v6 testing, contaminated as untouched holdout"}
    (out/"ml_data_decision.json").write_text(json.dumps(decision,indent=2,allow_nan=False)+"\n")
    (out/"ml_comparison.json").write_text(json.dumps(result,indent=2,default=str,allow_nan=False)+"\n")
    print("SIGNAL_COUNT",len(data),"TRAIN",counts["train"],"VAL",counts["validation"],
          "REUSED_TEST",counts["test_reused"],"POS",decision["n_positive"],flush=True)
    print("META_LABEL_EXPERIMENT",json.dumps({
        "status":result.get("status"),"validation_prec10":result.get("validation_precision_top_decile"),
        "test_prec10":result.get("test_precision_top_decile"),
        "sharpe_delta_proxy":result.get("sharpe_delta_proxy")}),flush=True)

if __name__=="__main__":
    main()
