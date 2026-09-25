from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from research_bot.v58.barriers import PRIMARY_H4_BARRIER, build_barrier_prices
from research_bot.v58.contracts import Direction, RegimeLabel, StrategyArm, TargetClass
from research_bot.v58.data_freeze import FrozenFileSpec, frozen_60_20_20_split, verify_frozen_ohlcv
from research_bot.v58.events import EventRecord, make_event_id, make_feature_snapshot_id
from research_bot.v58.holdout import (
    PROSPECTIVE_HOLDOUT,
    PROSPECTIVE_HOLDOUT_START,
    assert_tuning_timestamp_allowed,
)
from research_bot.v58.manifest import stable_frame_hash, stable_schema_hash
from research_bot.v58.targets import OHLCBar, resolve_target


UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]


def _event(*, horizon: int = 30) -> EventRecord:
    decision = datetime(2026, 1, 1, 12, tzinfo=UTC)
    setup = decision - timedelta(hours=4)
    entry_time = decision + timedelta(hours=4)
    snapshot = make_feature_snapshot_id(
        event_timestamp=decision,
        feature_version="58.0-phase2",
        features={"ATR_percent": 0.02, "trend_strength": 0.4},
    )
    event_id = make_event_id(
        symbol="BTC/USDT",
        venue="coinex",
        strategy_arm=StrategyArm.ARM_A,
        direction=Direction.LONG,
        setup_timestamp=setup,
        entry_reference="next_bar_open",
        feature_snapshot_id=snapshot,
        strategy_version="58.0-phase2",
    )
    return EventRecord(
        event_id=event_id,
        symbol="BTC/USDT",
        venue="coinex",
        timestamp=decision,
        setup_timestamp=setup,
        strategy_arm=StrategyArm.ARM_A,
        direction=Direction.LONG,
        entry_reference="next_bar_open",
        entry_time=entry_time,
        entry_price=100.0,
        stop_price=50.0,
        target_price=150.0,
        risk_R=3.0,
        holding_horizon=horizon,
        decision_timeframe="4h",
        regime=RegimeLabel.TREND_UP,
        regime_confidence=0.8,
        feature_snapshot_id=snapshot,
        data_version="v58-data-freeze1",
        code_version="v58-phase2",
        strategy_version="58.0-phase2",
        available_at=decision,
        source_event_hash="a" * 64,
    )


def test_frozen_barrier_contract_exact():
    b = PRIMARY_H4_BARRIER
    assert b.timeframe == "4h"
    assert b.atr_period == 14
    assert b.stop_atr == 1.5
    assert b.min_stop_fraction == 0.0005
    assert b.reward_r == 3.0
    assert b.max_hold_bars_after_entry == 30
    assert b.entry_rule == "NEXT_EXECUTABLE_BAR_OPEN"
    assert b.same_bar_policy == "STOP_FIRST"


def test_barrier_prices_long_and_short():
    long = build_barrier_prices(entry_price=100.0, atr_at_signal=2.0, direction=Direction.LONG)
    assert long.stop_distance == 3.0
    assert long.stop_price == 97.0
    assert long.target_price == 109.0
    short = build_barrier_prices(entry_price=100.0, atr_at_signal=2.0, direction=Direction.SHORT)
    assert short.stop_distance == 3.0
    assert short.stop_price == 103.0
    assert short.target_price == 91.0


def test_barrier_minimum_distance_floor():
    out = build_barrier_prices(entry_price=100_000.0, atr_at_signal=1.0, direction=Direction.LONG)
    assert out.stop_distance == 50.0


def test_timeout_is_entry_bar_plus_30():
    event = _event(horizon=30)
    bars = [
        OHLCBar(event.entry_time + timedelta(hours=4 * i), 100.0, 101.0, 99.0, 100.0)
        for i in range(31)
    ]
    out = resolve_target(event, bars)
    assert out.target_class is TargetClass.TIMEOUT
    assert out.time_to_event_bars == 31
    assert out.resolved_at == event.entry_time + timedelta(hours=4 * 30)


def test_early_barrier_resolution_does_not_require_full_horizon():
    event = _event(horizon=30)
    bar = OHLCBar(event.entry_time, 100.0, 151.0, 99.0, 150.0)
    out = resolve_target(event, [bar])
    assert out.target_class is TargetClass.TP
    assert out.time_to_event_bars == 1


def test_holdout_seal_is_fixed_and_tuning_forbidden():
    assert PROSPECTIVE_HOLDOUT.bars == 540
    assert PROSPECTIVE_HOLDOUT.start_bar_open == datetime(2026, 9, 25, 16, tzinfo=UTC)
    assert PROSPECTIVE_HOLDOUT.last_bar_open == datetime(2026, 12, 24, 12, tzinfo=UTC)
    assert_tuning_timestamp_allowed(PROSPECTIVE_HOLDOUT_START - timedelta(seconds=1))
    with pytest.raises(RuntimeError, match="sealed prospective holdout"):
        assert_tuning_timestamp_allowed(PROSPECTIVE_HOLDOUT_START)


def test_data_manifest_freezes_real_artifacts_and_spent_status():
    manifest = json.loads((ROOT / "V58_DATA_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["historical_primary"]["workflow_run_id"] == 36042192410
    assert manifest["historical_primary"]["workflow_artifact_id"] == 10826344695
    assert manifest["historical_primary"]["research_spent"] is True
    assert manifest["historical_primary"]["pristine_holdout"] is False
    assert len(manifest["historical_primary"]["datasets"]) == 5
    assert manifest["historical_external_transfer"]["workflow_artifact_id"] == 10827012617
    assert manifest["prospective_final_temporal_holdout"]["bars"] == 540
    assert manifest["prospective_final_temporal_holdout"]["bytes_available"] is False
    assert manifest["training_authorized"] is False
    assert manifest["promotion_authorized"] is False


def test_frozen_split_boundaries_match_contract():
    ts = pd.date_range("2023-09-25T08:00:00Z", periods=6572, freq="4h")
    frame = pd.DataFrame({"timestamp": ts})
    split = frozen_60_20_20_split(frame)
    assert split["development"] == {
        "rows": 3943,
        "start": "2023-09-25T08:00:00+00:00",
        "end": "2025-07-13T08:00:00+00:00",
    }
    assert split["validation"] == {
        "rows": 1314,
        "start": "2025-07-13T12:00:00+00:00",
        "end": "2026-02-17T08:00:00+00:00",
    }
    assert split["internal_test_spent"] == {
        "rows": 1315,
        "start": "2026-02-17T12:00:00+00:00",
        "end": "2026-09-24T12:00:00+00:00",
    }


def test_verify_frozen_ohlcv_detects_tampering(tmp_path: Path):
    ts = pd.date_range("2026-01-01", periods=12, freq="4h", tz="UTC")
    frame = pd.DataFrame({
        "timestamp": ts,
        "open": [100.0] * 12,
        "high": [101.0] * 12,
        "low": [99.0] * 12,
        "close": [100.5] * 12,
        "volume": [1000.0] * 12,
    })
    path = tmp_path / "sample.csv.gz"
    frame.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0}, float_format="%.17g")
    loaded = pd.read_csv(path, compression="gzip")
    loaded["timestamp"] = pd.to_datetime(loaded["timestamp"], utc=True)
    spec = FrozenFileSpec(
        symbol="BTC/USDT",
        file_name=path.name,
        compressed_sha256=sha256(path.read_bytes()).hexdigest(),
        frame_sha256=stable_frame_hash(loaded),
        schema_sha256=stable_schema_hash(loaded),
        rows=12,
        first_bar=loaded["timestamp"].iloc[0].isoformat(),
        last_bar=loaded["timestamp"].iloc[-1].isoformat(),
    )
    assert verify_frozen_ohlcv(path, spec)["gap_intervals"] == 0
    broken = FrozenFileSpec(
        symbol=spec.symbol,
        file_name=spec.file_name,
        compressed_sha256="0" * 64,
        frame_sha256=spec.frame_sha256,
        schema_sha256=spec.schema_sha256,
        rows=spec.rows,
        first_bar=spec.first_bar,
        last_bar=spec.last_bar,
    )
    with pytest.raises(ValueError, match="compressed SHA-256 mismatch"):
        verify_frozen_ohlcv(path, broken)


def test_phase2_configs_keep_execution_and_training_closed():
    research = yaml.safe_load((ROOT / "configs/v58/research.yaml").read_text(encoding="utf-8"))
    events = yaml.safe_load((ROOT / "configs/v58/events.yaml").read_text(encoding="utf-8"))
    assert research["training_authorized"] is False
    assert research["paper_execution"] is False
    assert research["live_execution"] is False
    assert events["training_authorized"] is False
    assert events["common_primary_barrier"]["stop_atr"] == 1.5
    assert events["common_primary_barrier"]["reward_target_R"] == 3.0
    assert events["common_primary_barrier"]["max_hold_bars_after_entry"] == 30
    assert all(v["use_common_primary_barrier"] is True for v in events["arms"].values())
