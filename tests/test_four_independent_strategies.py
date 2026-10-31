"""Synthetic *software* fixtures; NOT historical data, backtests, or ROI evidence."""
from __future__ import annotations

from datetime import timedelta
import numpy as np
import pandas as pd
import pytest

from research_bot.strategies import (
    StrategySignal, closed_bars, evaluate_all, combine,
)
from research_bot.strategies.common import result


@pytest.fixture
def synthetic_m15():
    # Invented prices ONLY to exercise causal code paths; never use for alpha claims.
    n = 120
    idx = pd.date_range("2024-01-01T00:00:00Z", periods=n, freq="15min", tz="UTC")
    px = 100.0 + np.arange(n) * .025 + np.sin(np.arange(n) / 5.0)
    return pd.DataFrame({
        "timestamp": idx,
        "available_at": idx + pd.Timedelta(minutes=15),
        "open": px - .12, "high": px + .8, "low": px - .8,
        "close": px, "volume": np.full(n, 1500.0),
    })


def test_each_family_has_closed_bar_typed_output(synthetic_m15):
    cutoff = synthetic_m15.available_at.iloc[-1].to_pydatetime()
    signals = evaluate_all(synthetic_m15, as_of=cutoff)
    assert set(signals) == {"ICHIMOKU", "ICT", "SMC", "AL_BROOKS"}
    for key, signal in signals.items():
        assert signal.style == key
        assert signal.action in {"BUY", "SELL", "HOLD"}
        assert isinstance(signal.score, int) and 0 <= signal.score <= 3
        assert signal.decision_at == cutoff


def test_all_four_ignore_future_market_candles(synthetic_m15):
    cutoff = synthetic_m15.available_at.iloc[-1].to_pydatetime()
    expected = evaluate_all(synthetic_m15, as_of=cutoff)
    idx = pd.date_range(synthetic_m15.timestamp.iloc[-1] + pd.Timedelta(minutes=15),
                        periods=5, freq="15min", tz="UTC")
    future = pd.DataFrame({
        "timestamp": idx, "available_at": idx + pd.Timedelta(minutes=15),
        "open": [1e6] * 5, "high": [1e6+1] * 5,
        "low": [1e6-1] * 5, "close": [1e6] * 5, "volume": [1e8] * 5,
    })
    actual = evaluate_all(pd.concat([synthetic_m15, future], ignore_index=True), as_of=cutoff)
    assert actual == expected


def test_unavailable_latest_bar_is_not_used(synthetic_m15):
    cutoff = synthetic_m15.available_at.iloc[-2].to_pydatetime()
    filtered = closed_bars(synthetic_m15, as_of=cutoff)
    assert len(filtered) == len(synthetic_m15) - 1
    assert all(s.decision_at == cutoff for s in evaluate_all(synthetic_m15, as_of=cutoff).values())


@pytest.mark.parametrize("corruption", ["bad_high", "negative_volume", "duplicate", "nonfinite"])
def test_market_data_contract_fail_closed(synthetic_m15, corruption):
    frame = synthetic_m15.copy()
    if corruption == "bad_high":
        frame.loc[8, "high"] = 1.0
    elif corruption == "negative_volume":
        frame.loc[8, "volume"] = -1
    elif corruption == "duplicate":
        frame.loc[8, "timestamp"] = frame.timestamp.iloc[7]
    else:
        frame.loc[8, "close"] = np.inf
    with pytest.raises(ValueError):
        closed_bars(frame, as_of=frame.available_at.iloc[-1].to_pydatetime())


def test_missing_availability_never_backfilled(synthetic_m15):
    with pytest.raises(ValueError, match="MISSING_OHLCV"):
        closed_bars(synthetic_m15.drop(columns=["available_at"]),
                    as_of=synthetic_m15.available_at.iloc[-1].to_pydatetime())


def _signals(at, scores, actions):
    return {name: StrategySignal(name, action, score, at)
            for name, action, score in zip(("ICHIMOKU","ICT","SMC","AL_BROOKS"), actions, scores)}


def test_confluence_equal_weight_seven_of_twelve():
    at = pd.Timestamp("2024-01-01T00:15:00Z").to_pydatetime()
    signals = _signals(at, [3, 3, 1, 0], ["BUY", "BUY", "BUY", "HOLD"])
    answer = combine(signals, as_of=at)
    assert answer.action == "BUY"
    assert answer.score_0_to_12 == 7
    assert len(answer.agreeing_styles) == 3


def test_confluence_rejects_mixed_sides_even_if_strong():
    at = pd.Timestamp("2024-01-01T00:15:00Z").to_pydatetime()
    x = _signals(at, [3, 3, 3, 0], ["BUY", "BUY", "SELL", "HOLD"])
    result_ = combine(x, as_of=at)
    assert result_.action == "HOLD"
    assert result_.reason == "CONFLICT_VETO"


def test_confluence_requires_three_agreeing_families():
    at = pd.Timestamp("2024-01-01T00:15:00Z").to_pydatetime()
    x = _signals(at, [3, 3, 0, 0], ["BUY", "BUY", "HOLD", "HOLD"])
    assert combine(x, as_of=at).reason == "INSUFFICIENT_INDEPENDENT_AGREEMENT"


def test_confluence_subthreshold_stays_flat():
    at = pd.Timestamp("2024-01-01T00:15:00Z").to_pydatetime()
    x = _signals(at, [2, 2, 2, 0], ["SELL", "SELL", "SELL", "HOLD"])
    assert combine(x, as_of=at).reason == "SCORE_BELOW_PREREGISTERED_THRESHOLD"


def test_reject_future_or_misaligned_votes():
    at = pd.Timestamp("2024-01-01T00:15:00Z").to_pydatetime()
    x = _signals(at, [3, 3, 1, 0], ["BUY", "BUY", "BUY", "HOLD"])
    with pytest.raises(ValueError, match="FUTURE_SIGNAL"):
        combine(x, as_of=at - timedelta(seconds=1))
    x["SMC"] = StrategySignal("SMC", "BUY", 1, at + timedelta(minutes=1))
    with pytest.raises(ValueError, match="MISALIGNED_SIGNALS"):
        combine(x, as_of=at + timedelta(minutes=1))


def test_weight_and_style_validation():
    at = pd.Timestamp("2024-01-01T00:15:00Z").to_pydatetime()
    x = _signals(at, [3, 3, 1, 0], ["BUY", "BUY", "BUY", "HOLD"])
    with pytest.raises(ValueError, match="WEIGHTS_MUST_SUM"):
        combine(x, as_of=at, weights={k: 0.1 for k in x})
    with pytest.raises(ValueError, match="EXACT_FOUR"):
        combine({k: v for k, v in x.items() if k != "SMC"}, as_of=at)


def test_future_timestamp_cannot_be_unaware(synthetic_m15):
    with pytest.raises(ValueError, match="timezone"):
        evaluate_all(synthetic_m15, as_of=pd.Timestamp("2024-01-02").to_pydatetime())


def test_signal_scores_have_no_arbitrary_hold_confidence(synthetic_m15):
    filtered = closed_bars(synthetic_m15,
                           as_of=synthetic_m15.available_at.iloc[-1].to_pydatetime())
    held = result("ICT", filtered, 0, 3, "some other earlier pattern")
    assert held.score == 0
    assert held.action == "HOLD"
