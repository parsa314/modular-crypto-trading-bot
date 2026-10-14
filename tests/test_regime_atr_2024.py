"""Synthetic-only invariant tests, NOT market-performance or HMM quality proof."""
from __future__ import annotations
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest

from research_bot.regime_detector import (
    FittedRegime,causal_regime_features,TRAIN_END,
)
from research_bot.regime_exit_lab import simulate_atr_exits
from research_bot.regime_statistics import cscv_pbo_6,deflated_sharpe_2024


def candles(n=150):
    t=pd.date_range("2023-12-30",periods=n,freq="15min",tz="UTC")
    c=100+np.arange(n)*.05
    return pd.DataFrame({"timestamp":t,"available_at":t+pd.Timedelta(minutes=15),
                         "open":c,"high":c+1,"low":c-1,"close":c,
                         "volume":np.ones(n)*1000})


def test_regime_features_do_not_repaint_after_future_append():
    df=candles()
    prev=causal_regime_features(df)
    next_=df.tail(5).copy()
    next_["timestamp"]=pd.date_range(df.timestamp.iloc[-1]+pd.Timedelta(minutes=15),
                                        periods=5,freq="15min",tz="UTC")
    next_["close"]=next_["open"]=100000.
    next_["high"]=100001.
    next_["low"]=99999.
    pd.testing.assert_frame_equal(
        causal_regime_features(pd.concat([df,next_],ignore_index=True)).iloc[:len(df)],
        prev,check_exact=True)


def test_hmm_forward_filter_does_not_peek_at_future():
    df=candles()
    fake=SimpleNamespace(
        startprob_=np.array([.5,.3,.2]),
        transmat_=np.array([[.8,.1,.1],[.2,.6,.2],[.15,.15,.7]]),
        means_=np.array([[-9.,.2,-4.],[-8.,.6,-4.],[-7.,.8,-3.]]),
        covars_=np.ones((3,3))*.8,
    )
    class Identity:
        def transform(self,v):
            return np.asarray(v,dtype=float)
    mod=FittedRegime(fake,Identity(),{0:"RANGE",1:"TREND",2:"VOLATILE"},
                     ("log_abs_return","efficiency","log_atr_pct"),100,"2023-12-31")
    before=mod.causal_inference(df)
    df_future=pd.concat([df,df.tail(1)],ignore_index=True)
    df_future.loc[len(df),"timestamp"]=df.timestamp.iloc[-1]+pd.Timedelta(minutes=15)
    df_future.loc[len(df),"available_at"]=df.available_at.iloc[-1]+pd.Timedelta(minutes=15)
    df_future.loc[len(df),["open","high","low","close"]]=[100000.,100001.,99999.,100000.]
    after=mod.causal_inference(df_future)
    assert after.regime.iloc[:len(df)].tolist()==before.regime.tolist()
    np.testing.assert_allclose(after.regime_confidence.iloc[:len(df)],
                               before.regime_confidence)


def test_atr_stop_first_wins_over_intrabar_both_target_and_stop():
    df=candles(85)
    for k in ["open","high","low","close"]:
        df[k]=100. if k in ("open","close") else (101. if k=="high" else 99.)
    df.loc[25,["open","high","low","close"]]=[100.,120.,95.,102.]
    enters=np.zeros(len(df),dtype=np.int8)
    enters[25]=1
    m,trades,nav=simulate_atr_exits(df,enters,np.full(len(df),"RANGE"))
    assert m["trade_count"]==1
    assert trades[0]["reason"]=="ATR_STOP_FIRST"
    assert trades[0]["hit1"] is False
    assert m["net_return"]<0


def test_atr_20_candles_no_1r_scheduled_next_open():
    df=candles(85)
    for k in ["open","high","low","close"]:
        df[k]=100. if k in ("open","close") else (101. if k=="high" else 99.)
    ent=np.zeros(len(df),dtype=np.int8);ent[25]=1
    stats,trades,nav=simulate_atr_exits(df,ent,np.full(len(df),"TREND"))
    assert stats["trade_count"]==1
    assert trades[0]["reason"]=="TIME_20_NOT_1R"
    assert trades[0]["exit_bar"]==45
    assert stats["net_return"]<0


def test_pbo_requires_complete_strategy_trials():
    x=cscv_pbo_6({7:np.zeros(1000),8:np.ones(1000)*.01})
    assert x["valid"] is False
    assert x["pbo"] is None


def test_dsr_reports_null_without_valid_estimate():
    x=deflated_sharpe_2024(np.array([.01,-.01]),{6:np.zeros(50),7:np.ones(50)})
    assert x["valid"] is False and x["dsr"] is None
