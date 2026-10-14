"""Broker reconciliation never submits orders and never guesses fills."""
from datetime import datetime, timedelta, timezone

from research_bot.mt5_demo_reconciliation import (
    BrokerDemoSnapshot, reconcile_demo_before_new_order,
)

NOW = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)


def state(**override):
    x = dict(account_key="Demo:314", captured_at=NOW, positions=(),
             open_orders=(), observation_complete=True, account_is_demo=True)
    x.update(override)
    return BrokerDemoSnapshot(**x)


def inspect(events=(), snap=None, ignored=None):
    return reconcile_demo_before_new_order(
        broker=snap or state(), journal_rows=events, now=NOW,
        current_unsubmitted_intent=ignored,
    )


def test_empty_observed_demo_account_with_no_intents_is_ready():
    x = inspect()
    assert x.approved and not x.reasons and len(x.snapshot_sha256) == 64


def test_disconnect_or_stale_snapshot_vetoes():
    assert "STALE_OR_FUTURE_BROKER_SNAPSHOT" in inspect(
        snap=state(captured_at=NOW - timedelta(minutes=5))).reasons
    assert "INCOMPLETE_BROKER_ORDER_POSITION_SNAPSHOT" in inspect(
        snap=state(observation_complete=False)).reasons


def test_real_account_cannot_be_accepted():
    assert "REAL_OR_UNVERIFIED_ACCOUNT_REFUSED" in inspect(
        snap=state(account_is_demo=False)).reasons


def test_live_broker_order_prevents_new_entry():
    x=inspect(snap=state(open_orders=({"ticket": 9, "symbol":"BTCUSD"},)))
    assert not x.approved
    assert "PENDING_BOT_ORDER_REQUIRES_RECONCILIATION" in x.reasons


def test_broker_position_without_local_entry_prevents_new_entry():
    x=inspect(snap=state(positions=({"ticket": 77, "symbol":"BTCUSD"},)))
    assert not x.approved
    assert "OPEN_BOT_POSITION_REQUIRES_MANAGEMENT_NOT_NEW_ENTRY" in x.reasons


def test_orphan_order_intent_must_not_retry_after_restart():
    x=inspect([{"record_type":"INTENT","signal_id":"order123"}])
    assert not x.approved
    assert x.unmatched_intents==("order123",)


def test_broker_error_does_not_prove_rejected_fill():
    events=[
        {"record_type":"INTENT","signal_id":"unknown"},
        {"record_type":"EXECUTION_ERROR","signal_id":"unknown"},
    ]
    assert "UNRESOLVED_LOCAL_ORDER_OR_CLOSE_INTENTS" in inspect(events).reasons


def test_unverified_local_fill_requires_broker_trade_history():
    events=[
        {"record_type":"INTENT","signal_id":"filled"},
        {"record_type":"EXECUTION_RESULT","signal_id":"filled"},
    ]
    assert "BROKER_DEAL_HISTORY_NOT_RECONCILED" in inspect(events).reasons


def test_current_intent_does_not_self_veto_before_network_submission():
    x=inspect([{"record_type":"INTENT","signal_id":"new"}],ignored="new")
    assert x.approved


def test_ticket_uniqueness_and_account_identity():
    x=inspect(snap=state(positions=({"ticket": 3},),open_orders=({"ticket":3},)))
    assert "INVALID_OR_DUPLICATE_BROKER_TICKET" in x.reasons
    assert "INVALID_ACCOUNT_IDENTITY" in inspect(snap=state(account_key="")).reasons
