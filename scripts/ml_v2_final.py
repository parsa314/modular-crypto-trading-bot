"""Final ML 2023 validation using real Binance H4/D1 prices and USD-M funding.

Train 2019-2022, validation 2023, NEVER download/read 2024+.
No live accounts, no order functions, no fabricated scores.
Static 50-name universe is explicitly NOT a historical PIT top-fifty.
"""
from __future__ import annotations
import hashlib,json,math,time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request,urlopen
from datetime import timezone
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score,precision_score,recall_score,f1_score
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import OneHotEncoder
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from hmmlearn.hmm import GaussianHMM

from ml.data_loader import SYMBOLS
assert len(SYMBOLS)==50 and len(set(SYMBOLS))==50
START=pd.Timestamp("2019-01-01",tz="UTC")
VAL=pd.Timestamp("2023-01-01",tz="UTC")
END=pd.Timestamp("2024-01-01",tz="UTC")
STEP={"4h":pd.Timedelta(hours=4),"1d":pd.Timedelta(days=1)}
HORIZON=30;TP_ATR=2.5;SL_ATR=1.;SIDE_COST=.0015
NUM_COLS=["signal_confidence","atr_ratio","volume_ratio","rsi","macd_hist",
          "return_24h","return_7d","corr_btc_30d","realized_vol_30d",
          "distance_atr_band_upper","distance_atr_band_lower","volume_z_score",
          "funding_rate_8h","funding_missing","distance_sma50","distance_sma200",
          "hour_of_day","day_of_week"]
CAT_COLS=["strategy_id","timeframe","regime"]
ROOT=Path("results")
ROOT.mkdir(exist_ok=True)

def ms(dt):return int(pd.Timestamp(dt).timestamp()*1000)
def read_json(url,timeout=25):
    errors=[]
    for attempt in range(3):
        try:
            req=Request(url,headers={"User-Agent":"academic-public-data-study/1.0"})
            with urlopen(req,timeout=timeout) as x:return json.loads(x.read())
        except Exception as ex:
            errors.append(str(ex)[:120])
            time.sleep(.5+attempt)
    raise RuntimeError("PUBLIC_SOURCE_FAILED "+str(errors))

def fetch_spot(symbol,tf):
    cursor=ms(START);finish=ms(END)
    rows={};endpoints=set();pages=0
    while cursor<finish:
        query=urlencode({"symbol":symbol,"interval":tf,"startTime":cursor,
                         "endTime":finish-1,"limit":1000})
        data=read_json("https://data-api.binance.vision/api/v3/klines?"+query)
        if not isinstance(data,list):raise ValueError("BAD_BINANCE_KLINE_PAYLOAD")
        if not data:break
        pages+=1
        for bar in data:
            t=int(bar[0])
            if t<cursor or t>=finish:continue
            values=[float(bar[i]) for i in [1,2,3,4,5,7]]
            if not np.isfinite(values).all() or min(values[:4])<=0 or min(values[4:])<0:
                raise ValueError(f"INVALID_KLINE_{symbol}_{tf}_{t}")
            o,h,l,c,v,q=values
            if h<max(o,c,l) or l>min(o,c,h):raise ValueError("BAD_OHLC")
            if t in rows and rows[t]!=values:raise ValueError("DUPLICATE_MISMATCH")
            rows[t]=values
        nxt=int(data[-1][0])+int(STEP[tf].total_seconds()*1000)
        if nxt<=cursor:raise RuntimeError("NO_PROGRESS")
        cursor=nxt
        if pages%8==0:time.sleep(.25)
    if not rows:return None,{"symbol":symbol,"tf":tf,"bars":0,"status":"NO_HISTORY"}
    dates=sorted(rows)
    gap=sum(b-a!=STEP[tf].total_seconds()*1000 for a,b in zip(dates,dates[1:]))
    data=pd.DataFrame([rows[t] for t in dates],columns=["open","high","low","close","volume","quote_volume"])
    data.index=pd.to_datetime(dates,unit="ms",utc=True)
    data.index.name="timestamp"
    digest=hashlib.sha256(json.dumps([(t,*rows[t]) for t in dates],separators=(",",":")).encode()).hexdigest()
    return data,{"symbol":symbol,"tf":tf,"bars":len(data),"first":str(data.index[0]),
                 "last":str(data.index[-1]),"gap_intervals":gap,
                 "sha256_price_rows":digest,"status":"AVAILABLE","pages":pages}

def fetch_funding(symbol):
    """USD-M perp historical funding, optional; always carry explicit missingness."""
    cursor=ms(START);stop=ms(END);pairs=[];calls=0
    while cursor<stop:
        query=urlencode({"symbol":symbol,"startTime":cursor,
                         "endTime":stop-1,"limit":1000})
        result=read_json("https://fapi.binance.com/fapi/v1/fundingRate?"+query,timeout=12)
        if not isinstance(result,list):raise ValueError("BAD_FUNDING_RESPONSE")
        if not result:break
        for r in result:
            t=int(r["fundingTime"])
            if cursor<=t<stop:pairs.append((t,float(r["fundingRate"])))
        next_cursor=int(result[-1]["fundingTime"])+1
        if next_cursor<=cursor:raise RuntimeError("FUNDING_PAGINATION_STALLED")
        cursor=next_cursor;calls+=1
        if len(result)<1000:break
        if calls%8==0:time.sleep(.25)
    if not pairs:return pd.Series(dtype=float),{"symbol":symbol,"funding_observations":0}
    m={k:v for k,v in pairs}
    f=pd.Series(m,dtype=float).sort_index()
    f.index=pd.to_datetime(f.index,unit="ms",utc=True)
    return f,{"symbol":symbol,"funding_observations":len(f),"last":str(f.index[-1])}

def add_features(frame,btc,tf,funding,regime_state):
    p=frame
    close=p.close
    prev=close.shift(1)
    tr=pd.concat([(p.high-p.low),(p.high-prev).abs(),(p.low-prev).abs()],axis=1).max(axis=1)
    atr=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    delta=close.diff()
    gain=delta.clip(lower=0).ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    loss=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    rsi=100-100/(1+(gain/loss.replace(0,np.nan)))
    rsi=rsi.fillna(100)
    ema12=close.ewm(span=12,adjust=False).mean()
    ema26=close.ewm(span=26,adjust=False).mean()
    macd=ema12-ema26
    macd_hist=macd-macd.ewm(span=9,adjust=False).mean()
    volume=p.quote_volume
    avg_vol=volume.shift(1).rolling(30,min_periods=30).mean()
    std_vol=volume.shift(1).rolling(30,min_periods=30).std()
    sma20=close.rolling(20).mean()
    bands_upper=sma20+2*atr
    bands_lower=sma20-2*atr
    k1=6 if tf=="4h" else 1;k7=42 if tf=="4h" else 7
    ret=close.pct_change(fill_method=None)
    br=btc.close.pct_change(fill_method=None)
    aligned=br.reindex(p.index)
    now=pd.Series(p.index+STEP[tf],index=p.index)
    if len(funding):
        f=pd.DataFrame({"funding_time":funding.index,"rate":funding.values})
        matching=pd.merge_asof(pd.DataFrame({"decision_time":now.values}),
                                f.sort_values("funding_time"),left_on="decision_time",
                                right_on="funding_time",direction="backward",
                                tolerance=pd.Timedelta(days=2))
        f8=pd.Series(matching.rate.to_numpy(),index=p.index)
    else:
        f8=pd.Series(np.nan,index=p.index)
    if len(regime_state):
        rg=regime_state.reindex(p.index,method="ffill")
    else:
        rg=pd.Series("MISSING",index=p.index)
    feat=pd.DataFrame({
        "atr":atr,"atr_ratio":atr/close,"volume_ratio":volume/avg_vol,
        "rsi":rsi,"macd_hist":macd_hist/close,
        "return_24h":close.pct_change(k1,fill_method=None),
        "return_7d":close.pct_change(k7,fill_method=None),
        "corr_btc_30d":ret.rolling(30,min_periods=30).corr(aligned),
        "realized_vol_30d":ret.rolling(30,min_periods=30).std(),
        "distance_atr_band_upper":(close-bands_upper)/atr,
        "distance_atr_band_lower":(close-bands_lower)/atr,
        "volume_z_score":(volume-avg_vol)/std_vol,
        "funding_rate_8h":f8,
        "funding_missing":f8.isna().astype(float),
        "distance_sma50":close/close.rolling(50).mean()-1,
        "distance_sma200":close/close.rolling(200).mean()-1,
        "hour_of_day":now.dt.hour.astype(float),
        "day_of_week":now.dt.dayofweek.astype(float),
        "regime":rg,
        "donchian55":p.high.shift(1).rolling(55).max(),
        "donchian20":p.high.shift(1).rolling(20).max(),
        "btc_close":btc.close.reindex(p.index)
    },index=p.index)
    # Features computed on prices *known by signal time*; no future candle.
    continuous=p.index.to_series().diff().eq(STEP[tf])
    valid=continuous.rolling(201,min_periods=201).sum().eq(201)
    feat.loc[~valid,NUM_COLS]=np.nan
    feat["history_ok"]=valid
    return feat

def fit_hmm(btc4):
    """Train-only 3-state GaussianHMM, then strictly forward-filter state post train."""
    change=np.log(btc4.close).diff()
    vol=change.rolling(30).std()
    usable=vol[(vol.index<VAL)&(vol.notna())]
    values=np.log(usable.clip(lower=1e-9).to_numpy()).reshape(-1,1)
    if len(values)<300:raise RuntimeError("INSUFFICIENT_HMM_TRAINING")
    model=GaussianHMM(n_components=3,covariance_type="diag",n_iter=180,tol=1e-4,random_state=42)
    model.fit(values)
    # Forward FILTER, never Viterbi over future prices.
    var=np.asarray(model.covars_).reshape(3,-1)[:,0]
    mu=model.means_.reshape(-1)
    trans=model.transmat_;start=model.startprob_
    out={};alpha=None
    for t,value in vol.dropna().items():
        x=math.log(max(float(value),1e-9))
        likelihood=np.exp(-.5*(np.log(2*np.pi*var)+(x-mu)**2/var))
        likelihood/=likelihood.max()
        alpha=(start if alpha is None else alpha@trans)*likelihood
        if alpha.sum()<=0 or not np.isfinite(alpha.sum()):raise RuntimeError("BAD_HMM_FILTER")
        alpha/=alpha.sum()
        out[t]="HMM"+str(np.argmax(alpha))
    return pd.Series(out).sort_index(),{"trained_rows":len(values),"means":mu.tolist()}

def signals_and_labels(symbol,tf,p,feat):
    # One event can be proposed per strategy/bar; overlapping events get
    # uniqueness weighting, not duplicated into fictitious independent rows.
    rows=[]
    dt=STEP[tf]
    future=30
    index=p.index
    for i in range(202,len(p)-future-1):
        f=feat.iloc[i]
        if not bool(f["history_ok"]):continue
        if not np.isfinite(f[["atr","rsi","donchian55","donchian20","atr_ratio"]].to_numpy(float)).all():
            continue
        if index[i+future+1]-index[i]!=dt*(future+1):continue
        close=p.close.iloc[i]
        cand=[]
        if close>f.donchian55:
            cand.append(("v4_donchian55",float(np.clip((close-f.donchian55)/f.atr,0,10)/10)))
        if f.rsi<30:
            cand.append(("v5_rsi14",float((30-f.rsi)/30)))
        if close>f.donchian20+.25*f.atr:
            cand.append(("v6_atr_breakout",float(np.clip((close-f.donchian20)/f.atr,0,10)/10)))
        if not cand:continue
        entry=float(p.open.iloc[i+1])
        atr=float(f.atr)
        stop=entry-SL_ATR*atr
        target=entry+TP_ATR*atr
        outcome="TIMEOUT";end_idx=i+future+1;endpx=float(p.close.iloc[end_idx])
        for j in range(i+1,i+future+2):
            bar=p.iloc[j]
            if bar.low<=stop:
                outcome="SL";end_idx=j;endpx=min(bar.open,stop);break
            if bar.high>=target:
                outcome="TP";end_idx=j;endpx=target;break
        net=endpx*(1-SIDE_COST)/(entry*(1+SIDE_COST))-1
        record={k:(float(f[k]) if k!="regime" and np.isfinite(f[k]) else np.nan) for k in NUM_COLS}
        record["regime"]=str(f.regime)
        record.update({"symbol":symbol,"timeframe":tf,"signal_time":index[i]+dt,
                       "entry_time":index[i+1],"event_end":index[end_idx],
                       "entry_i":i+1,"end_i":end_idx,"barrier":outcome,
                       "net_pnl":float(net),"label":int(net>0),
                       "is_2023_validation":bool(index[i]>=VAL)})
        for strategy,confidence in cand:
            row=dict(record);row["strategy_id"]=strategy;row["signal_confidence"]=confidence
            rows.append(row)
    return rows

def uniqueness_weights(events):
    """Average uniqueness per event based on observed simultaneous labels.
    Build per-(symbol,timeframe) concurrency arrays; separate crypto clocks.
    """
    weights=np.ones(len(events))
    for (_,tf),part in events.groupby(["symbol","timeframe"],sort=False):
        begin=part.entry_i.to_numpy(dtype=int);end=part.end_i.to_numpy(dtype=int)
        limit=max(end)+2
        diff=np.zeros(limit+1,dtype=float)
        np.add.at(diff,begin,1)
        np.add.at(diff,end+1,-1)
        concurrent=np.cumsum(diff)[:limit]
        inv=np.divide(1,concurrent,out=np.zeros_like(concurrent),where=concurrent>0)
        prefix=np.r_[0,np.cumsum(inv)]
        values=(prefix[end+1]-prefix[begin])/(end-begin+1)
        weights[part.index.to_numpy()]=values
    return weights

def _model(kind,n=220):
    if kind=="XGBoost":
        from xgboost import XGBClassifier
        return XGBClassifier(n_estimators=n,max_depth=3,learning_rate=.04,subsample=.8,
              colsample_bytree=.8,reg_lambda=12,n_jobs=2,random_state=42,
              objective="binary:logistic",eval_metric="logloss",tree_method="hist")
    if kind=="LightGBM":
        from lightgbm import LGBMClassifier
        return LGBMClassifier(n_estimators=n,num_leaves=15,learning_rate=.04,
              max_depth=4,subsample=.8,colsample_bytree=.8,reg_lambda=12,
              verbosity=-1,n_jobs=2,random_state=42)
    from catboost import CatBoostClassifier
    return CatBoostClassifier(iterations=n,depth=4,learning_rate=.04,l2_leaf_reg=12,
           verbose=False,thread_count=2,random_seed=42,loss_function="Logloss")

def train_and_predict(kind,fit,inner,external):
    pre=ColumnTransformer([
      ("numeric",SimpleImputer(strategy="median",keep_empty_features=True),NUM_COLS),
      ("category",OneHotEncoder(handle_unknown="ignore",sparse_output=False),CAT_COLS)])
    x=pre.fit_transform(fit[NUM_COLS+CAT_COLS])
    xx=pre.transform(inner[NUM_COLS+CAT_COLS])
    xt=pre.transform(external[NUM_COLS+CAT_COLS])
    clf=_model(kind)
    clf.fit(x,fit.label,sample_weight=fit.uniqueness_weight.to_numpy())
    return clf.predict_proba(xx)[:,1],clf.predict_proba(xt)[:,1],(pre,clf)

def cv_fold(train,begin,end):
    # Purge full 30 calendar days before outer evaluation and 5 day embargo.
    t=pd.to_datetime(train.signal_time,utc=True)
    et=pd.to_datetime(train.event_end,utc=True)
    a=pd.Timestamp(begin,tz="UTC");b=pd.Timestamp(end,tz="UTC")
    ins=train[(t<a-pd.Timedelta(days=35))&(et<a-pd.Timedelta(days=5))]
    out=train[(t>=a)&(t<b)&(et<b)]
    return ins,out

def run_models(events):
    train=events[(events.signal_time<VAL)&(events.event_end<VAL)].copy()
    val=events[(events.signal_time>=VAL)&(events.signal_time<END)&
               (events.event_end<END)].copy()
    status={"counts":{"total":len(events),"train":len(train),"validation":len(val),
                      "positive_train":int(train.label.sum()),
                      "positive_val":int(val.label.sum()),
                      "effective_uniqueness_train_weight_sum":float(train.uniqueness_weight.sum()),
                      "by_timeframe":{k:int(v) for k,v in events.timeframe.value_counts().items()},
                      "by_strategy":{k:int(v) for k,v in events.strategy_id.value_counts().items()}}}
    if len(train)<200 or len(val)<100 or train.label.nunique()!=2 or val.label.nunique()!=2:
        status["verdict"]="INSUFFICIENT_REAL_LABELS_FOR_TRAINING"
        return status
    # Nested train-only outer folds; inner held-out tail, with 35d gap.
    outer=[("2021-01-01","2022-01-01"),("2022-01-01","2023-01-01")]
    model_kinds=["XGBoost","LightGBM","CatBoost"]
    reviews={k:[] for k in model_kinds}
    for a,b in outer:
        ins,out=cv_fold(train,a,b)
        if len(ins)<200 or len(out)<100 or ins.label.nunique()<2 or out.label.nunique()<2:
            continue
        pivot=ins.signal_time.quantile(.75)
        inner_train=ins[(ins.signal_time<pivot-pd.Timedelta(days=35))&
                        (ins.event_end<pivot-pd.Timedelta(days=5))]
        inner_val=ins[(ins.signal_time>=pivot)&(ins.event_end<pd.Timestamp(a,tz="UTC")-pd.Timedelta(days=5))]
        if len(inner_train)<150 or len(inner_val)<40 or inner_train.label.nunique()<2:continue
        for k in model_kinds:
            _,pred,_=train_and_predict(k,inner_train,inner_val,out)
            reviews[k].append({"outer_start":a,"train_rows":len(inner_train),
                 "outer_rows":len(out),"auc":float(roc_auc_score(out.label,pred))})
        print("NESTED_OUTER",a,{k:reviews[k][-1]["auc"] for k in model_kinds},flush=True)
    # Select fixed best model on TRAIN-ONLY outer CV, never on Val 2023.
    scores={k:(float(np.mean([x["auc"] for x in reviews[k]]))
               if reviews[k] else None) for k in model_kinds}
    if any(x is None for x in scores.values()):
        status["verdict"]="NESTED_CV_INCOMPLETE"
        status["nested_cv"]=reviews
        return status
    # Internal trailing year 2022 for model-selection diagnostics; fixed n=220.
    train_l=train[(train.signal_time<pd.Timestamp("2022-01-01",tz="UTC")-pd.Timedelta(days=35))&
                  (train.event_end<pd.Timestamp("2022-01-01",tz="UTC")-pd.Timedelta(days=5))]
    train_r=train[(train.signal_time>=pd.Timestamp("2022-01-01",tz="UTC"))&(train.event_end<VAL)]
    predictions={}
    saved_models={}
    for k in model_kinds:
        _,p,model=train_and_predict(k,train_l,train_r,val)
        predictions[k]=p
        saved_models[k]=model
    predictions["Ensemble"]=np.mean(np.stack([predictions[k] for k in model_kinds]),axis=0)
    result={"nested_train_cv_auc":scores,"outer_folds":reviews,"validation":{}}
    for k,prob in predictions.items():
        top=np.argsort(prob)[-max(1,int(math.ceil(len(prob)*.1))):]
        cut=float(np.quantile(prob,.9))
        selected=prob>=cut
        y=val.label.to_numpy()
        auc=float(roc_auc_score(y,prob))
        result["validation"][k]={
           "auc":auc,"precision_top_decile":float(y[top].mean()),
           "precision":float(precision_score(y,selected,zero_division=0)),
           "recall":float(recall_score(y,selected,zero_division=0)),
           "f1":float(f1_score(y,selected,zero_division=0)),
           "selected_events":int(selected.sum()),
           "positive_rate":float(y.mean()),
           "passes_auc_0_55":bool(auc>.55)}
    result["model_selected_by_training_only_cv"]=max(scores,key=scores.get)
    result["verdict"]="PASS" if result["validation"][result["model_selected_by_training_only_cv"]]["auc"]>.55 else "FAIL"
    result["metrics_are_2023_validation_only"]=True
    result["test_2024_2026"]="NOT_OPENED"
    status.update(result)
    return status

def main():
    market={};funding={};manifests=[];funding_reports=[]
    for ix,sym in enumerate(SYMBOLS):
        for tf in ["4h","1d"]:
            try:
                data,manifest=fetch_spot(sym,tf)
                manifests.append(manifest)
                if data is not None:market[(sym,tf)]=data
                print("PRICES",ix+1,"/50",sym,tf,manifest["bars"],flush=True)
            except Exception as exc:
                manifests.append({"symbol":sym,"tf":tf,"status":"FAILED",
                                  "error":str(exc)[:180],"bars":0})
                print("PRICES_FAILED",sym,tf,str(exc)[:180],flush=True)
        # Funding permission/availability is separate from spot feed.
        if ix==0:
            try:
                series,metadata=fetch_funding(sym)
                funding[sym]=series;funding_reports.append(metadata)
            except Exception as ex:
                funding_reports.append({"symbol":sym,"status":"SOURCE_BLOCKED","error":str(ex)[:170]})
                funding[sym]=pd.Series(dtype=float)
                # Circuit breaker for unreachable derivatives feed.
                funding_unavailable=True
        elif not locals().get("funding_unavailable",False):
            try:
                series,metadata=fetch_funding(sym)
                funding[sym]=series;funding_reports.append(metadata)
            except Exception as ex:
                funding[sym]=pd.Series(dtype=float)
                funding_reports.append({"symbol":sym,"status":"SOURCE_UNAVAILABLE","error":str(ex)[:170]})
        else:
            funding[sym]=pd.Series(dtype=float)
            funding_reports.append({"symbol":sym,"status":"NOT_QUERIED_AFTER_SOURCE_BLOCKED"})
    if ("BTCUSDT","4h") not in market:raise RuntimeError("BTC_H4_MARKET_UNAVAILABLE")
    btc4=market[("BTCUSDT","4h")]
    hmm_states,hmm_meta=fit_hmm(btc4)
    all_events=[]
    for (sym,tf),frame in market.items():
        bt=market.get(("BTCUSDT",tf))
        if bt is None:continue
        feat=add_features(frame,bt,tf,funding[sym],hmm_states)
        rows=signals_and_labels(sym,tf,frame,feat)
        all_events.extend(rows)
        print("LABELS",sym,tf,len(rows),flush=True)
    events=pd.DataFrame(all_events)
    if events.empty:raise RuntimeError("NO_REAL_SIGNALS")
    events=events.reset_index(drop=True)
    events["uniqueness_weight"]=uniqueness_weights(events)
    events.to_parquet(ROOT/"ml_v2_signals.parquet",index=False)
    report=run_models(events)
    report["source"]={"prices":"Binance public unauthenticated spot 4h/daily klines",
                      "funding":"Binance public USD-M perpetual funding, missing is NaN not zero",
                      "requested_symbols":50,
                      "data_window":"2019-01-01 to 2023-12-31",
                      "2024_test_opened":False,
                      "universe_warning":"50 retro-selected symbols, not historical point-in-time top50",
                      "hmm_train":hmm_meta,
                      "price_manifest":manifests,
                      "funding_manifest":funding_reports}
    (ROOT/"ml_v2_comparison.json").write_text(json.dumps(report,indent=2,allow_nan=False,default=str)+"\n")
    print("FINAL_ML_VAL_2023",json.dumps({
         "train":report["counts"]["train"],"val":report["counts"]["validation"],
         "effective_unique_train":report["counts"]["effective_uniqueness_train_weight_sum"],
         "verdict":report["verdict"],
         "metrics":report.get("validation",{})}),flush=True)

if __name__=="__main__":main()
