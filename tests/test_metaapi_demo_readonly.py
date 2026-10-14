from __future__ import annotations

import pytest

from research_bot.execution_adapters.metaapi_demo_readonly import MetaApiDemoReadOnly
from research_bot.execution_adapters.mt5_transport_gateway import MT5TransportUnavailable


class Connection:
    def __init__(self, mode="ACCOUNT_TRADE_MODE_DEMO"):
        self.mode = mode
        self.closed = False

    async def connect(self):
        pass

    async def wait_synchronized(self):
        pass

    async def get_account_information(self):
        return {"tradeMode": self.mode, "currency": "USD", "server": "Demo"}

    async def get_positions(self):
        return [{"id": "1", "symbol": "EURUSD", "type": "POSITION_TYPE_BUY", "volume": 0.1}]

    async def close(self):
        self.closed = True


class Account:
    platform = "mt5"
    state = "DEPLOYED"

    def __init__(self, connection):
        self.connection = connection

    def get_rpc_connection(self):
        return self.connection


class AccountApi:
    def __init__(self, account):
        self.account = account

    async def get_account(self, account_id):
        assert account_id == "test-account"
        return self.account


class Api:
    def __init__(self, token, account):
        assert token == "test-token"
        self.metatrader_account_api = AccountApi(account)


@pytest.mark.asyncio
async def test_demo_account_read_only():
    conn = Connection()
    adapter = MetaApiDemoReadOnly(token="test-token", account_id="test-account",
                                  api_factory=lambda token: Api(token, Account(conn)))
    await adapter.connect_async()
    assert (await adapter.account_summary_async())["trade_mode"] == 0
    assert len(await adapter.get_positions_async()) == 1
    await adapter.shutdown_async()
    assert conn.closed
    assert not hasattr(adapter, "order_send")


@pytest.mark.asyncio
async def test_real_account_refused_and_connection_closed():
    conn = Connection(mode="ACCOUNT_TRADE_MODE_REAL")
    adapter = MetaApiDemoReadOnly(token="test-token", account_id="test-account",
                                  api_factory=lambda token: Api(token, Account(conn)))
    with pytest.raises(MT5TransportUnavailable, match="NON_DEMO_ACCOUNT_REFUSED"):
        await adapter.connect_async()
    assert conn.closed


@pytest.mark.asyncio
async def test_missing_credentials_refused():
    adapter = MetaApiDemoReadOnly(token="", account_id="", api_factory=lambda token: None)
    adapter._token = ""
    adapter._account_id = ""
    with pytest.raises(MT5TransportUnavailable, match="METAAPI_CREDENTIALS_NOT_CONFIGURED"):
        await adapter.connect_async()
