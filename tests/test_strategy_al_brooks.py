"""Al Brooks mechanical event tests, synthetic fixtures; never a PnL claim."""
import numpy as np
import pandas as pd
from research_bot.strategies.al_brooks_strategy import evaluate


def rising_bars():
    x=np.arange(130)
    p=100.0 + x*.1
    t=pd.date_range("2024-05-01", periods=len(x), freq="15min", tz="UTC")
    return pd.DataFrame(dict(timestamp=t, available_at=t+pd.Timedelta(minutes=15),
                             open=p-.1, high=p+.5, low=p-.5, close=p,
                             volume=np.full(len(x),100.)))


def test_brooks_quality_signal_in_causal_trend():
    df=rising_bars()
    df.loc[129,["open","high","low","close"]]=[112.,115.5,111.9,115.]
    vote=evaluate(df,as_of=df.available_at.iloc[-1].to_pydatetime())
    assert vote.style=="AL_BROOKS"
    assert vote.action=="BUY"
    assert vote.score==3
    assert vote.evidence["body_ratio"] >= .58
    assert vote.evidence["breakout"]==1.0


def test_brooks_unconfirmed_signal_is_hold():
    df=rising_bars()
    vote=evaluate(df,as_of=df.available_at.iloc[-1].to_pydatetime())
    assert vote.action=="HOLD"
    assert vote.score==0


def test_no_lookahead_bias_brooks_future_mutation():
    df=rising_bars()
    cutoff=df.available_at.iloc[-2].to_pydatetime()
    expected=evaluate(df,as_of=cutoff)
    df.loc[129,["open","high","low","close"]]=[1.e6,1.1e6,.9e6,1.e6]
    assert evaluate(df,as_of=cutoff)==expected
