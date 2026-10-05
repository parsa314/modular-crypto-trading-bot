from __future__ import annotations

"""Dedicated TradingView -> MT5 DEMO FastAPI bridge with local control panel.

Run this service on the Windows host/VPS that owns the MetaTrader 5 terminal.
The public webhook surface is intentionally separated from local-only admin/UI
routes so exposing the webhook through HTTPS does not expose MT5 credentials or
connection controls.
"""

from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
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
from .tradingview_mt5_ui import control_panel_html
from .tradingview_tunnel import QuickTunnelManager, TunnelUnavailableError
from .mt5_direct_strategy import (
    DirectMT5StrategyWorker,
    MT5DirectStrategyConfig,
    registered_strategy_names,
)


DEFAULT_TRADINGVIEW_IPS = {
    "52.89.214.238",
    "34.212.75.30",
    "54.218.53.128",
    "52.32.178.7",
}
LOOPBACK_IPS = {"127.0.0.1", "::1"}


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
    public_base_url: str

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

        if enabled and route_token and len(route_token) < 24:
            raise RuntimeError(
                "TV_WEBHOOK_ROUTE_TOKEN must be at least 24 characters when configured"
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
            public_base_url=os.getenv("TV_PUBLIC_BASE_URL", "").strip().rstrip("/"),
        )


def _direct_client_ip(request: Request) -> str:
    if request.client is None:
        return ""
    return str(request.client.host)


def _require_local_admin(request: Request) -> None:
    """Admin/UI endpoints are local-only even if public webhook is exposed."""

    if _direct_client_ip(request) not in LOOPBACK_IPS:
        raise HTTPException(status_code=403, detail="local control panel only")


def _client_ip(request: Request, *, trust_proxy: bool) -> str:
    direct = _direct_client_ip(request)
    if trust_proxy and direct in LOOPBACK_IPS:
        cf_ip = request.headers.get("cf-connecting-ip", "").strip()
        if cf_ip:
            return cf_ip
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
    return direct


def _execute_background(
    bridge: TradingViewMT5Bridge,
    signal,
) -> None:
    bridge.execute(signal)


def _webhook_url(request: Request) -> str:
    base = str(getattr(request.app.state, "public_base_url", "") or "").rstrip("/")
    token = str(getattr(request.app.state, "route_token", "") or "")
    if not base or not token:
        return ""
    return f"{base}/webhooks/tradingview/{token}"


def _build_executor(
    *,
    allowed_symbols: tuple[str, ...],
    symbol_map: dict[str, str],
    max_order_notional: float,
    max_spread_bps: float,
    submit_enabled: bool,
) -> MT5DemoExecutor:
    return MT5DemoExecutor(
        MT5DemoConfig(
            allowed_symbols=allowed_symbols,
            symbol_map=symbol_map,
            max_order_notional=max_order_notional,
            max_spread_bps=max_spread_bps,
            submit_enabled=submit_enabled,
        )
    )


def _attach_bridge(app: FastAPI, executor: MT5DemoExecutor) -> None:
    journal = TradingViewWebhookJournal(app.state.settings.journal_path)
    app.state.executor = executor
    app.state.bridge = TradingViewMT5Bridge(executor=executor, journal=journal)
    app.state.mt5_connected = True


def _stop_direct_worker(app: FastAPI) -> None:
    worker = getattr(app.state, "direct_worker", None)
    if worker is not None:
        try:
            worker.stop()
        finally:
            app.state.direct_worker = None


def _detach_bridge(app: FastAPI) -> None:
    _stop_direct_worker(app)
    executor = getattr(app.state, "executor", None)
    if executor is not None:
        executor.shutdown()
    app.state.executor = None
    app.state.bridge = None
    app.state.mt5_connected = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = BridgeSettings.from_env()
    app.state.settings = settings
    app.state.bridge = None
    app.state.executor = None
    app.state.mt5_connected = False
    app.state.direct_worker = None
    app.state.route_token = settings.route_token or secrets.token_urlsafe(32)
    app.state.public_base_url = settings.public_base_url
    app.state.enforce_source_ip = settings.enforce_source_ip
    app.state.trust_proxy = settings.trust_proxy
    app.state.tunnel = QuickTunnelManager()

    if settings.enabled:
        executor = _build_executor(
            allowed_symbols=settings.allowed_symbols,
            symbol_map=settings.symbol_map,
            max_order_notional=settings.max_order_notional,
            max_spread_bps=settings.max_spread_bps,
            submit_enabled=settings.submit_enabled,
        )
        executor.connect(
            terminal_path=settings.terminal_path,
            login=settings.login,
            password=settings.password,
            server=settings.server,
        )
        _attach_bridge(app, executor)

    try:
        yield
    finally:
        tunnel = getattr(app.state, "tunnel", None)
        if tunnel is not None:
            tunnel.stop()
        _detach_bridge(app)


app = FastAPI(
    title="TradingView -> MT5 DEMO Bridge",
    version="1.1.0",
    description=(
        "Fail-closed webhook bridge and local control panel for TradingView "
        "alerts into a MetaTrader 5 DEMO account. Real-money MT5 accounts are refused."
    ),
    lifespan=lifespan,
)


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "service": "tradingview-mt5-demo-bridge",
        "ui": "/ui",
        "health": "/health",
        "live_money_allowed": False,
    }


@app.get("/ui")
def ui(request: Request):
    _require_local_admin(request)
    return control_panel_html()


@app.get("/health")
def health(request: Request) -> dict[str, Any]:
    executor: MT5DemoExecutor | None = request.app.state.executor
    return {
        "status": "ok",
        "bridge_enabled": request.app.state.bridge is not None,
        "mt5_connected": bool(request.app.state.mt5_connected),
        "demo_submission_enabled": (
            executor.submission_enabled if executor is not None else False
        ),
        "source_ip_enforcement": request.app.state.settings.enforce_source_ip,
        "direct_strategy_running": bool(
            getattr(request.app.state, "direct_worker", None)
            and request.app.state.direct_worker.running
        ),
        "live_money_allowed": False,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/ui/status")
def ui_status(request: Request) -> dict[str, Any]:
    _require_local_admin(request)
    executor: MT5DemoExecutor | None = request.app.state.executor
    mt5 = (
        executor.account_summary()
        if executor is not None
        else {"connected": False, "submission_enabled": False}
    )
    return {
        "mt5": mt5,
        "tradingview": {
            "route_token": request.app.state.route_token,
            "public_base_url": request.app.state.public_base_url,
            "webhook_url": _webhook_url(request),
            "source_ip_enforcement": bool(request.app.state.enforce_source_ip),
        },
        "tunnel": (
            request.app.state.tunnel.status().__dict__
            if getattr(request.app.state, "tunnel", None) is not None
            else {"running": False, "public_url": "", "error": "", "pid": None}
        ),
        "direct_strategy": (
            request.app.state.direct_worker.status()
            if getattr(request.app.state, "direct_worker", None) is not None
            else {"running": False}
        ),
        "live_money_allowed": False,
    }


@app.post("/api/ui/mt5/connect")
async def ui_connect_mt5(request: Request) -> dict[str, Any]:
    _require_local_admin(request)
    payload = await request.json()

    try:
        allowed_symbols = tuple(
            str(x).strip()
            for x in payload.get("allowed_symbols", [])
            if str(x).strip()
        )
        symbol_map_raw = payload.get("symbol_map", {})
        if not isinstance(symbol_map_raw, dict):
            raise ValueError("symbol_map must be an object")
        symbol_map = {
            str(k).strip(): str(v).strip()
            for k, v in symbol_map_raw.items()
            if str(k).strip() and str(v).strip()
        }
        if not allowed_symbols:
            raise ValueError("allowed_symbols cannot be empty")
        missing = [s for s in allowed_symbols if s not in symbol_map]
        if missing:
            raise ValueError(
                "missing broker mapping for: " + ", ".join(missing)
            )

        login_raw = str(payload.get("login", "")).strip()
        login = int(login_raw) if login_raw else None
        password = str(payload.get("password", "")) or None
        server = str(payload.get("server", "")).strip() or None
        terminal_path = str(payload.get("terminal_path", "")).strip() or None
        max_order_notional = float(payload.get("max_order_notional", 5000.0))
        max_spread_bps = float(payload.get("max_spread_bps", 35.0))

        # MetaTrader5 uses a process-global terminal connection. Disconnect
        # the previous session before creating a replacement so a later
        # shutdown cannot tear down the newly established session.
        _detach_bridge(request.app)
        executor = _build_executor(
            allowed_symbols=allowed_symbols,
            symbol_map=symbol_map,
            max_order_notional=max_order_notional,
            max_spread_bps=max_spread_bps,
            submit_enabled=False,
        )
        executor.connect(
            terminal_path=terminal_path,
            login=login,
            password=password,
            server=server,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    _attach_bridge(request.app, executor)
    return {
        "status": "MT5_DEMO_CONNECTED",
        "account": executor.account_summary(),
        "live_money_allowed": False,
    }


@app.post("/api/ui/mt5/disconnect")
def ui_disconnect_mt5(request: Request) -> dict[str, Any]:
    _require_local_admin(request)
    _detach_bridge(request.app)
    return {"status": "MT5_DISCONNECTED", "live_money_allowed": False}


@app.post("/api/ui/demo/submission")
async def ui_submission(request: Request) -> dict[str, Any]:
    _require_local_admin(request)
    executor: MT5DemoExecutor | None = request.app.state.executor
    if executor is None:
        raise HTTPException(status_code=409, detail="MT5 demo is not connected")
    payload = await request.json()
    enabled = bool(payload.get("enabled", False))
    try:
        executor.set_demo_submission_enabled(enabled)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "DEMO_SUBMISSION_ENABLED" if enabled else "DRY_RUN_ENABLED",
        "submission_enabled": executor.submission_enabled,
        "live_money_allowed": False,
    }


@app.get("/api/ui/mt5/symbols")
def ui_symbols(request: Request, q: str = "") -> dict[str, Any]:
    _require_local_admin(request)
    executor: MT5DemoExecutor | None = request.app.state.executor
    if executor is None:
        raise HTTPException(status_code=409, detail="MT5 demo is not connected")
    try:
        items = executor.search_symbols(q, limit=100)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": items}




@app.get("/api/ui/direct/strategies")
def ui_direct_strategies(request: Request) -> dict[str, Any]:
    _require_local_admin(request)
    return {"items": list(registered_strategy_names())}


@app.get("/api/ui/direct/status")
def ui_direct_status(request: Request) -> dict[str, Any]:
    _require_local_admin(request)
    worker: DirectMT5StrategyWorker | None = request.app.state.direct_worker
    return worker.status() if worker is not None else {"running": False}


@app.post("/api/ui/direct/start")
async def ui_direct_start(request: Request) -> dict[str, Any]:
    _require_local_admin(request)
    executor: MT5DemoExecutor | None = request.app.state.executor
    if executor is None or not executor.connected:
        raise HTTPException(status_code=409, detail="connect MT5 demo first")

    payload = await request.json()
    try:
        canonical_symbol = str(payload.get("canonical_symbol", "")).strip()
        venue_symbol = str(payload.get("venue_symbol", "")).strip()
        strategy_name = str(payload.get("strategy_name", "")).strip()
        bars = int(payload.get("bars", 600))
        risk_percent = float(payload.get("risk_percent", 0.25))
        poll_seconds = float(payload.get("poll_seconds", 15.0))
        risk_fraction = risk_percent / 100.0
        ai_gate_enabled = bool(payload.get("ai_gate_enabled", True))
        ai_hurdle_bps = float(payload.get("ai_hurdle_bps", 24.0))
        ai_long_threshold = float(payload.get("ai_long_threshold", 0.56))
        ai_short_threshold = float(payload.get("ai_short_threshold", 0.44))
        ai_max_validation_brier = float(
            payload.get("ai_max_validation_brier", 0.28)
        )

        if canonical_symbol not in executor.config.allowed_symbols:
            raise ValueError(
                f"canonical symbol is not allowlisted: {canonical_symbol}"
            )
        expected_venue = str(
            executor.config.symbol_map.get(canonical_symbol, canonical_symbol)
        )
        if venue_symbol != expected_venue:
            raise ValueError(
                f"venue symbol mismatch: expected {expected_venue}"
            )

        config = MT5DirectStrategyConfig(
            canonical_symbol=canonical_symbol,
            venue_symbol=venue_symbol,
            strategy_name=strategy_name,
            bars=bars,
            risk_fraction=risk_fraction,
            ai_gate_enabled=ai_gate_enabled,
            ai_hurdle_bps=ai_hurdle_bps,
            ai_long_threshold=ai_long_threshold,
            ai_short_threshold=ai_short_threshold,
            ai_max_validation_brier=ai_max_validation_brier,
        )
        _stop_direct_worker(request.app)
        worker = DirectMT5StrategyWorker(
            executor,
            config,
            poll_seconds=poll_seconds,
        )
        worker.start()
        request.app.state.direct_worker = worker
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    return {
        "status": "DIRECT_MT5_STRATEGY_STARTED",
        "worker": worker.status(),
        "demo_submission_enabled": executor.submission_enabled,
        "live_money_allowed": False,
    }


@app.post("/api/ui/direct/stop")
def ui_direct_stop(request: Request) -> dict[str, Any]:
    _require_local_admin(request)
    worker: DirectMT5StrategyWorker | None = request.app.state.direct_worker
    if worker is not None:
        worker.stop()
    request.app.state.direct_worker = None
    return {"status": "DIRECT_MT5_STRATEGY_STOPPED"}


@app.post("/api/ui/direct/evaluate-now")
async def ui_direct_evaluate_now(request: Request) -> dict[str, Any]:
    _require_local_admin(request)
    executor: MT5DemoExecutor | None = request.app.state.executor
    if executor is None or not executor.connected:
        raise HTTPException(status_code=409, detail="connect MT5 demo first")

    payload = await request.json()
    try:
        canonical_symbol = str(payload.get("canonical_symbol", "")).strip()
        venue_symbol = str(payload.get("venue_symbol", "")).strip()
        strategy_name = str(payload.get("strategy_name", "")).strip()
        bars = int(payload.get("bars", 600))
        risk_percent = float(payload.get("risk_percent", 0.25))
        risk_fraction = risk_percent / 100.0
        ai_gate_enabled = bool(payload.get("ai_gate_enabled", True))
        ai_hurdle_bps = float(payload.get("ai_hurdle_bps", 24.0))
        ai_long_threshold = float(payload.get("ai_long_threshold", 0.56))
        ai_short_threshold = float(payload.get("ai_short_threshold", 0.44))
        ai_max_validation_brier = float(
            payload.get("ai_max_validation_brier", 0.28)
        )

        if canonical_symbol not in executor.config.allowed_symbols:
            raise ValueError(
                f"canonical symbol is not allowlisted: {canonical_symbol}"
            )
        expected_venue = str(
            executor.config.symbol_map.get(canonical_symbol, canonical_symbol)
        )
        if venue_symbol != expected_venue:
            raise ValueError(
                f"venue symbol mismatch: expected {expected_venue}"
            )

        config = MT5DirectStrategyConfig(
            canonical_symbol=canonical_symbol,
            venue_symbol=venue_symbol,
            strategy_name=strategy_name,
            bars=bars,
            risk_fraction=risk_fraction,
            ai_gate_enabled=ai_gate_enabled,
            ai_hurdle_bps=ai_hurdle_bps,
            ai_long_threshold=ai_long_threshold,
            ai_short_threshold=ai_short_threshold,
            ai_max_validation_brier=ai_max_validation_brier,
        )
        temp = DirectMT5StrategyWorker(executor, config, poll_seconds=15.0)
        outcome = temp.evaluate_now()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    return {
        "status": "DIRECT_MT5_EVALUATED",
        "outcome": asdict(outcome),
        "demo_submission_enabled": executor.submission_enabled,
        "live_money_allowed": False,
    }

@app.post("/api/ui/tradingview/token")
def ui_new_token(request: Request) -> dict[str, Any]:
    _require_local_admin(request)
    request.app.state.route_token = secrets.token_urlsafe(32)
    return {
        "route_token": request.app.state.route_token,
        "webhook_url": _webhook_url(request),
    }


@app.post("/api/ui/tradingview/configure")
async def ui_configure_tradingview(request: Request) -> dict[str, Any]:
    _require_local_admin(request)
    payload = await request.json()
    base = str(payload.get("public_base_url", "")).strip().rstrip("/")
    if base and not base.lower().startswith("https://"):
        raise HTTPException(
            status_code=400,
            detail="Public TradingView URL must use https://",
        )
    request.app.state.public_base_url = base
    return {
        "status": "TRADINGVIEW_WEBHOOK_CONFIGURED",
        "route_token": request.app.state.route_token,
        "public_base_url": base,
        "webhook_url": _webhook_url(request),
    }


@app.post("/api/ui/tradingview/dry-run-test")
async def ui_tradingview_dry_run(request: Request) -> dict[str, Any]:
    _require_local_admin(request)
    bridge: TradingViewMT5Bridge | None = request.app.state.bridge
    if bridge is None:
        raise HTTPException(status_code=409, detail="connect MT5 demo first")
    payload = await request.json()
    try:
        signal = bridge.accept(payload)
    except TradingViewDuplicateError:
        return {
            "status": "DUPLICATE_IGNORED",
            "event_id": str(payload.get("event_id", "")),
        }
    except TradingViewPayloadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "status": "DRY_RUN_ACCEPTED",
        "event_id": signal.event_id,
        "symbol": signal.canonical_symbol,
        "action": signal.action,
        "order_sent": False,
    }


@app.get("/api/ui/events")
def ui_events(request: Request, limit: int = 30) -> dict[str, Any]:
    _require_local_admin(request)
    bridge: TradingViewMT5Bridge | None = request.app.state.bridge
    if bridge is None:
        return {"items": []}
    return {
        "items": bridge.journal.latest(min(max(int(limit), 1), 100))
    }



@app.post("/api/ui/tunnel/start")
def ui_tunnel_start(request: Request) -> dict[str, Any]:
    _require_local_admin(request)
    tunnel: QuickTunnelManager = request.app.state.tunnel
    try:
        status = tunnel.start(local_url="http://127.0.0.1:8000")
    except TunnelUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    request.app.state.trust_proxy = True
    request.app.state.enforce_source_ip = True
    if status.public_url:
        request.app.state.public_base_url = status.public_url
    return {
        "status": "TUNNEL_RUNNING" if status.public_url else "TUNNEL_STARTING",
        **status.__dict__,
    }


@app.get("/api/ui/tunnel/status")
def ui_tunnel_status(request: Request) -> dict[str, Any]:
    _require_local_admin(request)
    tunnel: QuickTunnelManager = request.app.state.tunnel
    status = tunnel.status()
    if status.public_url:
        request.app.state.public_base_url = status.public_url
    return {
        **status.__dict__,
        "webhook_url": _webhook_url(request),
    }


@app.post("/api/ui/tunnel/stop")
def ui_tunnel_stop(request: Request) -> dict[str, Any]:
    _require_local_admin(request)
    tunnel: QuickTunnelManager = request.app.state.tunnel
    status = tunnel.stop()
    return {"status": "TUNNEL_STOPPED", **status.__dict__}


@app.get("/bridge/recent/{route_token}")
def recent(
    route_token: str,
    request: Request,
    limit: int = 20,
) -> dict[str, Any]:
    bridge = request.app.state.bridge
    if bridge is None:
        raise HTTPException(status_code=503, detail="bridge disabled")
    try:
        verify_route_token(request.app.state.route_token, route_token)
    except TradingViewAuthError as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
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
    executor: MT5DemoExecutor | None = request.app.state.executor

    if bridge is None or executor is None:
        raise HTTPException(status_code=503, detail="MT5 demo bridge is not connected")

    try:
        verify_route_token(request.app.state.route_token, route_token)
    except TradingViewAuthError as exc:
        raise HTTPException(status_code=404, detail="not found") from exc

    source_ip = _client_ip(
        request,
        trust_proxy=bool(request.app.state.trust_proxy),
    )
    if bool(request.app.state.enforce_source_ip) and source_ip not in settings.source_ips:
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

    if not executor.submission_enabled:
        return {
            "status": "ACCEPTED_DRY_RUN",
            "event_id": signal.event_id,
            "symbol": signal.canonical_symbol,
            "action": signal.action,
            "live_money_allowed": False,
        }

    background_tasks.add_task(_execute_background, bridge, signal)

    return {
        "status": "ACCEPTED_FOR_MT5_DEMO",
        "event_id": signal.event_id,
        "symbol": signal.canonical_symbol,
        "action": signal.action,
        "live_money_allowed": False,
    }
