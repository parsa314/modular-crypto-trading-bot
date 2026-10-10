"""MetaApi read-only MT5 DEMO RPC adapter.

Requires optional dependency: pip install metaapi-cloud-sdk
Requires METAAPI_TOKEN and METAAPI_ACCOUNT_ID in the *runtime environment*.
Account must already be provisioned in MetaApi. No broker credentials are
accepted, transmitted, or stored by this module. No order methods exposed.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from .mt5_transport_gateway import MT5TransportUnavailable


class MetaApiDemoReadOnly:
    def __init__(self, *, token: str | None = None, account_id: str | None = None, api_factory: Any = None):
        self._token = token or os.environ.get("METAAPI_TOKEN", "")
        self._account_id = account_id or os.environ.get("METAAPI_ACCOUNT_ID", "")
        self._api_factory = api_factory
        self._connection: Any = None
        self._account: Any = None

    async def connect_async(self) -> None:
        if not self._token or not self._account_id:
            raise MT5TransportUnavailable("METAAPI_CREDENTIALS_NOT_CONFIGURED")
        if self._connection is not None:
            raise MT5TransportUnavailable("ALREADY_CONNECTED")
        factory = self._api_factory
        if factory is None:
            try:
                from metaapi_cloud_sdk import MetaApi
            except ImportError as exc:
                raise MT5TransportUnavailable("METAAPI_SDK_NOT_INSTALLED") from exc
            factory = MetaApi
        api = factory(self._token)
        account = await api.metatrader_account_api.get_account(self._account_id)
        if str(getattr(account, "platform", "")).lower() != "mt5":
            raise MT5TransportUnavailable("NOT_MT5_ACCOUNT")
        # Do not deploy or provision accounts automatically.
        if str(getattr(account, "state", "")).upper() != "DEPLOYED":
            raise MT5TransportUnavailable("METAAPI_ACCOUNT_NOT_DEPLOYED")
        connection = account.get_rpc_connection()
        try:
            await connection.connect()
            await connection.wait_synchronized()
            info = await connection.get_account_information()
            if not self._is_demo(info):
                raise MT5TransportUnavailable("NON_DEMO_ACCOUNT_REFUSED")
        except BaseException:
            await connection.close()
            raise
        self._account = account
        self._connection = connection

    @staticmethod
    def _is_demo(info: dict[str, Any]) -> bool:
        # MetaApi uses account type strings such as ACCOUNT_TRADE_MODE_DEMO.
        # No guessing from broker server name or login number.
        mode = info.get("tradeMode")
        return mode in ("ACCOUNT_TRADE_MODE_DEMO", "DEMO", 0) and not isinstance(mode, bool)

    async def account_summary_async(self) -> dict[str, Any]:
        if self._connection is None:
            raise MT5TransportUnavailable("ACCOUNT_NOT_CONNECTED")
        info = await self._connection.get_account_information()
        if not self._is_demo(info):
            raise MT5TransportUnavailable("NON_DEMO_ACCOUNT_REFUSED")
        return {
            "connected": True,
            "trade_mode": 0,
            "currency": str(info.get("currency", "")),
            "server": str(info.get("server", "")),
        }

    async def get_positions_async(self) -> list[dict[str, Any]]:
        if self._connection is None:
            raise MT5TransportUnavailable("ACCOUNT_NOT_CONNECTED")
        await self.account_summary_async()
        positions = await self._connection.get_positions()
        return [{"id": str(p.get("id", "")), "symbol": str(p.get("symbol", "")),
                 "type": str(p.get("type", "")), "volume": p.get("volume")} for p in positions]

    async def shutdown_async(self) -> None:
        connection, self._connection = self._connection, None
        self._account = None
        if connection is not None:
            await connection.close()

    def connect(self) -> None:
        asyncio.run(self.connect_async())

    def account_summary(self) -> dict[str, Any]:
        return asyncio.run(self.account_summary_async())

    def shutdown(self) -> None:
        asyncio.run(self.shutdown_async())
