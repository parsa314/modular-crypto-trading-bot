"""Real Phase 1 MT5Forge Demo probe; no order_send and no invented connection.

Windows/Python >=3.12: configure MT5_DEMO_LOGIN, MT5_DEMO_PASSWORD,
MT5_DEMO_SERVER and MT5_DEMO_TERMINAL_PATH, then run this script.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.adapters.base import AdapterError
from src.adapters.mt5_broker import FORGE_VERSION, MT5Broker


async def probe(*, symbol: str = "EURUSD", timeframe: str = "H1", count: int = 100,
                volume: float | None = None, skip_order_check: bool = False) -> dict:
    try:
        installed = importlib.metadata.version("mt5forge")
    except importlib.metadata.PackageNotFoundError:
        installed = None
    result = {
        "phase": "1", "step": "MT5Forge Demo connection/history/check-only order",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "backend": "mt5forge", "reviewed_backend_version": FORGE_VERSION,
        "installed_backend_version": installed,
        "source": "https://pypi.org/project/mt5forge/1.0.3/",
        "source_api": ["MT5Connector.login(MT5Credentials)", "MarketDataFeed.get_candles(shift=1)",
                       "OrderBuilder.build_market_request(OrderRequest,price)", "guarded MetaTrader5.order_check"],
        "runtime": {"platform": platform.system(), "python": platform.python_version(),
                    "official_mt5_package_available": importlib.util.find_spec("MetaTrader5") is not None},
        "configured_demo_selectors": {key: bool(os.environ.get(key)) for key in
                                      ("MT5_DEMO_LOGIN", "MT5_DEMO_PASSWORD", "MT5_DEMO_SERVER", "MT5_DEMO_TERMINAL_PATH")},
        "symbol": symbol, "timeframe": timeframe, "requested_closed_bars": count,
        "connected": False, "verified_demo": False, "closed_bars_received": 0,
        "closed_candles": [],
        "open_positions_count": None, "order_check": None,
        "orders_submitted": 0, "fills": 0, "status": "UNAVAILABLE",
    }
    broker = MT5Broker()
    try:
        connected = await broker.connect()
        result.update(connected=connected["connected"], verified_demo=connected["account_type"] == "DEMO")
        bars = await broker.get_historical_data(symbol, timeframe, count)
        result.update(closed_bars_received=len(bars), first_bar_utc=bars.index[0].isoformat(),
                      last_bar_utc=bars.index[-1].isoformat(), last_close=float(bars.close.iloc[-1]),
                      volume_kind=bars.attrs["volume_kind"])
        result["closed_candles"] = [{"timestamp": timestamp.isoformat(),
                                      **{column: float(row[column]) for column in ("open", "high", "low", "close", "volume")}}
                                     for timestamp, row in bars.iterrows()]
        if len(bars) < count:
            raise AdapterError(f"Demo terminal returned {len(bars)} of {count} requested closed candles; load more actual history")
        positions = await broker.get_open_positions()
        result["open_positions_count"] = len(positions)
        if not skip_order_check:
            quote = await broker.get_quote(symbol)
            previous = bars.close.shift(1)
            true_range = pd.concat([bars.high - bars.low, (bars.high - previous).abs(),
                                    (bars.low - previous).abs()], axis=1).max(axis=1)
            distance = max(float(true_range.tail(14).mean()), quote["minimum_stop_distance"] * 2,
                           (quote["ask"] - quote["bid"]) * 3, quote["point"] * 10)
            result["order_check"] = await broker.place_order(
                symbol, "BUY", quote["min_volume"] if volume is None else volume,
                quote["bid"] - distance, quote["ask"] + 2 * distance)
            result["open_positions_count_after_check"] = len(await broker.get_open_positions())
        result["status"] = "DEMO_READ_ONLY_VERIFIED" if skip_order_check else "DEMO_CHECK_ONLY_VERIFIED"
    except (AdapterError, ValueError) as exc:
        result["error"] = str(exc)
        result["status"] = "CHECK_FAILED" if result["connected"] else "UNAVAILABLE"
    finally:
        await broker.close()
    result["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--timeframe", choices=("M1", "M5", "M15", "M30", "H1", "H4", "D1"), default="H1")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--volume", type=float)
    parser.add_argument("--skip-order-check", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "results/mt5_connection.json")
    parser.add_argument("--log", type=Path, default=ROOT / "logs/mt5_connection.log")
    args = parser.parse_args()
    if not 1 <= args.count <= 20000:
        parser.error("--count must be between 1 and 20000")
    result = asyncio.run(probe(symbol=args.symbol, timeframe=args.timeframe, count=args.count,
                               volume=args.volume, skip_order_check=args.skip_order_check))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    summary = {key: result[key] for key in ("phase", "step", "connected", "verified_demo", "closed_bars_received",
                                           "orders_submitted", "fills", "status")}
    if "error" in result:
        summary["error"] = result["error"]
    summary["timestamp_utc"] = result["finished_at_utc"]
    args.log.parent.mkdir(parents=True, exist_ok=True)
    with args.log.open("a") as stream:
        stream.write(json.dumps(summary, allow_nan=False) + "\n")
    if result["closed_candles"]:
        print(pd.DataFrame(result["closed_candles"]).to_string(index=False))
    print(json.dumps(summary, allow_nan=False))
    return 0 if result["status"] in {"DEMO_READ_ONLY_VERIFIED", "DEMO_CHECK_ONLY_VERIFIED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
