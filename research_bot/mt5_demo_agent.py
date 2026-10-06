from __future__ import annotations

"""Resilient MT5 DEMO agent for the direct strategy path.

Run this process on the Windows/VPS machine that has MT5 Desktop installed and
logged into the same DEMO account. Credentials are runtime-only.

The agent does not connect to MT5 Web directly; it uses the official Python
integration through the local MT5 Desktop terminal.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import getpass
import os
import signal
import threading
import time
from typing import Any

from .execution_adapters import MT5DemoConfig, MT5DemoExecutor
from .mt5_direct_strategy import (
    DirectMT5StrategyConfig,
    DirectMT5StrategyWorker,
)


@dataclass(frozen=True)
class MT5AgentConfig:
    canonical_symbol: str = "BTC/USDT"
    venue_symbol: str = "BTCUSD"
    strategy_name: str = "H4_V59_CONFLUENCE_DEMO"
    bars: int = 600
    risk_percent: float = 0.25
    poll_seconds: float = 15.0
    reconnect_seconds: float = 10.0
    max_order_notional: float = 5_000.0
    max_spread_bps: float = 35.0
    ai_gate_enabled: bool = True
    ai_hurdle_bps: float = 24.0
    ai_long_threshold: float = 0.56
    ai_short_threshold: float = 0.44
    ai_max_validation_brier: float = 0.28


class MT5DemoAgent:
    def __init__(
        self,
        config: MT5AgentConfig,
        *,
        login: int,
        server: str,
        password: str,
        terminal_path: str | None = None,
    ):
        self.config = config
        self.login = int(login)
        self.server = str(server)
        self._password = str(password)
        self.terminal_path = terminal_path
        self._stop = threading.Event()
        self._worker: DirectMT5StrategyWorker | None = None
        self._executor: MT5DemoExecutor | None = None
        self._state_lock = threading.Lock()
        self._status: dict[str, Any] = {
            "state": "INITIALIZING",
            "last_error": "",
            "last_connected_at": None,
            "reconnects": 0,
        }

    def stop(self) -> None:
        self._stop.set()
        worker = self._worker
        if worker is not None:
            worker.stop()
        executor = self._executor
        if executor is not None:
            executor.shutdown()

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            result = dict(self._status)
        if self._executor is not None and self._executor.connected:
            result["mt5"] = self._executor.account_summary()
        if self._worker is not None:
            result["strategy"] = self._worker.status()
        return result

    def _connect(self) -> None:
        executor = MT5DemoExecutor(
            MT5DemoConfig(
                allowed_symbols=(self.config.canonical_symbol,),
                symbol_map={self.config.canonical_symbol: self.config.venue_symbol},
                max_order_notional=self.config.max_order_notional,
                max_spread_bps=self.config.max_spread_bps,
                submit_enabled=False,
            )
        )
        executor.connect(
            terminal_path=self.terminal_path,
            login=self.login,
            password=self._password,
            server=self.server,
        )

        # DEMO is verified by connect(); only after verification may the agent
        # enable order submission.
        executor.set_demo_submission_enabled(True)

        strategy = DirectMT5StrategyConfig(
            canonical_symbol=self.config.canonical_symbol,
            venue_symbol=self.config.venue_symbol,
            strategy_name=self.config.strategy_name,
            bars=self.config.bars,
            risk_fraction=self.config.risk_percent / 100.0,
            ai_gate_enabled=self.config.ai_gate_enabled,
            ai_hurdle_bps=self.config.ai_hurdle_bps,
            ai_long_threshold=self.config.ai_long_threshold,
            ai_short_threshold=self.config.ai_short_threshold,
            ai_max_validation_brier=self.config.ai_max_validation_brier,
        )
        worker = DirectMT5StrategyWorker(
            executor,
            strategy,
            poll_seconds=self.config.poll_seconds,
        )

        old_worker = self._worker
        old_executor = self._executor
        if old_worker is not None:
            old_worker.stop()
        if old_executor is not None:
            old_executor.shutdown()

        self._executor = executor
        self._worker = worker
        worker.start()

        with self._state_lock:
            self._status.update(
                {
                    "state": "RUNNING_DEMO",
                    "last_error": "",
                    "last_connected_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                }
            )

    def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                if self._executor is None or not self._executor.connected:
                    self._connect()
                elif self._worker is None or not self._worker.running:
                    if self._worker is not None:
                        self._worker.start()
                time.sleep(1.0)
            except Exception as exc:
                with self._state_lock:
                    self._status.update(
                        {
                            "state": "RECONNECT_WAIT",
                            "last_error": f"{type(exc).__name__}: {exc}",
                            "reconnects": int(self._status["reconnects"]) + 1,
                        }
                    )
                if self._executor is not None:
                    self._executor.shutdown()
                    self._executor = None
                self._worker = None
                self._stop.wait(self.config.reconnect_seconds)


def load_runtime_credentials() -> tuple[int, str, str, str | None]:
    login_raw = os.getenv("MT5_LOGIN", "").strip()
    if not login_raw:
        login_raw = input("MT5 DEMO login: ").strip()
    server = os.getenv("MT5_SERVER", "").strip()
    if not server:
        server = input("MT5 DEMO server: ").strip()
    password = os.getenv("MT5_PASSWORD", "")
    if not password:
        password = getpass.getpass("MT5 DEMO password (not stored): ")
    terminal_path = os.getenv("MT5_TERMINAL_PATH", "").strip() or None
    if not login_raw or not server or not password:
        raise ValueError("MT5 DEMO login, server and password are required")
    return int(login_raw), server, password, terminal_path


def main() -> int:
    login, server, password, terminal_path = load_runtime_credentials()
    config = MT5AgentConfig(
        canonical_symbol=os.getenv("MT5_CANONICAL_SYMBOL", "BTC/USDT"),
        venue_symbol=os.getenv("MT5_VENUE_SYMBOL", "BTCUSD"),
        strategy_name=os.getenv(
            "MT5_STRATEGY",
            "H4_V59_CONFLUENCE_DEMO",
        ),
        bars=int(os.getenv("MT5_BARS", "600")),
        risk_percent=float(os.getenv("MT5_RISK_PERCENT", "0.25")),
        poll_seconds=float(os.getenv("MT5_POLL_SECONDS", "15")),
        reconnect_seconds=float(os.getenv("MT5_RECONNECT_SECONDS", "10")),
        max_order_notional=float(
            os.getenv("MT5_MAX_ORDER_NOTIONAL", "5000")
        ),
        max_spread_bps=float(os.getenv("MT5_MAX_SPREAD_BPS", "35")),
        ai_gate_enabled=os.getenv("MT5_AI_GATE", "1").strip().lower()
        in {"1", "true", "yes", "on"},
        ai_hurdle_bps=float(os.getenv("MT5_AI_HURDLE_BPS", "24")),
        ai_long_threshold=float(
            os.getenv("MT5_AI_LONG_THRESHOLD", "0.56")
        ),
        ai_short_threshold=float(
            os.getenv("MT5_AI_SHORT_THRESHOLD", "0.44")
        ),
        ai_max_validation_brier=float(
            os.getenv("MT5_AI_MAX_BRIER", "0.28")
        ),
    )

    agent = MT5DemoAgent(
        config,
        login=login,
        server=server,
        password=password,
        terminal_path=terminal_path,
    )

    def stop(_signum, _frame) -> None:
        agent.stop()

    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)

    # Do not retain the password in the caller after agent construction.
    password = ""

    print(
        "MT5 DEMO agent starting:",
        config.strategy_name,
        config.canonical_symbol,
        config.venue_symbol,
    )
    agent.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
