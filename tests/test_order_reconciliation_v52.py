from __future__ import annotations

from datetime import datetime, timezone

import pytest
from decimal import Decimal

from research_bot.order_reconciliation_v52 import (
    OrderLifecycle,
    ReconciledOrder,
    deterministic_client_order_id,
    mark_submit_ambiguous,
    mark_submit_started,
    realized_cost_from_trades,
    reconcile_nobitex_order,
    retry_submission_allowed,
)


def _local():
    return ReconciledOrder(
        client_order_id="mbot-test",
        symbol="BTC-USDT",
        side="buy",
        requested_quantity=1.0,
    )


def test_client_order_identity_is_deterministic_and_bounded():
    a = deterministic_client_order_id(
        strategy_version="V52_TEST",
        symbol="BTC-USDT",
        decision_epoch=1789300800,
        side="buy",
    )
    b = deterministic_client_order_id(
        strategy_version="V52_TEST",
        symbol="BTC-USDT",
        decision_epoch=1789300800,
        side="buy",
    )
    assert a == b
    assert len(a) <= 32
    assert a.startswith("mbot-")


def test_ambiguous_submit_never_blind_retries():
    order = mark_submit_started(_local())
    assert order.state is OrderLifecycle.SUBMITTING
    order = mark_submit_ambiguous(order)
    assert order.state is OrderLifecycle.UNKNOWN_PENDING_RECONCILIATION
    assert retry_submission_allowed(order) is False
    assert retry_submission_allowed(_local()) is True


def test_nobitex_partial_and_cancelled_with_partial_fill_are_preserved():
    local = _local()
    partial = reconcile_nobitex_order(
        local,
        {
            "id": 123,
            "clientOrderId": "mbot-test",
            "status": "Active",
            "amount": "1.0",
            "matchedAmount": "0.4",
            "averagePrice": "100.5",
            "fee": "0.01",
        },
    )
    assert partial.state is OrderLifecycle.PARTIAL
    assert partial.venue_order_id == "123"
    assert partial.matched_quantity == Decimal("0.4")
    assert partial.average_price == 100.5

    cancelled = reconcile_nobitex_order(
        partial,
        {
            "id": 123,
            "clientOrderId": "mbot-test",
            "status": "Canceled",
            "amount": "1.0",
            "matchedAmount": "0.4",
            "averagePrice": "100.5",
            "fee": "0.01",
        },
    )
    assert cancelled.state is OrderLifecycle.CANCELLED
    assert cancelled.matched_quantity == Decimal("0.4")


def test_done_must_be_fully_matched_and_identity_must_match():
    local = _local()
    with pytest.raises(ValueError, match="fully matched"):
        reconcile_nobitex_order(
            local,
            {
                "id": 1,
                "clientOrderId": "mbot-test",
                "status": "Done",
                "amount": "1.0",
                "matchedAmount": "0.9",
            },
        )
    with pytest.raises(ValueError, match="clientOrderId mismatch"):
        reconcile_nobitex_order(
            local,
            {
                "id": 1,
                "clientOrderId": "other",
                "status": "Active",
                "amount": "1.0",
                "matchedAmount": "0",
            },
        )


def test_realized_cost_uses_actual_trade_vwap_and_does_not_guess_fee_currency():
    trades = [
        {"amount": "0.4", "price": "101", "fee": "0.02"},
        {"amount": "0.6", "price": "102", "fee": "0.03"},
    ]
    out = realized_cost_from_trades(
        trades,
        side="buy",
        decision_reference_price=100.0,
        fee_currency=None,
        fee_to_quote_rate=None,
    )
    assert out.filled_quantity == 1.0
    assert out.vwap == Decimal("101.6")
    assert out.implementation_shortfall_quote == Decimal("1.6")
    assert out.explicit_fee_reported == Decimal("0.05")
    assert out.explicit_fee_quote is None
    assert out.total_cost_quote is None
    assert out.complete_for_quote_cost is False

    converted = realized_cost_from_trades(
        trades,
        side="buy",
        decision_reference_price=100.0,
        fee_currency="USDT",
        fee_to_quote_rate=1.0,
    )
    assert converted.total_cost_quote == Decimal("1.65")
    assert converted.complete_for_quote_cost is True

