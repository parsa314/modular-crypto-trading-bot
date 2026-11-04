"""Synthetic regression: missing cumulative fill evidence cannot be interpreted as zero.

No network/API keys and no live/paper orders are used. Real SDK and PostgreSQL
integrity must be verified by CI before this change can be promoted.
"""
from decimal import Decimal

import pytest

from research_bot.execution import OrderSide
from research_bot.nobitex_testnet_transport import snapshot_from_nobitex_order
from research_bot.okx_demo_transport import snapshot_from_ccxt_order
from research_bot.order_reconciliation_v52 import OrderLifecycle, ReconciledOrder, reconcile_nobitex_order
from research_bot.testnet_execution import AmbiguousTransportOutcome


def _nobitex_local():
    return ReconciledOrder(client_order_id="cid-safe", symbol="BTC/USDT",
                           side="buy", requested_quantity=Decimal("1"))


@pytest.mark.parametrize("status", ["closed", "canceled", "rejected"])
@pytest.mark.parametrize("absent", [None, ""])
def test_okx_missing_fill_cannot_be_treated_as_terminal_zero(status, absent):
    payload = {"id": "venue-123", "clientOrderId": "cid-safe",
               "status": status, "amount": "1"}
    if absent is not None:
        payload["filled"] = absent
    with pytest.raises(AmbiguousTransportOutcome, match="OKX_FILL_QUANTITY_UNAVAILABLE"):
        snapshot_from_ccxt_order(payload, fallback_client_order_id="cid-safe",
                                 fallback_requested_quantity=Decimal("1"))


def test_okx_explicit_zero_closed_is_reconcilable_cancellation():
    snapshot = snapshot_from_ccxt_order(
        {"id": "venue-123", "clientOrderId": "cid-safe", "status": "closed",
         "amount": "1", "filled": "0"},
        fallback_client_order_id="cid-safe", fallback_requested_quantity=Decimal("1"))
    assert snapshot.matched_quantity == Decimal("0")
    assert snapshot.state is OrderLifecycle.CANCELLED


@pytest.mark.parametrize("status", ["Done", "Canceled", "Rejected"])
@pytest.mark.parametrize("absent", [None, ""])
def test_nobitex_missing_terminal_fill_is_rejected(status, absent):
    payload = {"id": 123, "clientOrderId": "cid-safe", "status": status,
               "amount": "1"}
    if absent is not None:
        payload["matchedAmount"] = absent
    with pytest.raises(ValueError, match="NOBITEX_MATCHED_AMOUNT_UNAVAILABLE"):
        reconcile_nobitex_order(_nobitex_local(), payload)


def test_nobitex_explicit_zero_terminal_is_accepted():
    order = reconcile_nobitex_order(_nobitex_local(), {
        "id": 123, "clientOrderId": "cid-safe", "status": "Canceled",
        "amount": "1", "matchedAmount": "0", "fee": "0",
    })
    assert order.matched_quantity == Decimal("0")
    assert order.state is OrderLifecycle.CANCELLED


def test_nobitex_transport_parser_rejects_ambiguous_ack():
    with pytest.raises(ValueError, match="NOBITEX_MATCHED_AMOUNT_UNAVAILABLE"):
        snapshot_from_nobitex_order(
            {"id": 123, "clientOrderId": "cid-safe", "status": "Canceled",
             "amount": "1"},
            fallback_client_order_id="cid-safe",
            requested_quantity=Decimal("1"), symbol="BTC/USDT", side=OrderSide.BUY)
