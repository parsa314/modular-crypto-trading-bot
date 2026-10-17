"""Phase 2 — real Binance historical signals, triple barriers and XGBoost.

Train 2019-2022, validation 2023. ZERO ACCESS TO 2024-2026 DATA.
Historical asset roster is an explicitly retrospective 50-name PROXY.
NO execution/adapters, credentials, order access or RL.
"""
from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
from ml.data_loader import (DATA_START,TRAIN_END,VALID_END,TEST_END,
                            SYMBOLS,read_public_klines)
from ml.feature_engineer import build_features,add_btc_regime,FEATS
from ml.triple_barrier import label_event,HORIZON
from ml.meta_labeler import purged_folds,ml_experiment

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
                            float(f.atr),name,c,features)
            if row is not None:candidates.append(row)
    return candidates

def m15_v1_events(filename,manifest_file,btc_daily):
    """Authentic original v1 BUY transitions, not a daily-price imitation."""
    from scripts.backtest_btcusdt_2022_2024 import read_verified,confluence_positions
    d,_=read_verified(filename,manifest_file)
    d=d.loc[d.timestamp<pd.Timestamp(VALID_END,tz="UTC")].copy().reset_index(drop=True)
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
                        a,"v1_original_four_style",1.0,features,kind="M15")
        if row is not None:rows.append(row)
    return rows


def main():
    if TEST_END!="2024-01-01":
        raise RuntimeError("FORBIDDEN_HOLDOUT_DATA_ACCESS")
    out=Path("results")
    out.mkdir(exist_ok=True)
    manifest=[]
    market={}
    for symbol in SYMBOLS:
        try:
            p,record=read_public_klines(symbol)
        except Exception as exc:
            p=None
            record={"symbol":symbol,"status":"UNAVAILABLE_NOT_IMPUTED",
                    "reason":str(exc)[:200],"rows":0}
        manifest.append(record)
        if p is not None and len(p)>=280:market[symbol]=p
        print("HISTORICAL_DATA",symbol,record["status"],record["rows"],flush=True)
    if "BTCUSDT" not in market:raise RuntimeError("BTC_REFERENCE_UNAVAILABLE")
    btc=market["BTCUSDT"]
    events=[]
    for s,p in market.items():
        feat=build_features(p,btc)
        signal=day_signals(s,p,feat)
        events.extend(signal)
        print("CAUSAL_DAILY_SIGNALS",s,len(signal),flush=True)
    original=m15_v1_events(
        Path("data/btc_usdt_m15_2022_2024.parquet"),
        Path("data/btc_usdt_m15_2022_2024.manifest.json"),btc)
    events.extend(original)
    print("AUTHENTIC_V1_BTC_M15_BUY_EVENTS_PRE_2024",len(original),flush=True)
    data=pd.DataFrame(events)
    if data.empty:raise RuntimeError("ZERO_REAL_PRICE_SIGNALS")
    dates=pd.to_datetime(data.signal_time,utc=True)
    if (dates>=pd.Timestamp(VALID_END,tz="UTC")).any():
        raise RuntimeError("TEST_DATA_EXPOSURE_FORBIDDEN")
    regime=add_btc_regime(data,btc)
    data["hour"]=dates.dt.hour.astype(float)
    data["day_of_week"]=dates.dt.dayofweek.astype(float)
    data.to_parquet(out/"signals.parquet",index=False)
    counts,comparison=ml_experiment(data,market=market)
    counts.update({"historical_universe_requested":50,
                   "historical_universe_with_sufficient_bars":len(market),
                   "historical_manifest":manifest,
                   "v1_authentic_M15_BTC_events_pre_2024":len(original),
                   "minimum_5000_rows_met":len(data)>=5000,
                   "regime":regime,
                   "sample_limitations":[
                     "Retrospectively selected 50 names, not point-in-time historical top 50; survivorship bias",
                     "V4 V5 V6 are individual signal entries, NOT independent portfolio fills",
                     "V1 M15 is only BTC 2022-23 real archived data; not copied to other coins",
                     "Triple barrier TP2ATR SL1ATR TIME20 bars STOP_FIRST, long-only with 15bps each way",
                     "Train 2019-22, validation 2023; no 2024-26 dataset loaded/fetched",
                     "2024-26 has ALREADY been exposed in earlier v6 work and is not virgin OOS" ]})
    decision={"n_total":len(data),"n_labeled":int(data.label.notna().sum()),
              "n_positive":int(data.label.sum()),
              "n_negative":int((1-data.label).sum()),
              "minimum_5000_rows_met":len(data)>=5000,
              "decision":"ML_TRAINED_EXPLORATORY" if comparison.get("status","").startswith("TRAINED") else "INSUFFICIENT_DATA_FOR_NUMERIC_MODEL",
              "2024_2026_holdout_accessed":False,
              "caveat":"Previous v6 research already used this holdout historically; no new access by this job."}
    (out/"signal_count.json").write_text(json.dumps(counts,indent=2,default=str,allow_nan=False)+"\n")
    (out/"ml_data_decision.json").write_text(json.dumps(decision,indent=2,allow_nan=False)+"\n")
    (out/"ml_comparison.json").write_text(json.dumps(comparison,indent=2,allow_nan=False)+"\n")
    (out/"ml_filtered_backtest.json").write_text(json.dumps({
       "raw":comparison.get("validation_portfolio_raw"),
       "filtered":comparison.get("validation_portfolio_filtered"),
       "delta_sharpe":comparison.get("validation_sharpe_delta"),
       "source":"Real Binance daily prices 2019-2023; BTC real M15 2022-23",
       "period":"2023-01-01/2023-12-31","not_virgin_test":True},indent=2,allow_nan=False)+"\n")
    print("REAL_SIGNAL_COUNT",len(data),"TRAIN",counts["train"],
          "VAL",counts["validation"],"POS",decision["n_positive"],flush=True)
    print("REAL_ML_VALIDATION",json.dumps({
        "status":comparison.get("status"),
        "precision_top_decile":comparison.get("validation_precision_top_decile"),
        "auc":comparison.get("validation_auc"),
        "sharpe_raw":comparison.get("validation_portfolio_raw",{}).get("sharpe"),
        "sharpe_filtered":comparison.get("validation_portfolio_filtered",{}).get("sharpe"),
        "sharpe_delta":comparison.get("validation_sharpe_delta")}),flush=True)

if __name__=="__main__":main()
