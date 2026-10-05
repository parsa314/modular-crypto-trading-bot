from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from research_bot.tradingview_mt5_service import (
    _client_ip,
    _direct_config_from_payload,
    _require_local_admin,
)
from research_bot.tradingview_mt5_ui import control_panel_html
from research_bot.tradingview_tunnel import QuickTunnelManager, TunnelUnavailableError


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
    assert "H4_V59_CONFLUENCE_DEMO" in body or "directStrategy" in body
    assert 'id="directAI"' in body
    assert 'id="directBrier"' in body
    assert 'id="directAILong"' in body
    assert 'id="directAIShort"' in body


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


def test_quick_tunnel_fails_cleanly_when_cloudflared_is_missing(monkeypatch):
    monkeypatch.setattr("research_bot.tradingview_tunnel.shutil.which", lambda _: None)
    manager = QuickTunnelManager()
    with pytest.raises(TunnelUnavailableError, match="cloudflared"):
        manager.start()


def test_direct_payload_defaults_to_v59_ai_contract():
    executor = SimpleNamespace(
        config=SimpleNamespace(
            allowed_symbols=("BTC/USDT",),
            symbol_map={"BTC/USDT": "BTCUSD"},
        )
    )
    cfg, poll = _direct_config_from_payload(
        executor,
        {
            "canonical_symbol": "BTC/USDT",
            "venue_symbol": "BTCUSD",
        },
    )

    assert cfg.strategy_name == "H4_V59_CONFLUENCE_DEMO"
    assert cfg.risk_fraction == pytest.approx(0.0025)
    assert cfg.ai_gate_enabled is True
    assert cfg.ai_hurdle_bps == pytest.approx(24.0)
    assert cfg.ai_long_threshold == pytest.approx(0.56)
    assert cfg.ai_short_threshold == pytest.approx(0.44)
    assert cfg.ai_max_validation_brier == pytest.approx(0.28)
    assert poll == pytest.approx(15.0)


def test_direct_payload_rejects_wrong_broker_symbol():
    executor = SimpleNamespace(
        config=SimpleNamespace(
            allowed_symbols=("BTC/USDT",),
            symbol_map={"BTC/USDT": "BTCUSD"},
        )
    )
    with pytest.raises(ValueError, match="venue symbol mismatch"):
        _direct_config_from_payload(
            executor,
            {
                "canonical_symbol": "BTC/USDT",
                "venue_symbol": "BTCUSDm",
            },
        )
