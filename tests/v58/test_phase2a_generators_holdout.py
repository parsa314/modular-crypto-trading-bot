from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from research_bot.v58.contracts import StrategyArm
from research_bot.v58.generators import generate_candidates
from research_bot.v58.holdout import Partition, TemporalSeal, authorize_partition_access


UTC = timezone.utc


def _trend(n: int = 260) -> pd.DataFrame:
    ts = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    close = np.linspace(100, 180, n)
    close[-1] = close[-2] + 10
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"timestamp": ts, "open": open_, "high": np.maximum(open_, close)+0.4,
                         "low": np.minimum(open_, close)-0.4, "close": close,
                         "volume": np.full(n, 1000.0)})


def test_arm_a_is_deterministic_and_uses_bar_close_timestamp():
    frame = _trend()
    first = generate_candidates(frame, venue="coinex", symbol="BTC/USDT", timeframe="4h")
    second = generate_candidates(frame.copy(), venue="coinex", symbol="BTC/USDT", timeframe="4h")
    assert [e.event_id for e in first] == [e.event_id for e in second]
    arm_a = [e for e in first if e.strategy_arm is StrategyArm.ARM_A]
    assert arm_a
    source_open = frame.loc[arm_a[-1].row_index, "timestamp"].to_pydatetime()
    assert arm_a[-1].event_timestamp > source_open
    assert arm_a[-1].setup_subtype == "S6_BREAKOUT"


def test_future_mutation_does_not_change_past_event_ids():
    frame = _trend()
    cutoff = 230
    a = generate_candidates(frame.iloc[:cutoff].copy(), venue="coinex", symbol="BTC/USDT")
    changed = frame.copy()
    changed.loc[cutoff:, ["open", "high", "low", "close", "volume"]] *= 7
    b = generate_candidates(changed.iloc[:cutoff].copy(), venue="coinex", symbol="BTC/USDT")
    assert [x.event_id for x in a] == [x.event_id for x in b]


def test_final_holdout_fails_closed_for_selection_and_training():
    seal = TemporalSeal(datetime(2025,1,1,tzinfo=UTC), datetime(2025,2,1,tzinfo=UTC),
                        datetime(2025,3,1,tzinfo=UTC), "a"*64)
    assert seal.partition_for(datetime(2025,2,15,tzinfo=UTC)) is Partition.FINAL_HOLDOUT
    for purpose in ("TRAIN", "CALIBRATE", "MODEL_SELECTION", "FIT_PREPROCESSOR"):
        with pytest.raises(PermissionError):
            authorize_partition_access(Partition.FINAL_HOLDOUT, purpose=purpose)
    authorize_partition_access(Partition.FINAL_HOLDOUT, purpose="FINAL_EVALUATION")
