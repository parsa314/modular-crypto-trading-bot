from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import ccxt
import pytest

from research_bot.execution import OrderSide
from research_bot.order_reconciliation_v52 import OrderLifecycle
from research_bot.okx_demo_transport import (
    OKXDemoCredentials,
    OKXDemoTransport,
    credentials_from_env,
    snapshot_from_ccxt_order,
)
from research_bot.testnet_execution import (
    AmbiguousTransportOutcome,
    TestnetOrderIntent,
    TestnetSafetyError,
)


class FakeOKX:
    id = "okx"
    precisionMode = ccxt.TICK_SIZE

    def __init__(self, config=None):
        self.config = config or {}
        self.calls = []
        self.created = None
        self.fetch_result = {
            "id": "venue-1",
            "clientOrderId": "cid-1",
            "status": "open",
            "amount": 0.01,
            "filled": 0.0,
            "average": None,
            "fee": None,
        }
        self.markets = {
            "BTC/USDT": {
                "precision": {"amount": 0.001},
                "limits": {
                    "amount": {"min": 0.001},
                    "cost": {"min": 5.0},
                },
            }
        }

    def set_sandbox_mode(self, enabled):
        self.calls.append(("set_sandbox_mode", enabled))

    def load_markets(self):
        self.calls.append(("load_markets",))
        return self.markets

    def fetch_balance(self):
        self.calls.append(("fetch_balance",))
        return {"total": {"USDT": 10000.0}}

    def create_order(self, symbol, order_type, side, amount, price, params):
        self.calls.append(("create_order", symbol, order_type, side, amount, price, params))
        if isinstance(self.created, Exception):
            raise self.created
        return self.created or {
            "id": "venue-1",
            "clientOrderId": params["clientOrderId"],
            "status": None,
            "amount": amount,
            "filled": 0.0,
            "average": None,
            "fee": None,
        }

    def fetch_order(self, order_id, symbol, params=None):
        self.calls.append(("fetch_order", order_id, symbol, params))
        return self.fetch_result

    def cancel_order(self, order_id, symbol, params=None):
        self.calls.append(("cancel_order", order_id, symbol, params))
        return {"id": "venue-1", "clientOrderId": "cid-1"}


def _creds():
    return OKXDemoCredentials("demo-key", "demo-secret", "demo-passphrase")


def _intent(cid="cid-1"):
    return TestnetOrderIntent(
        client_order_id=cid,
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        quantity=Decimal("0.010"),
        reference_price=Decimal("100000"),
        created_at=datetime(2026, 10, 5, tzinfo=timezone.utc),
    )


def test_env_requires_explicit_demo_enable_and_demo_specific_credentials():
    with pytest.raises(TestnetSafetyError, match="EXPLICIT_ENABLE"):
        credentials_from_env({})

    creds = credentials_from_env(
        {
            "OKX_DEMO_ENABLED": "true",
            "OKX_DEMO_API_KEY": "k",
            "OKX_DEMO_API_SECRET": "s",
            "OKX_DEMO_API_PASSPHRASE": "p",
        }
    )
    assert creds == OKXDemoCredentials("k", "s", "p")


def test_factory_enables_sandbox_before_any_exchange_call():
    instances = []

    def factory(config):
        ex = FakeOKX(config)
        instances.append(ex)
        return ex

    transport = OKXDemoTransport.from_credentials(_creds(), exchange_factory=factory)
    assert transport.sandbox is True
    assert transport.venue == "OKX_DEMO"
    assert instances[0].calls == [("set_sandbox_mode", True)]
    assert instances[0].config["options"]["defaultType"] == "spot"


def test_private_preflight_is_read_only_and_authenticated():
    ex = FakeOKX()
    transport = OKXDemoTransport(ex)
    result = transport.private_preflight("BTC/USDT")
    assert result["sandbox"] is True
    assert result["authenticated_balance_response"] is True
    assert all(call[0] not in {"create_order", "cancel_order"} for call in ex.calls)


def test_market_precision_uses_okx_market_metadata():
    ex = FakeOKX()
    transport = OKXDemoTransport(ex)
    p = transport.market_precision("BTC/USDT", reference_price=Decimal("100000"))
    assert p.quantity_step == Decimal("0.001")
    assert p.min_quantity == Decimal("0.001")
    assert p.min_notional == Decimal("5.0")


def test_submit_uses_market_order_and_client_order_id():
    ex = FakeOKX()
    transport = OKXDemoTransport(ex)
    snapshot = transport.submit_market_order(_intent())
    assert snapshot.state is OrderLifecycle.ACKNOWLEDGED
    call = ex.calls[-1]
    assert call[:5] == ("create_order", "BTC/USDT", "market", "buy", 0.01)
    assert call[-1] == {"clientOrderId": "cid-1"}


def test_submit_network_error_is_ambiguous_not_retryable_success_assumption():
    ex = FakeOKX()
    ex.created = ccxt.RequestTimeout("timeout after send")
    transport = OKXDemoTransport(ex)
    with pytest.raises(AmbiguousTransportOutcome, match="OUTCOME_UNKNOWN"):
        transport.submit_market_order(_intent())


def test_missing_venue_id_after_submit_is_ambiguous():
    ex = FakeOKX()
    ex.created = {
        "id": None,
        "clientOrderId": "cid-1",
        "status": None,
        "amount": 0.01,
        "filled": 0.0,
    }
    transport = OKXDemoTransport(ex)
    with pytest.raises(AmbiguousTransportOutcome, match="WITHOUT_VENUE_ORDER_ID"):
        transport.submit_market_order(_intent())


def test_snapshot_maps_partial_fill_fee_and_terminal_states():
    partial = snapshot_from_ccxt_order(
        {
            "id": "venue-1",
            "clientOrderId": "cid-1",
            "status": "open",
            "amount": "0.010",
            "filled": "0.004",
            "average": "100010",
            "fee": {"cost": "0.15", "currency": "USDT"},
        },
        fallback_client_order_id="cid-1",
    )
    assert partial.state is OrderLifecycle.PARTIAL
    assert partial.matched_quantity == Decimal("0.004")
    assert partial.average_price == Decimal("100010")
    assert partial.fee_reported == Decimal("0.15")
    assert partial.fee_currency == "USDT"

    filled = snapshot_from_ccxt_order(
        {
            "id": "venue-1",
            "clientOrderId": "cid-1",
            "status": "closed",
            "amount": "0.010",
            "filled": "0.010",
            "average": "100012",
        },
        fallback_client_order_id="cid-1",
    )
    assert filled.state is OrderLifecycle.FILLED


def test_fetch_without_venue_id_reconciles_by_client_order_id():
    ex = FakeOKX()
    transport = OKXDemoTransport(ex)
    snapshot = transport.fetch_order(
        client_order_id="cid-1",
        venue_order_id=None,
        symbol="BTC/USDT",
    )
    assert snapshot is not None
    assert ex.calls[-1] == (
        "fetch_order",
        "cid-1",
        "BTC/USDT",
        {"clientOrderId": "cid-1"},
    )


def test_fetch_order_not_found_returns_none():
    class MissingOKX(FakeOKX):
        def fetch_order(self, order_id, symbol, params=None):
            raise ccxt.OrderNotFound("missing")

    transport = OKXDemoTransport(MissingOKX())
    assert transport.fetch_order(
        client_order_id="cid-1",
        venue_order_id="venue-1",
        symbol="BTC/USDT",
    ) is None


def test_cancel_reconciles_authoritative_final_state():
    ex = FakeOKX()
    ex.fetch_result = {
        "id": "venue-1",
        "clientOrderId": "cid-1",
        "status": "canceled",
        "amount": 0.01,
        "filled": 0.002,
        "average": 100010,
        "fee": {"cost": 0.05, "currency": "USDT"},
    }
    transport = OKXDemoTransport(ex)
    snapshot = transport.cancel_order(
        client_order_id="cid-1",
        venue_order_id="venue-1",
        symbol="BTC/USDT",
    )
    assert snapshot.state is OrderLifecycle.CANCELLED
    assert snapshot.matched_quantity == Decimal("0.002")
    assert [call[0] for call in ex.calls][-2:] == ["cancel_order", "fetch_order"]


def test_non_okx_exchange_is_rejected():
    ex = FakeOKX()
    ex.id = "binance"
    with pytest.raises(TestnetSafetyError, match="OKX_EXCHANGE_REQUIRED"):
        OKXDemoTransport(ex)
