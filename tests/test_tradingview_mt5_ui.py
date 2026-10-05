from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from research_bot.tradingview_mt5_service import _client_ip, _require_local_admin
from research_bot.tradingview_mt5_ui import control_panel_html
from research_bot.tradingview_tunnel import QuickTunnelManager


class FakeRequest:
    def __init__(self, host: str, headers=None):
        self.client = SimpleNamespace(host=host)
        self.headers = headers or {}


class FakeProcess:
    def __init__(self, lines, pid=321):
        self.stdout = list(lines)
        self.pid = pid

    def poll(self):
        return None


def test_control_panel_contains_mt5_and_tradingview_controls():
    response = control_panel_html()
    body = response.body.decode("utf-8")
    assert "MetaTrader 5 Demo" in body
    assert "TradingView" in body
    assert 'id="login"' in body
    assert 'id="password"' in body
    assert 'id="webhookUrl"' in body
    assert "ساخت لینک عمومی موقت" in body


def test_admin_surface_is_loopback_only():
    _require_local_admin(FakeRequest("127.0.0.1"))
    _require_local_admin(FakeRequest("::1"))

    with pytest.raises(HTTPException) as exc:
        _require_local_admin(FakeRequest("203.0.113.10"))
    assert exc.value.status_code == 403


def test_proxy_headers_are_trusted_only_from_loopback_tunnel():
    local = FakeRequest(
        "127.0.0.1",
        headers={
            "cf-connecting-ip": "52.89.214.238",
            "x-forwarded-for": "52.89.214.238",
        },
    )
    assert _client_ip(local, trust_proxy=True) == "52.89.214.238"

    remote_spoof = FakeRequest(
        "198.51.100.50",
        headers={"cf-connecting-ip": "52.89.214.238"},
    )
    assert _client_ip(remote_spoof, trust_proxy=True) == "198.51.100.50"


def test_quick_tunnel_reader_extracts_trycloudflare_url():
    manager = QuickTunnelManager()
    fake = FakeProcess(
        [
            "INF Starting tunnel\n",
            "INF Your quick Tunnel has been created! https://demo-abc.trycloudflare.com\n",
        ]
    )
    manager._process = fake  # type: ignore[assignment]
    manager._reader(fake)  # type: ignore[arg-type]
    status = manager.status()
    assert status.running is True
    assert status.public_url == "https://demo-abc.trycloudflare.com"
