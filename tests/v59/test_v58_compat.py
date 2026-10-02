from __future__ import annotations

import pytest

from research_bot.v59.compat_v58 import adapt_v58_signal


def test_v58_ambiguous_same_timestamp_is_not_silently_promoted():
    row = {
        "signal_id": "s1",
        "strategy_id": "FVG_ICT_TSI_MTF",
        "family": "FVG_ICT_TSI_MTF",
        "symbol": "BTC/USDT",
        "venue": "research_csv",
        "direction": "long",
        "signal_time": "2026-01-01T00:05:00+00:00",
        "entry_time": "2026-01-01T00:05:00+00:00",
        "entry_reference_price": 100.0,
        "stop_price": 99.0,
        "target_price": 102.0,
    }
    with pytest.raises(ValueError, match="strictly-after-decision"):
        adapt_v58_signal(row, data_version="d", strategy_version="s")


def test_v58_strict_time_signal_can_be_migrated_but_unknown_regime_stays_untrusted():
    row = {
        "signal_id": "s2",
        "strategy_id": "C10_01_TREND_PULLBACK_REJECTION",
        "family": "CONFLUENCE10",
        "symbol": "BTC/USDT",
        "venue": "research_csv",
        "direction": "long",
        "signal_time": "2026-01-01T00:00:00+00:00",
        "entry_time": "2026-01-01T00:05:00+00:00",
        "entry_reference_price": 100.0,
        "stop_price": 99.0,
        "target_price": 102.0,
        "confirmations": ["ICHIMOKU", "ICT", "SMC", "AL_BROOKS"],
    }
    event = adapt_v58_signal(row, data_version="d", strategy_version="s")
    assert event.regime.value == "UNKNOWN"
    assert event.regime_confidence == 0.0
