from __future__ import annotations

import pytest

from research_bot.execution_adapters.mt5_transport_gateway import (
    MT5TransportMode,
    MT5TransportSettings,
    MT5TransportUnavailable,
    create_transport,
    verify_demo_read_only,
)


class FakeTransport:
    def __init__(self, trade_mode=0):
        self.trade_mode = trade_mode
        self.closed = False

    def connect(self):
        pass

    def account_summary(self):
        return {"connected": True, "trade_mode": self.trade_mode, "currency": "USD", "server": "Demo"}

    def shutdown(self):
        self.closed = True


def test_cloud_requires_explicit_provider_and_adapter():
    with pytest.raises(MT5TransportUnavailable, match="CLOUD_PROVIDER_NOT_SELECTED"):
        create_transport(MT5TransportSettings(mode=MT5TransportMode.CLOUD))
    with pytest.raises(MT5TransportUnavailable, match="CLOUD_ADAPTER_NOT_CONFIGURED"):
        create_transport(MT5TransportSettings(mode=MT5TransportMode.CLOUD, cloud_provider="example"))


def test_cloud_order_submission_is_never_enabled_by_gateway():
    with pytest.raises(MT5TransportUnavailable, match="CLOUD_ORDER_SUBMISSION_NOT_VALIDATED"):
        create_transport(MT5TransportSettings(mode=MT5TransportMode.CLOUD, cloud_provider="example", allow_demo_orders=True), cloud_transport=FakeTransport())


def test_demo_read_only_sanitizes_and_disconnects():
    transport = FakeTransport()
    result = verify_demo_read_only(transport)
    assert result == {"connected": True, "trade_mode": 0, "currency": "USD", "server": "Demo"}
    assert transport.closed


@pytest.mark.parametrize("trade_mode", [1, 2, None, "0", True])
def test_non_demo_or_ambiguous_account_refused(trade_mode):
    transport = FakeTransport(trade_mode)
    with pytest.raises(MT5TransportUnavailable, match="NON_DEMO_ACCOUNT_REFUSED"):
        verify_demo_read_only(transport)
    assert transport.closed
