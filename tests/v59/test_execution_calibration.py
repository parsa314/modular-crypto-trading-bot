from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from research_bot.v59.execution import ExecutionAssumptions
from research_bot.v59.execution_calibration import (
    ExecutionCalibrationConfig,
    ExecutionCalibrationSample,
    assumptions_from_calibration,
    fit_execution_calibration,
    samples_from_frame,
)
from research_bot.v59.hashing import stable_hash


UTC = timezone.utc
START = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


def sample(i: int, *, base_bps: float = 2.0, impact_coeff: float = 8.0, error_bps: float = 0.0):
    participation = 0.01 + (i % 20) * 0.005
    depth = 100_000.0
    notional = participation * depth
    spread_bps = 2.0
    residual = base_bps + impact_coeff * participation ** 0.5 + error_bps
    adverse = spread_bps / 2.0 + residual
    mid = 100.0
    side = "BUY" if i % 2 == 0 else "SELL"
    fill = mid * (1.0 + adverse / 10_000.0) if side == "BUY" else mid * (1.0 - adverse / 10_000.0)
    return ExecutionCalibrationSample(
        sample_id=f"s{i:04d}",
        timestamp=START + timedelta(minutes=i),
        side=side,
        reference_mid=mid,
        fill_price=fill,
        notional=notional,
        depth_notional_10bps=depth,
        spread_bps=spread_bps,
        fee_bps_per_side=5.0,
        source_hash=stable_hash({"sample": i}),
    )


def test_sample_requires_timezone_and_valid_side():
    with pytest.raises(ValueError):
        ExecutionCalibrationSample(
            sample_id="x",
            timestamp=datetime(2026, 1, 1),
            side="BUY",
            reference_mid=100,
            fill_price=100,
            notional=100,
            depth_notional_10bps=1000,
            spread_bps=2,
            fee_bps_per_side=5,
            source_hash="h",
        )
    with pytest.raises(ValueError):
        sample(1).__class__(**{**sample(1).__dict__, "side": "HOLD"})


def test_insufficient_sample_never_promotes():
    report = fit_execution_calibration([sample(i) for i in range(20)])
    assert report.status == "INSUFFICIENT_SAMPLE"
    with pytest.raises(ValueError, match="not eligible"):
        assumptions_from_calibration(report)


def test_exact_fixture_recovers_known_base_and_impact_coefficients():
    rows = [sample(i) for i in range(140)]
    report = fit_execution_calibration(rows)
    assert report.status == "CALIBRATION_CANDIDATE_PASSED"
    assert report.train_samples == 98
    assert report.validation_samples == 42
    assert report.fitted_base_bps_per_side == pytest.approx(2.0, abs=1e-8)
    assert report.fitted_impact_coefficient_bps == pytest.approx(8.0, abs=1e-8)
    assert report.validation_mae_bps == pytest.approx(0.0, abs=1e-8)
    assert report.train_end < report.validation_start


def test_validation_failure_rejects_candidate():
    rows = []
    for i in range(140):
        error = 0.0 if i < 98 else 20.0
        rows.append(sample(i, error_bps=error))
    report = fit_execution_calibration(rows)
    assert report.status == "CALIBRATION_CANDIDATE_REJECTED"
    assert report.validation_mae_bps is not None
    assert report.validation_mae_bps > 5.0


def test_promoted_assumptions_are_conservative_and_do_not_change_other_limits():
    report = fit_execution_calibration([sample(i) for i in range(140)])
    base = ExecutionAssumptions(
        taker_fee_bps_per_side=6.0,
        latency_stress_bps_per_side=1.5,
        max_depth_participation=0.15,
    )
    promoted = assumptions_from_calibration(report, base)
    assert promoted.taker_fee_bps_per_side == 6.0
    assert promoted.latency_stress_bps_per_side == 1.5
    assert promoted.max_depth_participation == 0.15
    assert promoted.slippage_stress_bps_per_side >= 2.0
    assert promoted.impact_coefficient_bps == pytest.approx(8.0, abs=1e-8)


def test_duplicate_sample_id_is_forbidden():
    rows = [sample(i) for i in range(100)]
    rows.append(rows[-1])
    with pytest.raises(ValueError, match="duplicate"):
        fit_execution_calibration(rows)


def test_samples_from_frame_requires_timezone_and_schema():
    rows = [sample(i) for i in range(3)]
    frame = pd.DataFrame(
        [
            {
                "sample_id": row.sample_id,
                "timestamp": row.timestamp.isoformat(),
                "side": row.side,
                "reference_mid": row.reference_mid,
                "fill_price": row.fill_price,
                "notional": row.notional,
                "depth_notional_10bps": row.depth_notional_10bps,
                "spread_bps": row.spread_bps,
                "fee_bps_per_side": row.fee_bps_per_side,
                "source_hash": row.source_hash,
            }
            for row in rows
        ]
    )
    parsed = samples_from_frame(frame)
    assert len(parsed) == 3
    assert parsed[0].timestamp.tzinfo is not None

    bad = frame.drop(columns=["depth_notional_10bps"])
    with pytest.raises(ValueError, match="missing calibration columns"):
        samples_from_frame(bad)


def test_report_hash_is_stable():
    report1 = fit_execution_calibration([sample(i) for i in range(140)])
    report2 = fit_execution_calibration([sample(i) for i in range(140)])
    assert report1.report_hash == report2.report_hash
