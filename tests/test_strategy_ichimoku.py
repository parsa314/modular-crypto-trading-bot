"""Ichimoku-only causal event regression; fixture prices are synthetic."""
import numpy as np
import pandas as pd
import pytest

from research_bot.strategies.ichimoku_strategy import evaluate


def bars():
    t = pd.date_range("2024-01-01", periods=130, freq="15min", tz="UTC")
    y = 100 + np.arange(130) * .08 + np.sin(np.arange(130) / 4) * .1
    return pd.DataFrame(dict(timestamp=t, available_at=t+pd.Timedelta(minutes=15),
                             open=y-.1, high=y+.7, low=y-.7, close=y,
                             volume=np.full(130, 800)))


def test_ichimoku_signal_type_and_probability_not_claimed():
    df = bars()
    signal = evaluate(df, as_of=df.available_at.iloc[-1].to_pydatetime())
    assert signal.style == "ICHIMOKU"
    assert signal.action in {"BUY", "SELL", "HOLD"}
    assert signal.score in {0,1,2,3}
    assert signal.decision_at == df.available_at.iloc[-1].to_pydatetime()


def test_no_lookahead_bias_ichimoku_mutation():
    df = bars()
    now = df.available_at.iloc[-2].to_pydatetime()
    initial = evaluate(df, as_of=now)
    df.loc[129, "open"] = 100000.0
    df.loc[129, "high"] = 100001.0
    df.loc[129, "low"] = 99999.0
    df.loc[129, "close"] = 100000.0
    assert evaluate(df, as_of=now) == initial


def test_ichimoku_future_data_cannot_create_confidence():
    df = bars()
    now = df.available_at.iloc[-1].to_pydatetime()
    future = df.tail(1).copy()
    future["timestamp"] += pd.Timedelta(days=10)
    future["available_at"] += pd.Timedelta(days=10)
    future["open"] = 5_000_000.
    future["high"] = 5_000_001.
    future["low"] = 4_999_999.
    future["close"] = 5_000_000.
    assert evaluate(pd.concat([df, future], ignore_index=True), as_of=now) == evaluate(df, as_of=now)
