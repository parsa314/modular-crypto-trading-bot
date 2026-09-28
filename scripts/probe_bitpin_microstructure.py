from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from research_bot.bitpin_public import fetch_bitpin_orderbook, fetch_bitpin_ticker


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * q
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi - k) + xs[hi] * (k - lo)


def summarize(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "median": None, "p95": None, "max": None, "mean": None}
    return {
        "min": min(values),
        "median": statistics.median(values),
        "p95": pct(values, 0.95),
        "max": max(values),
        "mean": statistics.fmean(values),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sample Bitpin public BTC/USDT microstructure without credentials")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--interval", type=float, default=0.5, help="minimum sleep between completed samples")
    parser.add_argument("--depth", type=int, default=10)
    parser.add_argument("--out-dir", default="artifacts")
    args = parser.parse_args()

    if args.samples < 1:
        raise SystemExit("--samples must be >= 1")
    if args.interval < 0:
        raise SystemExit("--interval must be >= 0")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []

    for i in range(args.samples):
        sample_started = time.perf_counter()
        observed_at = datetime.now(timezone.utc)
        try:
            t0 = time.perf_counter()
            ticker = fetch_bitpin_ticker(args.symbol)
            ticker_latency_ms = (time.perf_counter() - t0) * 1000.0

            t1 = time.perf_counter()
            book = fetch_bitpin_orderbook(args.symbol, depth=args.depth)
            book_latency_ms = (time.perf_counter() - t1) * 1000.0

            bids = book["bids"]
            asks = book["asks"]
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            mid = (best_bid + best_ask) / 2.0
            spread = best_ask - best_bid
            spread_bps = (spread / mid) * 10000.0
            ticker_price = float(ticker["price"])
            ticker_vs_mid_bps = ((ticker_price / mid) - 1.0) * 10000.0

            bid_qty = sum(float(qty) for _, qty in bids)
            ask_qty = sum(float(qty) for _, qty in asks)
            denom = bid_qty + ask_qty
            imbalance = (bid_qty - ask_qty) / denom if denom > 0 else 0.0

            bid_notional = sum(float(price) * float(qty) for price, qty in bids)
            ask_notional = sum(float(price) * float(qty) for price, qty in asks)

            row = {
                "sample": i + 1,
                "observed_at_utc": observed_at.isoformat(),
                "ticker_latency_ms": ticker_latency_ms,
                "orderbook_latency_ms": book_latency_ms,
                "quote_timestamp": book["timestamp"].isoformat(),
                "ticker_price": ticker_price,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "mid": mid,
                "spread": spread,
                "spread_bps": spread_bps,
                "ticker_vs_mid_bps": ticker_vs_mid_bps,
                "bid_qty_depth": bid_qty,
                "ask_qty_depth": ask_qty,
                "book_imbalance": imbalance,
                "bid_notional_depth": bid_notional,
                "ask_notional_depth": ask_notional,
                "crossed": best_ask < best_bid,
            }
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False))
        except Exception as exc:  # probe must record provider/network failures instead of hiding them
            failure = {
                "sample": i + 1,
                "observed_at_utc": observed_at.isoformat(),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            failures.append(failure)
            print(json.dumps(failure, ensure_ascii=False))

        elapsed = time.perf_counter() - sample_started
        if i + 1 < args.samples and args.interval > elapsed:
            time.sleep(args.interval - elapsed)

    csv_path = out_dir / "bitpin_microstructure_samples.csv"
    if rows:
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    spreads = [float(r["spread_bps"]) for r in rows]
    ticker_mid = [abs(float(r["ticker_vs_mid_bps"])) for r in rows]
    ticker_latency = [float(r["ticker_latency_ms"]) for r in rows]
    book_latency = [float(r["orderbook_latency_ms"]) for r in rows]
    imbalances = [float(r["book_imbalance"]) for r in rows]
    crossed_count = sum(bool(r["crossed"]) for r in rows)

    success_rate = len(rows) / args.samples
    summary = {
        "mode": "READ_ONLY_PUBLIC",
        "credentials_used": False,
        "live_orders_enabled": False,
        "symbol": args.symbol,
        "requested_samples": args.samples,
        "successful_samples": len(rows),
        "failed_samples": len(failures),
        "success_rate": success_rate,
        "crossed_books": crossed_count,
        "spread_bps": summarize(spreads),
        "abs_ticker_vs_mid_bps": summarize(ticker_mid),
        "ticker_latency_ms": summarize(ticker_latency),
        "orderbook_latency_ms": summarize(book_latency),
        "book_imbalance": summarize(imbalances),
        "first_observed_at_utc": rows[0]["observed_at_utc"] if rows else None,
        "last_observed_at_utc": rows[-1]["observed_at_utc"] if rows else None,
        "failures": failures,
        "gate": {
            "min_success_rate": 0.90,
            "requires_no_crossed_books": True,
            "requires_positive_spread": True,
        },
    }

    json_path = out_dir / "bitpin_microstructure_summary.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SUMMARY_JSON=" + json.dumps(summary, ensure_ascii=False))

    positive_spreads = all(float(r["spread_bps"]) > 0.0 for r in rows)
    passed = success_rate >= 0.90 and crossed_count == 0 and positive_spreads
    print(f"PROBE_PASS={str(passed).lower()}")
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
