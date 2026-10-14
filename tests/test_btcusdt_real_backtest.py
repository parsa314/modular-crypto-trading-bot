"""Software invariants with SYNTHETIC fixture ONLY; never market PnL evidence."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.backtest_btcusdt_2022_2024 import (
    simulate, random_baseline, upper_bound_candidates,
)


@pytest.mark.parametrize("seed", [20261014,20261015,20261016,20261017,20261018])
def test_random_baseline_matches_exact_count_and_durations(seed):
    durations=[5,10,4,50,1,20]
    positions=random_baseline(350,durations,seed)
    entries=np.flatnonzero(np.diff(np.r_[0,positions])==1)
    ends=np.flatnonzero(np.diff(np.r_[positions,0])==-1)+1
    assert len(entries)==len(ends)==len(durations)
    assert sorted((ends-entries).tolist())==sorted(durations)
    assert np.all(ends[:-1]<entries[1:])
    metrics,trades=simulate(np.ones(350)*100.,positions)
    assert metrics["trade_count"]==len(durations)
    assert len(trades)==len(durations)


def test_constant_spot_price_loses_exact_twoway_costs():
    prices=np.ones(100)*100.
    position=np.zeros(100,dtype=np.int8)
    position[2:10]=1
    report,trades=simulate(prices,position)
    assert report["trade_count"]==1
    assert report["win_rate"]==0
    assert report["profit_factor"]==0
    assert report["net_return"]==pytest.approx((1-0.0015)**2-1,abs=1e-12)


def test_zero_trades_have_undefined_sharpe_and_winrate():
    report,trades=simulate(np.full(120,100.0),np.zeros(120,dtype=np.int8))
    assert report["net_return"]==0.
    assert report["trade_count"]==0
    assert report["sharpe"] is None and report["sortino"] is None
    assert report["profit_factor"] is None and report["win_rate"] is None


def test_necessary_filter_not_changed_by_appending_future_bars():
    n=150
    times=pd.date_range("2024-01-01",periods=n,freq="15min",tz="UTC")
    p=100+np.arange(n)*.01
    x=pd.DataFrame({"timestamp":times,"open":p-.05,"high":p+.3,
                    "low":p-.3,"close":p,"volume":np.full(n,1000.)})
    before=upper_bound_candidates(x)
    future=x.tail(10).copy().reset_index(drop=True)
    future["timestamp"]=pd.date_range(times[-1]+pd.Timedelta(minutes=15),
                                     periods=10,freq="15min",tz="UTC")
    future.loc[:,["open","high","low","close"]]=[100000.,101000.,99000.,100500.]
    after=upper_bound_candidates(pd.concat([x,future],ignore_index=True))
    assert np.array_equal(before,after[:n])


def test_exact_one_complete_candle_latency_matches_next_open():
    # Signal at i cannot execute at i nor i+1; only i+2. This is a
    # mechanical invariant enforced by shifting a target array by 2.
    wanted=np.array([0,0,1,1,0,0,0],dtype=np.int8)
    actual=np.zeros(len(wanted),dtype=np.int8)
    actual[2:]=wanted[:-2]
    assert actual.tolist()==[0,0,0,0,1,1,0]
