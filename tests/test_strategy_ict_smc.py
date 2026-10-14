"""Mechanical ICT and SMC setup tests; synthetic fixtures only, not alpha."""
import numpy as np
import pandas as pd
from research_bot.strategies.ict_strategy import evaluate as ict
from research_bot.strategies.smc_strategy import evaluate as smc


def fixture():
    times = pd.date_range("2024-05-01T00:00:00Z", periods=130, freq="15min", tz="UTC")
    return pd.DataFrame(dict(timestamp=times, available_at=times+pd.Timedelta(minutes=15),
                             open=np.full(130,100.), high=np.full(130,101.),
                             low=np.full(130,99.), close=np.full(130,100.),
                             volume=np.full(130,1000.)))


def test_ict_prior_sweep_then_mss_is_candidate():
    df=fixture()
    df.loc[127,"low"]=97.0  # prior-bar sweep through old lows
    df.loc[129,["high","close"]]=[105.0,104.0]  # confirmed MSS and displacement
    got=ict(df, as_of=df.available_at.iloc[-1].to_pydatetime())
    assert got.style=="ICT"
    assert got.action=="BUY"
    assert 2 <= got.score <= 3
    assert got.evidence["sweep_bars_ago"]==2.0


def test_smc_bos_uses_previous_opposing_bar_only():
    df=fixture()
    df.loc[127,["open","close"]]=[100.2,99.5]  # completed bearish OB proxy
    df.loc[129,["open","high","low","close"]]=[102.3,106.,102.,105.]
    got=smc(df, as_of=df.available_at.iloc[-1].to_pydatetime())
    assert got.style=="SMC"
    assert got.action=="BUY"
    assert got.score==3
    assert got.evidence["fvg"]==1.0


def test_no_lookahead_bias_ict_and_smc():
    df=fixture()
    cutoff=df.available_at.iloc[-2].to_pydatetime()
    a,b=ict(df,as_of=cutoff),smc(df,as_of=cutoff)
    future=df.copy()
    future.loc[129,["open","high","low","close"]]=[500000.,500100.,499900.,500050.]
    assert ict(future,as_of=cutoff)==a
    assert smc(future,as_of=cutoff)==b


def test_future_pivot_cannot_confirm_historical_structure():
    df=fixture()
    cutoff=df.available_at.iloc[-1].to_pydatetime()
    initial=(ict(df,as_of=cutoff), smc(df,as_of=cutoff))
    additional=df.tail(1).copy()
    additional["timestamp"]+=pd.Timedelta(hours=1)
    additional["available_at"]+=pd.Timedelta(hours=1)
    additional.loc[:,["open","high","low","close"]]=[500.,550.,450.,540.]
    x=pd.concat([df,additional],ignore_index=True)
    assert (ict(x,as_of=cutoff),smc(x,as_of=cutoff))==initial
