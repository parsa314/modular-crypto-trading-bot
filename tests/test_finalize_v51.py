from __future__ import annotations

import pandas as pd
import pytest

from research_bot.finalize_v51 import audit_prospective_evidence_v51, terminal_route_v51


def _row(*, venue="coinex", symbol="BTCUSDT", close="2026-09-13T16:00:00Z", seen="2026-09-13T16:05:00Z", eligible=True):
    close_ts = pd.Timestamp(close)
    return {
        "venue": venue,
        "symbol": symbol,
        "timeframe": "4h",
        "bar_open_at": close_ts - pd.Timedelta(hours=4),
        "bar_close_at": close_ts,
        "first_seen_at": pd.Timestamp(seen),
        "open": 100.0,
        "high": 110.0,
        "low": 90.0,
        "close": 105.0,
        "volume": 10.0,
        "payload_sha256": "a" * 64,
        "prospective_eligible": eligible,
        "source_commit": "b" * 40,
        "collector_version": "v51-ops-1",
    }


def test_timely_row_is_admissible():
    audit = audit_prospective_evidence_v51(pd.DataFrame([_row()]))
    assert audit.eligible_rows == 1
    assert audit.ineligible_rows == 0


def test_late_row_cannot_be_marked_eligible():
    frame = pd.DataFrame([_row(seen="2026-09-13T17:00:01Z", eligible=True)])
    with pytest.raises(ValueError, match="eligibility contradicts"):
        audit_prospective_evidence_v51(frame)


def test_late_row_is_retained_when_ineligible():
    frame = pd.DataFrame([_row(seen="2026-09-13T17:00:01Z", eligible=False)])
    audit = audit_prospective_evidence_v51(frame)
    assert audit.eligible_rows == 0
    assert audit.ineligible_rows == 1


def test_placeholder_source_commit_fails_closed():
    frame = pd.DataFrame([_row()])
    frame.loc[0, "source_commit"] = "UNKNOWN"
    with pytest.raises(ValueError, match="source commit"):
        audit_prospective_evidence_v51(frame)


def test_forbidden_venue_fails_closed():
    with pytest.raises(ValueError, match="forbidden venue"):
        audit_prospective_evidence_v51(pd.DataFrame([_row(venue="kraken")]))


def test_bad_hash_fails_closed():
    frame = pd.DataFrame([_row()])
    frame.loc[0, "payload_sha256"] = "bad"
    with pytest.raises(ValueError, match="SHA-256"):
        audit_prospective_evidence_v51(frame)


def test_incomplete_series_routes_without_economic_decision():
    evidence = pd.DataFrame([_row()])
    support = pd.DataFrame(columns=["venue", "prospective_block_v51", "evidence_present", "conflict_cohorts"])
    decision, audit = terminal_route_v51(
        evidence=evidence,
        venue_block_support=support,
        conflict_counts=[100, 100, 100, 100, 100],
        expectancy_deltas=[1, 1, 1, 1, 1],
        aggregate_expectancy_a0=0.0,
        aggregate_expectancy_a1=1.0,
        profit_factor_a0=1.0,
        profit_factor_a1=2.0,
        stress_profit_factor_a0=1.0,
        stress_profit_factor_a1=2.0,
        worst_drawdown_a1=-0.01,
        causal_checks_passed=True,
    )
    assert audit.eligible_series == 1
    assert decision == "V51_INCOMPLETE_EVIDENCE"



def test_missing_raw_venue_block_cell_cannot_reach_economic_gate():
    from research_bot.overlap_arbitration_v51 import (
        ALLOWED_ASSETS_V51, ALLOWED_VENUES_V51, BLOCK_DAYS_V51,
        N_BLOCKS_V51, PROSPECTIVE_START_V51,
    )

    rows = []
    # All 15 venue/asset series and all five blocks appear, but the raw ledger
    # intentionally omits OKX and KuCoin in block 5.
    for venue in ALLOWED_VENUES_V51:
        for asset in ALLOWED_ASSETS_V51:
            for block in range(1, N_BLOCKS_V51 + 1):
                if block == N_BLOCKS_V51 and venue != "coinex":
                    continue
                close_ts = PROSPECTIVE_START_V51 + pd.Timedelta(
                    days=BLOCK_DAYS_V51 * (block - 1), hours=4
                )
                rows.append(_row(
                    venue=venue,
                    symbol=f"{asset}/USDT",
                    close=close_ts.isoformat(),
                    seen=(close_ts + pd.Timedelta(minutes=5)).isoformat(),
                ))

    evidence = pd.DataFrame(rows)
    support = pd.DataFrame([
        {"venue": venue, "prospective_block_v51": block,
         "evidence_present": True, "conflict_cohorts": 20}
        for venue in ALLOWED_VENUES_V51
        for block in range(1, N_BLOCKS_V51 + 1)
    ])
    decision, audit = terminal_route_v51(
        evidence=evidence,
        venue_block_support=support,
        conflict_counts=[20, 20, 20, 20, 20],
        expectancy_deltas=[1, 1, 1, 1, 1],
        aggregate_expectancy_a0=0.0,
        aggregate_expectancy_a1=1.0,
        profit_factor_a0=1.0,
        profit_factor_a1=2.0,
        stress_profit_factor_a0=1.0,
        stress_profit_factor_a1=2.0,
        worst_drawdown_a1=-0.01,
        causal_checks_passed=True,
    )
    assert audit.eligible_series == 15
    assert audit.eligible_blocks == N_BLOCKS_V51
    assert decision == "V51_INCOMPLETE_EVIDENCE"
