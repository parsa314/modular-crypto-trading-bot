"""Synthetic proof that path-dependent barrier labels respect all input arrival times.

No real returns, model performance or scientific promotion are inferred.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd
import pytest

from research_bot.strategy_labels_012 import triple_barrier_label


ENTRY = datetime(2024, 1, 1, 1, tzinfo=timezone.utc)


def _bars(*, later_bar_hits: bool, timeout: bool = False):
    times = [ENTRY + timedelta(hours=i) for i in range(3)]
    high = [101, 103 if later_bar_hits else 101, 101]
    low = [99, 99, 99]
    if timeout:
        high = [101, 101, 101]
    # The first bar arrives late; the following event bar arrives earlier.
    available = [ENTRY + timedelta(hours=3),
                 ENTRY + timedelta(hours=2),
                 ENTRY + timedelta(hours=3)]
    return pd.DataFrame({
        "timestamp": times, "available_at": available, "high": high, "low": low
    })


def _args(**updates):
    values = dict(
        decision_at=ENTRY-timedelta(minutes=1),
        entry_at=ENTRY, entry_price=Decimal("100"),
        take_profit=Decimal("102"), stop_loss=Decimal("98"),
        timeframe="H1", horizon_bars=3, direction="LONG",
        as_of=ENTRY + timedelta(hours=4)
    )
    values.update(updates)
    return values


def test_tp_label_waits_for_late_preceding_input():
    result = triple_barrier_label(_bars(later_bar_hits=True), **_args())
    assert result.outcome == "TP"
    assert result.observed_bars == 2
    assert result.label_available_at == (ENTRY+timedelta(hours=3)).isoformat()


def test_ambiguous_label_waits_for_late_preceding_input():
    data=_bars(later_bar_hits=True)
    data.loc[1, "low"] = 97
    result = triple_barrier_label(data, **_args())
    assert result.outcome == "AMBIGUOUS"
    assert result.label_available_at == (ENTRY+timedelta(hours=3)).isoformat()


def test_timeout_label_waits_for_latest_consumed_availability():
    data = _bars(later_bar_hits=False, timeout=True)
    data.loc[0, "available_at"] = ENTRY + timedelta(hours=5)
    data.loc[1, "available_at"] = ENTRY + timedelta(hours=2)
    data.loc[2, "available_at"] = ENTRY + timedelta(hours=4)
    result = triple_barrier_label(data, **_args(as_of=ENTRY + timedelta(hours=6)))
    assert result.outcome == "TIMEOUT"
    assert result.label_available_at == (ENTRY+timedelta(hours=5)).isoformat()


def test_as_of_before_initial_bar_arrival_cannot_claim_label():
    # A later bar arriving earlier does not authorize reading a missing first bar.
    with pytest.raises(ValueError, match="MISSING_DUPLICATE_OR_MISALIGNED_LABEL_BARS"):
        triple_barrier_label(_bars(later_bar_hits=True),
                             **_args(as_of=ENTRY + timedelta(hours=2,minutes=30)))


def test_normal_in_order_arrivals_keep_original_event_time():
    data = _bars(later_bar_hits=True)
    data["available_at"] = [ENTRY+timedelta(hours=1),
                            ENTRY+timedelta(hours=2),
                            ENTRY+timedelta(hours=3)]
    result = triple_barrier_label(data, **_args())
    assert result.outcome == "TP"
    assert result.label_available_at == (ENTRY+timedelta(hours=2)).isoformat()
