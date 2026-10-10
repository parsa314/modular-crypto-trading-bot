from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from research_bot.demo_experience import (
    AppendOnlyDemoExperienceLedger,
    DemoDecisionExperience,
    DemoExperienceError,
    DemoOutcomeExperience,
)


T0 = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def decision(event_id="evt-1"):
    return DemoDecisionExperience(
        event_id=event_id,
        decision_at=T0,
        canonical_symbol="BTC/USDT",
        venue="MT5_DEMO",
        venue_symbol="BTCUSD",
        execution_domain="mt5:UnitTest-Demo:BTCUSD",
        strategy_version="v59",
        model_version="champion-001",
        feature_snapshot_id="feat-001",
        side="BUY",
        expected_edge=0.42,
        model_confidence=0.81,
        regime="TREND_UP",
    )


def outcome(event_id="evt-1", when=None):
    return DemoOutcomeExperience(
        event_id=event_id,
        outcome_at=when or (T0 + timedelta(hours=4)),
        outcome="TP",
        realized_r=1.5,
        realized_pnl=150.0,
        realized_cost=4.0,
        exit_price=102.0,
    )


def test_training_rows_are_unavailable_until_outcome_is_observed(tmp_path):
    ledger = AppendOnlyDemoExperienceLedger(tmp_path / "experience.jsonl")
    ledger.record_decision(decision())

    assert ledger.training_rows(as_of=T0 + timedelta(hours=1)) == []

    ledger.record_outcome(outcome())
    assert ledger.training_rows(as_of=T0 + timedelta(hours=3)) == []

    rows = ledger.training_rows(as_of=T0 + timedelta(hours=5))
    assert len(rows) == 1
    assert rows[0]["event_id"] == "evt-1"
    assert rows[0]["decision"]["model_version"] == "champion-001"
    assert rows[0]["outcome"]["outcome"] == "TP"


def test_outcome_cannot_be_recorded_before_decision(tmp_path):
    ledger = AppendOnlyDemoExperienceLedger(tmp_path / "experience.jsonl")

    with pytest.raises(DemoExperienceError, match="before its decision"):
        ledger.record_outcome(outcome())


def test_outcome_must_be_strictly_after_decision(tmp_path):
    ledger = AppendOnlyDemoExperienceLedger(tmp_path / "experience.jsonl")
    ledger.record_decision(decision())

    with pytest.raises(DemoExperienceError, match="strictly after"):
        ledger.record_outcome(outcome(when=T0))


def test_duplicate_decision_and_duplicate_outcome_are_rejected(tmp_path):
    ledger = AppendOnlyDemoExperienceLedger(tmp_path / "experience.jsonl")
    ledger.record_decision(decision())

    with pytest.raises(DemoExperienceError, match="duplicate experience"):
        ledger.record_decision(decision())

    ledger.record_outcome(outcome())
    with pytest.raises(DemoExperienceError, match="duplicate experience"):
        ledger.record_outcome(outcome())


def test_execution_domain_is_preserved_for_cross_venue_separation(tmp_path):
    ledger = AppendOnlyDemoExperienceLedger(tmp_path / "experience.jsonl")
    ledger.record_decision(decision())
    ledger.record_outcome(outcome())

    row = ledger.training_rows(as_of=T0 + timedelta(hours=5))[0]
    assert row["decision"]["venue"] == "MT5_DEMO"
    assert row["decision"]["execution_domain"] == "mt5:UnitTest-Demo:BTCUSD"
