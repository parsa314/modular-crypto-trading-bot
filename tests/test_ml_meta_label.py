"""Real pipeline arithmetic and leakage sentinels (synthetic fixtures only)."""
from __future__ import annotations
from datetime import timedelta
import numpy as np
import pandas as pd
import pytest
from ml.data_loader import SYMBOLS
from ml.triple_barrier import label_event
from ml.meta_labeler import purged_folds,ml_experiment
from ml.feature_engineer import build_features

def ohlcv(rows=250):
    times=pd.date_range("2020-01-01",periods=rows,freq="D",tz="UTC")
    close=np.full(rows,100.,dtype=float)
    return pd.DataFrame({"open":close,"high":close+1,"low":close-1,
                         "close":close,"volume":np.ones(rows)*100,
                         "quote_volume":np.ones(rows)*10000},index=times)

def test_exactly_fifty():
    assert len(SYMBOLS)==len(set(SYMBOLS))==50

def test_stop_first_ambiguous_daily_bar():
    df=ohlcv()
    df.loc[df.index[21],["open","high","low","close"]]=[100,110,90,100]
    f={"hour":0.,"day_of_week":1}
    e=label_event(df,"BTCUSDT",21,df.index[20]+pd.Timedelta(days=1),
                  2.,"v4_donchian55",1.,f)
    assert e["barrier"]=="SL"
    assert e["label"]==0
    assert e["net_pnl"]<0
    assert e["event_end"]==df.index[21]

def test_take_profit_when_stop_not_touched():
    df=ohlcv()
    df.loc[df.index[21],["open","high","low","close"]]=[100,104,99,103]
    x=label_event(df,"BTCUSDT",21,df.index[20]+pd.Timedelta(days=1),
                  2.,"v6_atr_breakout",1.,{"rsi":60})
    assert x["barrier"]=="TP"
    assert x["label"]==1
    assert x["net_pnl"]>0

def test_no_future_repainting_of_feature_engineering():
    df=ohlcv(270)
    orig=build_features(df,df)
    more=ohlcv(280)
    more.loc[more.index[-10:],["open","close","high","low"]]=[9999,9999,10000,9998]
    new=build_features(more,more)
    pd.testing.assert_frame_equal(orig,new.iloc[:270])

def test_train_purged_by_event_end_before_validation():
    times=pd.date_range("2019-06-01",periods=700,freq="D",tz="UTC")
    df=pd.DataFrame({"signal_time":times,"event_end":times+pd.Timedelta(days=20)})
    splits=list(purged_folds(df))
    assert splits
    for a,b in splits:
        assert (df.event_end.iloc[a].max()+pd.Timedelta(days=5)
                <df.signal_time.iloc[b].min())
        assert not set(a).intersection(set(b))

def test_models_do_not_claim_metric_without_classes():
    df=pd.DataFrame({
        "symbol":["BTCUSDT"]*180,
        "strategy_id":["v5_rsi14_meanrev"]*180,
        "vol_regime":["LOW"]*180,
        "signal_time":pd.date_range("2019-01-01",periods=180,freq="D",tz="UTC"),
        "event_end":pd.date_range("2019-01-01",periods=180,freq="D",tz="UTC")+pd.Timedelta(days=5),
        "label":np.zeros(180,dtype=int),
        "net_pnl":np.full(180,-.01),
        **{c:np.ones(180) for c in ["confidence","atr_pct","atr20ratio","vol_ratio","rsi",
              "distance_sma50","distance_sma200","macd_pct","bb_position",
              "corr_btc","hour","day_of_week","btc_volatility"]}
    })
    counts,metrics=ml_experiment(df)
    assert metrics["status"]=="INSUFFICIENT_TRAIN_OR_VALIDATION_FOR_MODEL"
    assert counts["test_reused"]==0
