from __future__ import annotations

"""Fail-closed MT5 DEMO preflight.

This command validates the complete execution path without placing an order:
terminal initialization/login, DEMO-only account assertion, symbol mapping,
closed-bar availability, spread/tick freshness, and strategy evaluation.
"""

import argparse
import getpass
import os
from datetime import datetime, timezone

from .execution_adapters import MT5DemoConfig, MT5DemoExecutor
from .mt5_direct_strategy import DirectMT5StrategyConfig, evaluate_direct_strategy


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=os.getenv("MT5_VENUE_SYMBOL", "BTCUSD"))
    parser.add_argument("--canonical", default=os.getenv("MT5_CANONICAL_SYMBOL", "BTC/USDT"))
    parser.add_argument("--bars", type=int, default=int(os.getenv("MT5_BARS", "600")))
    parser.add_argument("--terminal-path", default=os.getenv("MT5_TERMINAL_PATH") or None)
    args = parser.parse_args()

    login_raw = os.getenv("MT5_LOGIN", "").strip() or input("MT5 DEMO login: ").strip()
    server = os.getenv("MT5_SERVER", "").strip() or input("MT5 DEMO server: ").strip()
    password = os.getenv("MT5_PASSWORD", "") or getpass.getpass("MT5 DEMO password (not stored): ")

    print("PREFLIGHT_START", datetime.now(timezone.utc).isoformat())
    executor = MT5DemoExecutor(
        MT5DemoConfig(
            allowed_symbols=(args.canonical,),
            symbol_map={args.canonical: args.symbol},
            max_order_notional=5000.0,
            max_spread_bps=35.0,
            submit_enabled=False,
        )
    )

    try:
        executor.connect(
            terminal_path=args.terminal_path,
            login=int(login_raw),
            password=password,
            server=server,
        )
        print("01_LOGIN=PASS")
        summary = executor.account_summary()
        print(
            "02_DEMO=PASS",
            f"login={summary['login']}",
            f"server={summary['server']}",
            f"trade_mode={summary['trade_mode']}",
        )

        matches = executor.search_symbols(args.symbol, limit=20)
        if args.symbol not in matches:
            print("03_SYMBOL=FAIL", f"requested={args.symbol}", f"matches={matches[:10]}")
            return 2
        print("03_SYMBOL=PASS", args.symbol)

        bars = executor.copy_closed_bars(args.symbol, "4h", count=args.bars)
        if len(bars) < 300:
            print("04_DATA=FAIL", f"closed_bars={len(bars)}")
            return 3
        print("04_DATA=PASS", f"closed_bars={len(bars)}", f"latest={bars[-1]['time']}")

        frame = __import__("pandas").DataFrame(bars)
        strategy_cfg = DirectMT5StrategyConfig(
            canonical_symbol=args.canonical,
            venue_symbol=args.symbol,
            strategy_name="H4_V59_CONFLUENCE_DEMO",
            bars=args.bars,
            ai_gate_enabled=True,
            risk_fraction=0.0025,
        )
        decision = evaluate_direct_strategy(executor, frame, strategy_cfg)
        print(
            "05_SIGNAL=" + ("FOUND" if decision.signal is not None else "NONE"),
            f"reason={decision.reason}",
        )
        if decision.ai_result is not None:
            print(
                "06_AI=" + ("PASS" if decision.ai_result.approved else "REJECT"),
                f"reason={decision.ai_result.reason}",
                f"brier={decision.ai_result.validation_brier}",
                f"p_favorable={decision.ai_result.probability_favorable}",
            )
        else:
            print("06_AI=NOT_RUN")

        print("07_EXECUTION=BLOCKED_BY_PREFLIGHT")
        print("PREFLIGHT_COMPLETE")
        return 0
    except Exception as exc:
        print("PREFLIGHT_FAIL", type(exc).__name__, str(exc))
        return 10
    finally:
        password = ""
        executor.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
