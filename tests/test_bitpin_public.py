from __future__ import annotations

from datetime import datetime, timezone
import math

import pytest

from research_bot.bitpin_depth import fetch_bitpin_depth
from research_bot.bitpin_public import (
    BitpinAPIError,
    _to_market,
    fetch_bitpin_markets,
    fetch_bitpin_orderbook,
    fetch_bitpin_recent_trades,
    fetch_bitpin_ticker,
    fetch_bitpin_tickers,
)


def test_symbol_normalization():
    assert _to_market("btc/usdt") == "BTC_USDT"
    assert _to_market("btc-usdt") == "BTC_USDT"
    assert _to_market("BTC_USDT") == "BTC_USDT"
    with pytest.raises(ValueError):
        _to_market("BTCUSDT")


def test_markets_accept_direct_list_and_normalize_symbol():
    def req(path, timeout):
        assert path == "/mkt/markets/"
        return [{"symbol": "btc_usdt", "tradable": True}]

    df = fetch_bitpin_markets(request_fn=req)
    assert df.iloc[0]["symbol"] == "BTC_USDT"


def test_ticker_strict_mode_rejects_nonfinite_or_zero_price():
    def bad(path, timeout):
        return [{"symbol": "BTC_USDT", "price": "nan"}]

    with pytest.raises(BitpinAPIError, match="invalid prices"):
        fetch_bitpin_tickers(strict=True, request_fn=bad)


def test_tickers_quarantine_invalid_provider_rows_but_keep_valid_market():
    def mixed(path, timeout):
        return [
            {"symbol": "OLD_USDT", "price": "0", "timestamp": 1_789_000_000},
            {"symbol": "BTC_USDT", "price": "65000", "timestamp": 1_789_000_001},
        ]

    df = fetch_bitpin_tickers(request_fn=mixed)
    assert list(df["symbol"]) == ["BTC_USDT"]
    assert df.attrs["invalid_price_rows"] == 1
    assert df.attrs["raw_ticker_rows"] == 2
    ticker = fetch_bitpin_ticker("BTC/USDT", request_fn=mixed)
    assert ticker["price"] == 65000.0
    assert ticker["invalid_price_rows_in_feed"] == 1


def test_exact_ticker_fails_closed_if_requested_market_has_no_valid_price():
    def req(path, timeout):
        return [
            {"symbol": "BTC_USDT", "price": "0"},
            {"symbol": "ETH_USDT", "price": "3000"},
        ]

    with pytest.raises(BitpinAPIError, match="No valid Bitpin ticker for BTC_USDT"):
        fetch_bitpin_ticker("BTC/USDT", request_fn=req)


def test_orderbook_is_sorted_validated_and_depth_limited():
    ts = 1_789_000_000

    def req(path, timeout):
        assert path == "/mth/orderbook/BTC_USDT/"
        return {
            "bids": [["99", "2"], ["98", "5"], ["100", "1"]],
            "asks": [["103", "2"], ["104", "5"], ["102", "1"]],
            "timestamp": ts,
        }

    book = fetch_bitpin_orderbook("BTC/USDT", depth=2, request_fn=req)
    assert book["bids"] == [(100.0, 1.0), (99.0, 2.0)]
    assert book["asks"] == [(102.0, 1.0), (103.0, 2.0)]
    assert book["timestamp"].tzinfo == timezone.utc


def test_depth_adapter_matches_execution_contract():
    def req(path, timeout):
        return {
            "bids": [["100", "2"], ["99", "3"]],
            "asks": [["101", "4"], ["102", "1"]],
            "updated_at": 1_789_000_000_000,
        }

    depth = fetch_bitpin_depth("BTC/USDT", limit=2, request_fn=req)
    assert depth.best_bid == 100.0
    assert depth.best_ask == 101.0
    assert math.isclose(depth.bid_depth, 5.0)
    assert math.isclose(depth.ask_depth, 5.0)
    assert math.isclose(depth.bid_depth_notional, 497.0)
    assert math.isclose(depth.ask_depth_notional, 506.0)
    assert depth.imbalance == 0.0


def test_crossed_book_fails_closed():
    def req(path, timeout):
        return {"bids": [["101", "1"]], "asks": [["100", "1"]]}

    with pytest.raises(BitpinAPIError, match="Crossed Bitpin book"):
        fetch_bitpin_orderbook("BTC/USDT", request_fn=req)


def test_recent_trades_support_paginated_results_envelope():
    def req(path, timeout):
        return {
            "results": [
                {
                    "id": 2,
                    "symbol": "BTC_USDT",
                    "price": "101",
                    "base_amount": "0.2",
                    "created_at": "2026-09-14T10:01:00Z",
                    "side": "buy",
                },
                {
                    "id": 1,
                    "symbol": "BTC_USDT",
                    "price": "100",
                    "base_amount": "0.1",
                    "created_at": "2026-09-14T10:00:00Z",
                    "side": "sell",
                },
            ]
        }

    df = fetch_bitpin_recent_trades("BTC/USDT", request_fn=req)
    assert list(df["id"]) == [1, 2]
    assert isinstance(df.iloc[0]["timestamp"], datetime)
