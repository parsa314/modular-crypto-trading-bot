"""Explicit anti-lookahead invariant tests on completed OHLCV observations."""
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import pytest

from research_bot.strategies.common import closed_bars


def _bars(count=110):
    t = pd.date_range("2024-05-01", periods=count, freq="15min", tz="UTC")
    c = np.linspace(100.0, 111.0, count)
    return pd.DataFrame({"timestamp": t, "available_at": t + pd.Timedelta(minutes=15),
        "open": c, "high": c + .4, "low": c - .4, "close": c,
        "volume": np.ones(count)})


def test_no_lookahead_bias_future_contamination_ignored():
    """Mutation of arbitrarily large AFTER-as_of prices must not alter data."""
    frame = _bars()
    cutoff = frame.available_at.iloc[-1].to_pydatetime()
    expected = closed_bars(frame, as_of=cutoff)
    future = _bars(10)
    future["timestamp"] += pd.Timedelta(days=10)
    future["available_at"] += pd.Timedelta(days=10)
    future.loc[:, ("open", "high", "low", "close")] *= 1000000
    added = pd.concat([frame, future], ignore_index=True)
    actual = closed_bars(added, as_of=cutoff)
    pd.testing.assert_frame_equal(actual, expected)
    assert len(actual) == len(frame)


def test_mutated_unavailable_bar_must_not_repaint_history():
    frame = _bars()
    cutoff = frame.available_at.iloc[-2].to_pydatetime()
    baseline = closed_bars(frame, as_of=cutoff)
    frame.loc[frame.index[-1], "close"] = 999999.0
    frame.loc[frame.index[-1], "high"] = 1000000.0
    pd.testing.assert_frame_equal(closed_bars(frame, as_of=cutoff), baseline)


def test_unlabeled_candle_availability_fail_closed():
    frame = _bars()
    with pytest.raises(ValueError, match="MISSING_OHLCV"):
        closed_bars(frame.drop(columns=["available_at"]),
                    as_of=frame.available_at.iloc[-1].to_pydatetime())


def test_invalid_time_order_or_ohlcv_fails_closed():
    frame = _bars()
    frame.loc[3, "available_at"] = frame.timestamp.iloc[3] - pd.Timedelta(minutes=1)
    with pytest.raises(ValueError, match="AVAILABLE_BEFORE"):
        closed_bars(frame, as_of=frame.available_at.iloc[-1].to_pydatetime())
    bad = _bars()
    bad.loc[2, "high"] = 0.01
    with pytest.raises(ValueError, match="INCONSISTENT_OHLC"):
        closed_bars(bad, as_of=bad.available_at.iloc[-1].to_pydatetime())


def test_as_of_requires_timezone():
    frame = _bars()
    with pytest.raises(ValueError, match="timezone"):
        closed_bars(frame, as_of=datetime(2024,5,2))
