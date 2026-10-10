"""Venue-neutral MT5 connection selection with fail-closed cloud support.

Cloud providers must supply an independently reviewed adapter. Merely selecting
cloud mode never sends orders, stores credentials, or bypasses DEMO checks.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class MT5TransportMode(str, Enum):
    DESKTOP = "desktop"
    CLOUD = "cloud"


class MT5TransportUnavailable(RuntimeError):
    pass


@runtime_checkable
class MT5ReadOnlyTransport(Protocol):
    def connect(self) -> None: ...
    def account_summary(self) -> dict[str, Any]: ...
    def shutdown(self) -> None: ...


@dataclass(frozen=True)
class MT5TransportSettings:
    mode: MT5TransportMode = MT5TransportMode.DESKTOP
    cloud_provider: str | None = None
    allow_demo_orders: bool = False


def create_transport(
    settings: MT5TransportSettings,
    *,
    desktop_transport: MT5ReadOnlyTransport | None = None,
    cloud_transport: MT5ReadOnlyTransport | None = None,
) -> MT5ReadOnlyTransport:
    """Return only an explicitly supplied, independently configured adapter.

    No cloud credentials or provider endpoints are inferred from account
    credentials. Cloud order submission is intentionally unsupported here.
    """
    if settings.mode == MT5TransportMode.DESKTOP:
        if desktop_transport is None:
            raise MT5TransportUnavailable("DESKTOP_ADAPTER_NOT_CONFIGURED")
        return desktop_transport
    if settings.mode == MT5TransportMode.CLOUD:
        if settings.allow_demo_orders:
            raise MT5TransportUnavailable("CLOUD_ORDER_SUBMISSION_NOT_VALIDATED")
        if not settings.cloud_provider:
            raise MT5TransportUnavailable("CLOUD_PROVIDER_NOT_SELECTED")
        if cloud_transport is None:
            raise MT5TransportUnavailable("CLOUD_ADAPTER_NOT_CONFIGURED")
        return cloud_transport
    raise MT5TransportUnavailable("UNKNOWN_MT5_TRANSPORT")


def verify_demo_read_only(transport: MT5ReadOnlyTransport) -> dict[str, Any]:
    """Connect, verify account mode, and return a sanitized read-only summary."""
    transport.connect()
    try:
        summary = transport.account_summary()
        if not summary.get("connected", False):
            raise MT5TransportUnavailable("ACCOUNT_NOT_CONNECTED")
        # MetaTrader ACCOUNT_TRADE_MODE_DEMO = 0; do not infer from server name.
        if type(summary.get("trade_mode")) is not int or summary["trade_mode"] != 0:
            raise MT5TransportUnavailable("NON_DEMO_ACCOUNT_REFUSED")
        return {
            "connected": True,
            "trade_mode": 0,
            "currency": str(summary.get("currency", "")),
            "server": str(summary.get("server", "")),
        }
    finally:
        transport.shutdown()
