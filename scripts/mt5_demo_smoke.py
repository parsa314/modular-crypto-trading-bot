from __future__ import annotations

"""Fail-closed command-line smoke runner for MT5 DEMO only.

By default this script only validates that the connected account is DEMO.
Submitting an order requires BOTH:
1) --submit-demo
2) MT5_DEMO_SUBMIT_ENABLED=1

Credentials, if needed, are read from environment variables and never printed:
MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_TERMINAL_PATH.
"""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os

from research_bot.execution import ExecutionRequest, OrderSide
from research_bot.execution_adapters import MT5DemoConfig, MT5DemoExecutor


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--canonical-symbol", default="BTC/USDT")
    p.add_argument("--venue-symbol", default="BTCUSD")
    p.add_argument("--side", choices=("BUY", "SELL"), default="BUY")
    p.add_argument("--quantity", type=float)
    p.add_argument("--reference-price", type=float)
    p.add_argument("--stop-loss", type=float)
    p.add_argument("--take-profit", type=float)
    p.add_argument("--max-order-notional", type=float, default=5_000.0)
    p.add_argument("--max-spread-bps", type=float, default=35.0)
    p.add_argument("--submit-demo", action="store_true")
    return p


def main() -> int:
    args = _parser().parse_args()
    submit_env = os.getenv("MT5_DEMO_SUBMIT_ENABLED", "0") == "1"
    submit_enabled = bool(args.submit_demo and submit_env)

    cfg = MT5DemoConfig(
        allowed_symbols=(args.canonical_symbol,),
        symbol_map={args.canonical_symbol: args.venue_symbol},
        max_order_notional=args.max_order_notional,
        max_spread_bps=args.max_spread_bps,
        submit_enabled=submit_enabled,
    )
    executor = MT5DemoExecutor(cfg)

    login_raw = os.getenv("MT5_LOGIN")
    login = int(login_raw) if login_raw else None

    try:
        executor.connect(
            terminal_path=os.getenv("MT5_TERMINAL_PATH") or None,
            login=login,
            password=os.getenv("MT5_PASSWORD") or None,
            server=os.getenv("MT5_SERVER") or None,
        )
        print(
            json.dumps(
                {
                    "status": "MT5_DEMO_ACCOUNT_VALIDATED",
                    "submission_enabled": submit_enabled,
                    "canonical_symbol": args.canonical_symbol,
                    "venue_symbol": args.venue_symbol,
                },
                sort_keys=True,
            )
        )

        if not args.submit_demo:
            return 0
        if not submit_env:
            raise RuntimeError(
                "--submit-demo also requires MT5_DEMO_SUBMIT_ENABLED=1"
            )

        required = {
            "--quantity": args.quantity,
            "--reference-price": args.reference_price,
            "--stop-loss": args.stop_loss,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ValueError(
                "demo submission missing required arguments: " + ", ".join(missing)
            )

        now = datetime.now(timezone.utc)
        client_order_id = f"mt5demo-{int(now.timestamp())}-{args.side.lower()}"
        req = ExecutionRequest(
            client_order_id=client_order_id,
            symbol=args.canonical_symbol,
            side=OrderSide(args.side),
            quantity=float(args.quantity),
            reference_price=float(args.reference_price),
            created_at=now,
        )
        result = executor.execute(
            req,
            stop_loss=float(args.stop_loss),
            take_profit=args.take_profit,
            now=now,
        )
        payload = asdict(result)
        payload["timestamp"] = result.timestamp.isoformat()
        print(json.dumps(payload, sort_keys=True))
        return 0
    finally:
        executor.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
