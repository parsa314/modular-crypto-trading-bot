from __future__ import annotations

"""Headless direct registered-strategy -> MetaTrader 5 DEMO runner.

Credentials are read from environment variables or an interactive password
prompt. They are never written by this script.

Required/optional environment variables:
  MT5_LOGIN
  MT5_PASSWORD
  MT5_SERVER
  MT5_TERMINAL_PATH
  MT5_DEMO_SUBMIT_ENABLED=1   (second opt-in for DEMO order submission)
"""

import argparse
from dataclasses import asdict
import getpass
import json
import os
import signal
import sys
import time

from research_bot.execution_adapters import MT5DemoConfig, MT5DemoExecutor
from research_bot.mt5_direct_strategy import (
    DirectMT5StrategyWorker,
    MT5DirectStrategyConfig,
    registered_strategy_names,
)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run one registered strategy directly against MT5 DEMO"
    )
    p.add_argument("--canonical-symbol", default="BTC/USDT")
    p.add_argument("--venue-symbol", default="BTCUSD")
    p.add_argument("--strategy", default="H4_S6_BREAKOUT")
    p.add_argument("--bars", type=int, default=600)
    p.add_argument("--risk-percent", type=float, default=0.25)
    p.add_argument("--poll-seconds", type=float, default=15.0)
    p.add_argument("--max-order-notional", type=float, default=5000.0)
    p.add_argument("--max-spread-bps", type=float, default=35.0)
    p.add_argument("--submit-demo", action="store_true")
    p.add_argument("--list-strategies", action="store_true")
    return p


def _truthy(name: str) -> bool:
    return os.getenv(name, "0").strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    args = _parser().parse_args()

    if args.list_strategies:
        for name in registered_strategy_names():
            print(name)
        return 0

    login_raw = os.getenv("MT5_LOGIN", "").strip()
    if not login_raw:
        login_raw = input("MT5 DEMO login: ").strip()
    if not login_raw:
        raise SystemExit("MT5 DEMO login is required")

    server = os.getenv("MT5_SERVER", "").strip()
    if not server:
        server = input("MT5 DEMO server: ").strip()
    if not server:
        raise SystemExit("MT5 DEMO server is required")

    password = os.getenv("MT5_PASSWORD", "")
    if not password:
        password = getpass.getpass("MT5 DEMO password (not stored): ")
    if not password:
        raise SystemExit("MT5 DEMO password is required")

    terminal_path = os.getenv("MT5_TERMINAL_PATH", "").strip() or None
    submit_enabled = bool(args.submit_demo and _truthy("MT5_DEMO_SUBMIT_ENABLED"))

    executor = MT5DemoExecutor(
        MT5DemoConfig(
            allowed_symbols=(args.canonical_symbol,),
            symbol_map={args.canonical_symbol: args.venue_symbol},
            max_order_notional=float(args.max_order_notional),
            max_spread_bps=float(args.max_spread_bps),
            submit_enabled=submit_enabled,
        )
    )

    executor.connect(
        terminal_path=terminal_path,
        login=int(login_raw),
        password=password,
        server=server,
    )

    # Remove the local variable as soon as login has completed. This is not a
    # secure-memory wipe guarantee, but it avoids accidental later logging/use.
    password = ""

    config = MT5DirectStrategyConfig(
        canonical_symbol=args.canonical_symbol,
        venue_symbol=args.venue_symbol,
        strategy_name=args.strategy,
        bars=int(args.bars),
        risk_fraction=float(args.risk_percent) / 100.0,
    )
    worker = DirectMT5StrategyWorker(
        executor,
        config,
        poll_seconds=float(args.poll_seconds),
    )

    stop_requested = False

    def _stop(_signum, _frame):
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)

    try:
        worker.start()
        print(
            json.dumps(
                {
                    "status": "MT5_DIRECT_DEMO_STARTED",
                    "strategy": args.strategy,
                    "canonical_symbol": args.canonical_symbol,
                    "venue_symbol": args.venue_symbol,
                    "submission_enabled": submit_enabled,
                    "account": executor.account_summary(),
                    "live_money_allowed": False,
                },
                default=str,
                sort_keys=True,
            )
        )

        last_iterations = -1
        while not stop_requested:
            status = worker.status()
            if int(status["iterations"]) != last_iterations:
                print(json.dumps(status, default=str, sort_keys=True))
                last_iterations = int(status["iterations"])
            time.sleep(1.0)
        return 0
    finally:
        worker.stop()
        executor.shutdown()


if __name__ == "__main__":
    sys.exit(main())
