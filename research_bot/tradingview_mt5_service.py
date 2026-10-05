from __future__ import annotations

"""Dedicated TradingView -> MT5 DEMO FastAPI bridge.

Run this service on the Windows host/VPS that owns the MetaTrader 5 terminal.
It is intentionally separate from research_bot.service so the canonical
research-only deployment firewall remains untouched.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from .execution_adapters import MT5DemoConfig, MT5DemoExecutor
from .tradingview_mt5_bridge import (
    TradingViewAuthError,
    TradingViewDuplicateError,
    TradingViewMT5Bridge,
    TradingViewPayloadError,
    TradingViewWebhookJournal,
    verify_route_token,
)


DEFAULT_TRADINGVIEW_IPS = {
    "52.89.214.238",
    "34.212.75.30",
    "54.218.53.128",
    "52.32.178.7",
}


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in os.getenv(name, default).split(",")
        if item.strip()
    )


@dataclass(frozen=True)
class BridgeSettings:
    enabled: bool
    route_token: str
    allowed_symbols: tuple[str, ...]
    symbol_map: dict[str, str]
    journal_path: Path
    enforce_source_ip: bool
    trust_proxy: bool
    source_ips: frozenset[str]
    submit_enabled: bool
    terminal_path: str | None
    login: int | None
    password: str | None
    server: str | None
    max_order_notional: float
    max_spread_bps: float

    @classmethod
    def from_env(cls) -> "BridgeSettings":
        enabled = _truthy("TV_MT5_BRIDGE_ENABLED", False)
        route_token = os.getenv("TV_WEBHOOK_ROUTE_TOKEN", "").strip()

        allowed_symbols = _csv(
            "TV_ALLOWED_SYMBOLS",
            "BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT,DOGE/USDT",
        )
        raw_map = os.getenv(
            "TV_MT5_SYMBOL_MAP_JSON",
            '{"BTC/USDT":"BTCUSD","ETH/USDT":"ETHUSD",'
            '"SOL/USDT":"SOLUSD","XRP/USDT":"XRPUSD","DOGE/USDT":"DOGEUSD"}',
        )
        try:
            parsed_map = json.loads(raw_map)
        except json.JSONDecodeError as exc:
            raise RuntimeError("TV_MT5_SYMBOL_MAP_JSON must be valid JSON") from exc
        if not isinstance(parsed_map, dict):
            raise RuntimeError("TV_MT5_SYMBOL_MAP_JSON must be a JSON object")
        symbol_map = {str(k): str(v) for k, v in parsed_map.items()}

        login_raw = os.getenv("MT5_LOGIN", "").strip()
        login = int(login_raw) if login_raw else None

        ips = frozenset(
            _csv(
                "TRADINGVIEW_SOURCE_IPS",
                ",".join(sorted(DEFAULT_TRADINGVIEW_IPS)),
            )
        )

        if enabled and len(route_token) < 24:
            raise RuntimeError(
                "TV_WEBHOOK_ROUTE_TOKEN must be at least 24 characters when bridge is enabled"
            )
        if enabled and not allowed_symbols:
            raise RuntimeError("TV_ALLOWED_SYMBOLS cannot be empty")

        return cls(
            enabled=enabled,
            route_token=route_token,
            allowed_symbols=allowed_symbols,
            symbol_map=symbol_map,
            journal_path=Path(
                os.getenv(
                    "TV_WEBHOOK_JOURNAL",
                    "results/tradingview_mt5_webhook_journal.jsonl",
                )
            ),
            enforce_source_ip=_truthy("TRADINGVIEW_ENFORCE_SOURCE_IP", True),
            trust_proxy=_truthy("TRADINGVIEW_TRUST_PROXY", False),
            source_ips=ips,
            submit_enabled=_truthy("MT5_DEMO_SUBMIT_ENABLED", False),
            terminal_path=os.getenv("MT5_TERMINAL_PATH") or None,
            login=login,
            password=os.getenv("MT5_PASSWORD") or None,
            server=os.getenv("MT5_SERVER") or None,
            max_order_notional=float(
                os.getenv("MT5_MAX_ORDER_NOTIONAL", "5000")
            ),
            max_spread_bps=float(os.getenv("MT5_MAX_SPREAD_BPS", "35")),
        )


def _client_ip(request: Request, *, trust_proxy: bool) -> str:
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
    if request.client is None:
        return ""
    return str(request.client.host)


def _execute_background(
    bridge: TradingViewMT5Bridge,
    signal,
) -> None:
    bridge.execute(signal)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = BridgeSettings.from_env()
    app.state.settings = settings
    app.state.bridge = None
    app.state.executor = None
    app.state.mt5_connected = False

    if settings.enabled:
        cfg = MT5DemoConfig(
            allowed_symbols=settings.allowed_symbols,
            symbol_map=settings.symbol_map,
            max_order_notional=settings.max_order_notional,
            max_spread_bps=settings.max_spread_bps,
            submit_enabled=settings.submit_enabled,
        )
        executor = MT5DemoExecutor(cfg)
        executor.connect(
            terminal_path=settings.terminal_path,
            login=settings.login,
            password=settings.password,
            server=settings.server,
        )
        journal = TradingViewWebhookJournal(settings.journal_path)
        app.state.executor = executor
        app.state.bridge = TradingViewMT5Bridge(
            executor=executor,
            journal=journal,
        )
        app.state.mt5_connected = True

    try:
        yield
    finally:
        executor = getattr(app.state, "executor", None)
        if executor is not None:
            executor.shutdown()
        app.state.mt5_connected = False


app = FastAPI(
    title="TradingView -> MT5 DEMO Bridge",
    version="1.0.0",
    description=(
        "Fail-closed webhook bridge for TradingView alerts into a MetaTrader 5 "
        "DEMO account. Real-money MT5 accounts are refused by the executor."
    ),
    lifespan=lifespan,
)


@app.get("/health")
def health(request: Request) -> dict[str, Any]:
    settings: BridgeSettings = request.app.state.settings
    return {
        "status": "ok",
        "bridge_enabled": settings.enabled,
        "mt5_connected": bool(request.app.state.mt5_connected),
        "demo_submission_enabled": settings.submit_enabled,
        "allowed_symbols": list(settings.allowed_symbols),
        "source_ip_enforcement": settings.enforce_source_ip,
        "live_money_allowed": False,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/bridge/recent")
def recent(request: Request, limit: int = 20) -> dict[str, Any]:
    bridge = request.app.state.bridge
    if bridge is None:
        raise HTTPException(status_code=503, detail="bridge disabled")
    return {
        "items": bridge.journal.latest(min(max(limit, 1), 100)),
        "live_money_allowed": False,
    }


@app.post("/webhooks/tradingview/{route_token}", status_code=202)
async def tradingview_webhook(
    route_token: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    settings: BridgeSettings = request.app.state.settings
    bridge: TradingViewMT5Bridge | None = request.app.state.bridge

    if not settings.enabled or bridge is None:
        raise HTTPException(status_code=503, detail="bridge disabled")

    try:
        verify_route_token(settings.route_token, route_token)
    except TradingViewAuthError as exc:
        raise HTTPException(status_code=404, detail="not found") from exc

    source_ip = _client_ip(request, trust_proxy=settings.trust_proxy)
    if settings.enforce_source_ip and source_ip not in settings.source_ips:
        raise HTTPException(status_code=403, detail="source IP not allowlisted")

    content_type = request.headers.get("content-type", "").lower()
    if "application/json" not in content_type:
        raise HTTPException(
            status_code=415,
            detail="TradingView alert message must be valid JSON",
        )

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid JSON body") from exc

    try:
        signal = bridge.accept(payload)
    except TradingViewDuplicateError:
        return {
            "status": "DUPLICATE_OR_AMBIGUOUS_IGNORED",
            "event_id": str(payload.get("event_id", "")),
            "live_money_allowed": False,
        }
    except TradingViewPayloadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not settings.submit_enabled:
        return {
            "status": "ACCEPTED_DRY_RUN",
            "event_id": signal.event_id,
            "symbol": signal.canonical_symbol,
            "action": signal.action,
            "live_money_allowed": False,
        }

    # Return immediately so TradingView is not blocked by MT5 order latency.
    # Execution continues after the HTTP 202 response.
    background_tasks.add_task(_execute_background, bridge, signal)

    return {
        "status": "ACCEPTED_FOR_MT5_DEMO",
        "event_id": signal.event_id,
        "symbol": signal.canonical_symbol,
        "action": signal.action,
        "live_money_allowed": False,
    }
