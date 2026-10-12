from __future__ import annotations

import argparse
import json

from research_bot.bitpin_depth import fetch_bitpin_depth
from research_bot.bitpin_public import (
    fetch_bitpin_markets,
    fetch_bitpin_orderbook,
    fetch_bitpin_recent_trades,
    fetch_bitpin_ticker,
    fetch_bitpin_tickers,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Bitpin public API smoke test")
    parser.add_argument("--symbol", default="BTC/USDT")
    args = parser.parse_args()

    markets = fetch_bitpin_markets()
    tickers = fetch_bitpin_tickers()
    ticker = fetch_bitpin_ticker(args.symbol)
    book = fetch_bitpin_orderbook(args.symbol, depth=5)
    depth = fetch_bitpin_depth(args.symbol, limit=10)
    trades = fetch_bitpin_recent_trades(args.symbol)

    mid = (depth.best_bid + depth.best_ask) / 2.0
    ticker_price = float(ticker["price"])
    payload = {
        "mode": "READ_ONLY_PUBLIC",
        "credentials_used": False,
        "live_orders_enabled": False,
        "markets": int(len(markets)),
        "valid_tickers": int(len(tickers)),
        "invalid_ticker_rows_quarantined": int(tickers.attrs.get("invalid_price_rows", 0)),
        "raw_ticker_rows": int(tickers.attrs.get("raw_ticker_rows", len(tickers))),
        "symbol": args.symbol,
        "ticker": {
            "symbol": ticker.get("symbol"),
            "price": ticker_price,
            "low": float(ticker["low"]) if ticker.get("low") is not None else None,
            "high": float(ticker["high"]) if ticker.get("high") is not None else None,
            "daily_change_price": float(ticker["daily_change_price"]) if ticker.get("daily_change_price") is not None else None,
        },
        "orderbook": {
            "market": book["market"],
            "timestamp": book["timestamp"].isoformat(),
            "best_bid": depth.best_bid,
            "best_ask": depth.best_ask,
            "mid": mid,
            "spread_bps": depth.spread_bps,
            "crossed": bool(depth.best_ask < depth.best_bid),
            "top_5_bids": book["bids"],
            "top_5_asks": book["asks"],
        },
        "consistency": {
            "ticker_minus_mid": ticker_price - mid,
            "ticker_vs_mid_bps": ((ticker_price / mid) - 1.0) * 10000.0,
        },
        "recent_trade_count": int(len(trades)),
        "latest_trade_price": float(trades.iloc[-1]["price"]) if len(trades) else None,
        "latest_trade_timestamp": trades.iloc[-1]["timestamp"].isoformat() if len(trades) else None,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
