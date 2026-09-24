"""Research backtest and forward observation entry point; exchange orders disabled."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from research_bot.coinex_public import fetch_coinex_klines
from research_bot.forward_paper_v14 import ForwardPaperConfig, ForwardPaperRunner
from research_bot.persistence import store_from_environment
from research_bot.spot_ichimoku_bot import SpotBacktestConfig, backtest_spot_ichimoku


def main() -> None:
    parser = argparse.ArgumentParser(description="CoinEx spot Ichimoku research bot")
    parser.add_argument("mode", choices=("backtest", "observe"))
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--bars", type=int, default=600)
    parser.add_argument("--initial-cash", type=float, default=10_000.0)
    parser.add_argument("--allocation", type=float, default=0.20)
    parser.add_argument("--fee-bps", type=float, default=10.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--output", type=Path, default=Path("artifacts/spot_ichimoku_result.json"))
    args = parser.parse_args()
    if args.bars < 122:
        parser.error("--bars must be at least 122")
    if args.mode == "backtest":
        bars = fetch_coinex_klines(args.symbol, period="4hour", market_type="spot", bars=args.bars)
        result = backtest_spot_ichimoku(
            bars, config=SpotBacktestConfig(
                initial_cash=args.initial_cash, allocation_fraction=args.allocation,
                fee_bps=args.fee_bps, slippage_bps=args.slippage_bps,
            ),
        )
        result["symbol"] = args.symbol
        result["source"] = "CoinEx public spot OHLCV"
    else:
        # The repository's scientific gate currently forbids paper and live orders.
        # A durable store is required so restarted observations cannot be relabelled.
        if not os.getenv("DATABASE_URL"):
            parser.error("observe requires DATABASE_URL for durable evidence")
        store = store_from_environment(initial_cash=args.initial_cash)
        runner = ForwardPaperRunner(
            store, config=ForwardPaperConfig(symbols=(args.symbol,), bars=args.bars),
            paper_execution_enabled=False,
        )
        result = runner.run_symbol(args.symbol)
        result["mode"] = "forward_observation_no_orders"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"mode": result["mode"], "symbol": args.symbol, "output": str(args.output),
                      "status": result.get("status", "completed")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
