"""ML-v2 invariants: synthetic data ONLY; real metrics are GitHub Actions output."""
from __future__ import annotations
from datetime import timezone
import numpy as np
import pandas as pd
from scripts.ml_v2_final import (SYMBOLS,uniqueness_weights,
                                 signals_and_labels,STEP,SL_ATR,TP_ATR,
                                 NUM_COLS,END,VAL)
def candles(n=280,tf="4h"):
    d=pd.date_range("2022-01-01",periods=n,freq=tf,tz="UTC")
    c=np.linspace(100,120,n)
    return pd.DataFrame({"open":c,"high":c+1,"low":c-1,
                "close":c,"volume":np.ones(n),"quote_volume":np.ones(n)*1000},index=d)

def test_exactly_fifty_symbols():
    assert len(SYMBOLS)==50 and len(set(SYMBOLS))==50

def test_volatility_barrier_is_asymmetric():
    assert TP_ATR==2.5 and SL_ATR==1

def test_2024_held_back():
    assert END==pd.Timestamp("2024-01-01",tz="UTC")
    assert VAL==pd.Timestamp("2023-01-01",tz="UTC")

def test_overlap_uniqueness_downweights():
    ev=pd.DataFrame({"symbol":["BTCUSDT"]*3,"timeframe":["4h"]*3,
                     "entry_i":[10,12,13],"end_i":[20,20,21]})
    w=uniqueness_weights(ev)
    assert np.all(w>0) and np.all(w<=1)
    assert np.any(w<1)

def test_no_synthetic_signal_when_indicators_invalid():
    p=candles()
    f=pd.DataFrame({col:np.nan for col in NUM_COLS},index=p.index)
    f["history_ok"]=False
    f["atr"]=np.nan
    f["donchian55"]=np.nan
    f["donchian20"]=np.nan
    f["regime"]="MISSING"
    assert signals_and_labels("BTCUSDT","4h",p,f)==[]
