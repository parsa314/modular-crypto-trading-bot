from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from research_bot.execution import OrderSide
from research_bot.order_reconciliation_v52 import OrderLifecycle
from research_bot.nobitex_testnet_transport import (
    NobitexTestnetCredentials,
    NobitexTestnetTransport,
    TESTNET_API_BASE,
    credentials_from_env,
)
from research_bot.testnet_execution import AmbiguousTransportOutcome, TestnetOrderIntent, TestnetSafetyError


class FakeHTTP:
    def __init__(self):
        self.calls = []
        self.raise_on_add = None
        self.status_order = {
            "id": 25,
            "type": "buy",
            "amount": "0.010000",
            "matchedAmount": "0.004000",
            "status": "Active",
            "averagePrice": "100010",
            "fee": "0.1",
            "clientOrderId": "cid-1",
        }

    def request(self, method, url, *, headers=None, body=None, timeout_seconds=10.0):
        self.calls.append((method, url, dict(headers or {}), body, timeout_seconds))
        if url.endswith("/v3/orderbook/BTCUSDT"):
            return {
                "status": "ok",
                "lastTradePrice": "100000",
                "asks": [["100010", "1"]],
                "bids": [["99990", "1"]],
            }
        if url.endswith("/v2/options"):
            return {
                "status": "ok",
                "nobitex": {
                    "minOrders": {"usdt": "11", "rls": "3000000"},
                    "amountPrecisions": {"BTCUSDT": "0.000001"},
                },
            }
        if url.endswith("/users/wallets/list"):
            return {
                "status": "ok",
                "wallets": [
                    {"currency": "usdt", "balance": "1000"},
                    {"currency": "btc", "balance": "1"},
                ],
            }
        if url.endswith("/market/orders/add"):
            if self.raise_on_add is not None:
                raise self.raise_on_add
            return {
                "status": "ok",
                "order": {
                    "id": 25,
                    "type": body["type"],
                    "amount": body["amount"],
                    "matchedAmount": "0",
                    "status": "Active",
                    "averagePrice": "0",
                    "fee": "0",
                    "clientOrderId": body["clientOrderId"],
                },
            }
        if url.endswith("/market/orders/status"):
            return {"status": "ok", "order": dict(self.status_order)}
        if url.endswith("/market/orders/update-status"):
            self.status_order = {**self.status_order, "status": "Canceled"}
            return {"status": "ok", "updatedStatus": "Canceled"}
        raise AssertionError(url)


def _transport(http=None):
    return NobitexTestnetTransport(
        NobitexTestnetCredentials("testnet-token"),
        http_client=http or FakeHTTP(),
    )


def _intent():
    return TestnetOrderIntent(
        client_order_id="cid-1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        quantity=Decimal("0.010000"),
        reference_price=Decimal("100000"),
        created_at=datetime(2026, 10, 5, tzinfo=timezone.utc),
    )


def test_env_requires_explicit_testnet_enable_and_testnet_token():
    with pytest.raises(TestnetSafetyError, match="EXPLICIT_ENABLE"):
        credentials_from_env({})
    creds = credentials_from_env(
        {"NOBITEX_TESTNET_ENABLED": "true", "NOBITEX_TESTNET_TOKEN": "abc"}
    )
    assert creds == NobitexTestnetCredentials("abc")


def test_transport_is_hard_locked_to_nobitex_testnet_host():
    t = _transport()
    assert t.sandbox is True
    assert t.venue == "NOBITEX_TESTNET"
    assert t.base_url == "https://testnetapi.nobitex.ir"


def test_public_reference_and_precision_use_official_testnet_endpoints():
    http = FakeHTTP()
    t = _transport(http)
    px = t.public_reference_price("BTC/USDT")
    precision = t.market_precision("BTC/USDT", reference_price=px)
    assert px == Decimal("100000")
    assert precision.quantity_step == Decimal("0.000001")
    assert precision.min_notional == Decimal("11")
    assert all(call[1].startswith(TESTNET_API_BASE) for call in http.calls)


def test_private_preflight_is_read_only_and_does_not_log_balances():
    http = FakeHTTP()
    result = _transport(http).private_preflight("BTC/USDT")
    assert result["authenticated"] is True
    assert result["wallet_count"] == 2
    assert "wallets" not in result
    assert "balance" not in result
    assert all(not call[1].endswith("/market/orders/add") for call in http.calls)
    auth_calls = [call for call in http.calls if call[1].endswith("/users/wallets/list")]
    assert auth_calls[0][2]["Authorization"] == "Token testnet-token"


def test_submit_market_order_uses_expected_price_and_client_order_id():
    http = FakeHTTP()
    snapshot = _transport(http).submit_market_order(_intent())
    assert snapshot.state is OrderLifecycle.ACKNOWLEDGED
    call = [c for c in http.calls if c[1].endswith("/market/orders/add")][0]
    assert call[3] == {
        "type": "buy",
        "execution": "market",
        "srcCurrency": "btc",
        "dstCurrency": "usdt",
        "amount": "0.010000",
        "price": "100000",
        "clientOrderId": "cid-1",
    }


def test_network_error_after_submit_is_ambiguous():
    http = FakeHTTP()
    http.raise_on_add = TimeoutError("timeout")
    with pytest.raises(AmbiguousTransportOutcome, match="OUTCOME_UNKNOWN"):
        _transport(http).submit_market_order(_intent())


def test_fetch_reconciles_partial_fill_by_numeric_order_id():
    http = FakeHTTP()
    snapshot = _transport(http).fetch_order(
        client_order_id="cid-1",
        venue_order_id="25",
        symbol="BTC/USDT",
    )
    assert snapshot is not None
    assert snapshot.state is OrderLifecycle.PARTIAL
    assert snapshot.matched_quantity == Decimal("0.004")
    call = http.calls[-1]
    assert call[3] == {"id": "25"}


def test_cancel_then_fetch_returns_cancelled_with_partial_fill_preserved():
    http = FakeHTTP()
    snapshot = _transport(http).cancel_order(
        client_order_id="cid-1",
        venue_order_id="25",
        symbol="BTC/USDT",
    )
    assert snapshot.state is OrderLifecycle.CANCELLED
    assert snapshot.matched_quantity == Decimal("0.004")
    assert [c[1].rsplit("/", 1)[-1] for c in http.calls[-2:]] == [
        "update-status",
        "status",
    ]


def test_submit_ack_without_order_object_is_ambiguous():
    class MissingOrderHTTP(FakeHTTP):
        def request(self, method, url, **kwargs):
            if url.endswith("/market/orders/add"):
                self.calls.append((method, url, kwargs.get("headers", {}), kwargs.get("body"), kwargs.get("timeout_seconds", 10.0)))
                return {"status": "ok"}
            return super().request(method, url, **kwargs)

    with pytest.raises(AmbiguousTransportOutcome, match="WITHOUT_ORDER_OBJECT"):
        _transport(MissingOrderHTTP()).submit_market_order(_intent())


def test_production_environment_names_are_not_used_as_fallback():
    with pytest.raises(ValueError, match="token is required"):
        credentials_from_env(
            {
                "NOBITEX_TESTNET_ENABLED": "true",
                "NOBITEX_API_TOKEN": "production-token-must-be-ignored",
            }
        )
