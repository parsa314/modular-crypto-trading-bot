import copy

import pandas as pd

from research_bot.integrity_v19 import finalize_payload_hash
from research_bot.phase_q_v20 import (
    PHASE_Q_MATURITY_NOT_BEFORE,
    PHASE_Q_PILOT_START_AT,
    evaluate_phase_q,
    phase_q_protocol_metadata,
    validate_phase_q_snapshot,
)


START = pd.Timestamp(PHASE_Q_PILOT_START_AT)
MATURITY = pd.Timestamp(PHASE_Q_MATURITY_NOT_BEFORE)


def _snapshot(i: int, ts: pd.Timestamp, *, event: str = "schedule", ref: str = "refs/heads/main", authorized: bool = True) -> dict:
    def row(symbol: str) -> dict:
        venues = [
            {"venue": "coinex", "trade_staleness_seconds": 2.0},
            {"venue": "okx", "trade_staleness_seconds": 3.0},
            {"venue": "kucoin", "trade_staleness_seconds": 4.0},
        ] if authorized else []
        return {
            "symbol": symbol,
            "feature_authorized": authorized,
            "accepted_venues": 3 if authorized else 0,
            "accepted_venue_names": ["coinex", "okx", "kucoin"] if authorized else [],
            "venue_clock_skew_seconds": 5.0 if authorized else 120.0,
            "venues": venues,
            "quality_flags": [] if authorized else ["QUALITY_GATED"],
        }

    payload = {
        "version": "v0.19",
        "research_status": "PROSPECTIVE_MULTI_VENUE_MICROSTRUCTURE_COLLECTION_ONLY",
        "generated_at": ts.isoformat(),
        "collection_rule": "prospective_only_fixed_window_no_backfill_no_signal",
        "phase_q_protocol": phase_q_protocol_metadata(),
        "ci_provenance": {
            "event_name": event,
            "run_id": str(1000 + i),
            "run_attempt": "1",
            "workflow": "v19-forward-microstructure",
            "ref": ref,
            "sha": f"commit-{i}",
            "repository": "parsa314/modular-crypto-trading-bot",
        },
        "symbols": [row("BTC/USDT"), row("ETH/USDT")],
        "provider_failures": [],
        "signal_authorized": False,
        "paper_strategy_replacement_authorized": False,
        "live_execution_authorized": False,
    }
    finalize_payload_hash(payload, "snapshot_sha256")
    return payload


def test_manual_dispatch_and_non_main_never_count():
    good = _snapshot(1, START + pd.Timedelta(minutes=17))
    manual = _snapshot(2, START + pd.Timedelta(minutes=47), event="workflow_dispatch")
    branch = _snapshot(3, START + pd.Timedelta(minutes=77), ref="refs/heads/feature")
    out = evaluate_phase_q([good, manual, branch], as_of=(START + pd.Timedelta(hours=2)).isoformat())
    assert out["countable_snapshot_count"] == 1
    assert out["excluded_snapshot_count"] == 2
    assert out["feature_freeze_authorized"] is False
    assert out["predictive_modeling_authorized"] is False
    assert out["paper_strategy_replacement_authorized"] is False
    assert out["live_execution_authorized"] is False


def test_tampered_snapshot_hash_never_counts():
    snap = _snapshot(1, START + pd.Timedelta(minutes=17))
    tampered = copy.deepcopy(snap)
    tampered["symbols"][0]["feature_authorized"] = False
    assert "SNAPSHOT_HASH_MISMATCH" in validate_phase_q_snapshot(tampered)
    out = evaluate_phase_q([tampered], as_of=(START + pd.Timedelta(hours=1)).isoformat())
    assert out["countable_snapshot_count"] == 0
    assert out["feature_freeze_authorized"] is False


def test_duplicate_scheduled_snapshots_same_slot_cannot_inflate_coverage():
    a = _snapshot(1, START + pd.Timedelta(minutes=17))
    b = _snapshot(2, START + pd.Timedelta(minutes=18))
    out = evaluate_phase_q([a, b], as_of=(START + pd.Timedelta(hours=1)).isoformat())
    assert out["countable_snapshot_count"] == 2
    assert out["core_gate"]["unique_measurement_slots"] == 1
    assert out["diagnostics"]["duplicate_slot_snapshots"] == 1
    assert out["feature_freeze_authorized"] is False


def test_phase_q_passes_after_full_seven_day_quality_pilot_but_modeling_remains_locked():
    snaps = [
        _snapshot(i, START + pd.Timedelta(minutes=17 + 30 * i))
        for i in range(336)
    ]
    out = evaluate_phase_q(snaps, as_of=MATURITY.isoformat())
    assert out["core_gate"]["elapsed_hours"] == 168.0
    assert out["core_gate"]["expected_measurement_opportunities"] == 336
    assert out["core_gate"]["unique_measurement_slots"] == 336
    assert out["core_gate"]["symbols"]["BTC/USDT"]["authorized_coverage_ratio"] == 1.0
    assert out["core_gate"]["symbols"]["ETH/USDT"]["authorized_coverage_ratio"] == 1.0
    assert out["decision"] == "PHASE_Q_PASSED_FEATURE_FREEZE_ALLOWED"
    assert out["data_quality_gate_passed"] is True
    assert out["feature_freeze_authorized"] is True
    assert out["next_allowed_action"] == "FREEZE_4H_FEATURE_SPEC"
    assert out["predictive_modeling_authorized"] is False
    assert out["testnet_promotion_authorized"] is False
    assert out["live_execution_authorized"] is False


def test_80_percent_coverage_boundary_is_enforced_per_symbol():
    snaps = []
    for i in range(336):
        authorized = i < 268  # 268 / 336 < 0.80
        snaps.append(_snapshot(i, START + pd.Timedelta(minutes=17 + 30 * i), authorized=authorized))
    out = evaluate_phase_q(snaps, as_of=MATURITY.isoformat())
    assert out["core_gate"]["symbols"]["BTC/USDT"]["authorized_coverage_ratio"] < 0.80
    assert out["feature_freeze_authorized"] is False
    assert any("AUTHORIZED_COVERAGE_BELOW_TARGET" in reason for reason in out["core_gate"]["reasons"])


def test_phase_q_future_snapshots_cannot_inflate_as_of_coverage():
    # 267/336 valid opportunities is below the frozen 80% threshold.
    historical = [
        _snapshot(i, START + pd.Timedelta(minutes=17 + 30 * i))
        for i in range(267)
    ]
    post_cutoff = [
        _snapshot(i, MATURITY + pd.Timedelta(minutes=17 + 30 * (i - 267)))
        for i in range(267, 336)
    ]
    result = evaluate_phase_q(historical + post_cutoff, as_of=MATURITY.isoformat())
    assert result["countable_snapshot_count"] == 267
    assert result["excluded_snapshot_count"] == 69
    assert all("AFTER_AS_OF" in row["reasons"] for row in result["excluded_snapshots"])
    assert result["core_gate"]["unique_measurement_slots"] == 267
    assert result["core_gate"]["symbols"]["BTC/USDT"]["authorized_coverage_ratio"] < 0.80
    assert result["feature_freeze_authorized"] is False
    assert result["predictive_modeling_authorized"] is False


def test_v19_quality_gate_does_not_count_future_samples_at_as_of():
    from research_bot.microstructure_quality_v19 import evaluate_quality_gate

    future = [
        _snapshot(i, MATURITY + pd.Timedelta(minutes=17 + 30 * i))
        for i in range(336)
    ]
    result = evaluate_quality_gate(
        future,
        pilot_start_at=PHASE_Q_PILOT_START_AT,
        as_of=MATURITY.isoformat(),
    )
    assert result["unique_snapshot_hashes"] == 0
    assert result["unique_measurement_slots"] == 0
    assert result["symbols"]["BTC/USDT"]["authorized_slots"] == 0
    assert result["decision"] == "GATE_Q_NOT_MATURE"


def test_phase_q_download_error_summary_is_safe_and_actionable():
    from scripts.download_v19_forward_artifacts import summarize_download_errors

    failures = [
        {"artifact_id": 1, "reason": "HTTPError:HTTP Error 403: Forbidden"},
        {"artifact_id": 2, "reason": "HTTPError:HTTP Error 410: Gone"},
        {"artifact_id": 3, "reason": "HTTPError:HTTP Error 403: Forbidden"},
        {"artifact_id": 4, "reason": "BadZipFile:invalid archive"},
    ]
    assert summarize_download_errors(failures) == {
        "BadZipFile": 1, "HTTP_403": 2, "HTTP_410": 1,
    }
