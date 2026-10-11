"""Controlled documented schemas. No credential, live exchange or order test."""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from research_bot.course.bitpin import (
    BitpinDataError,
    BitpinReadOnlyAdapter,
    PublicResponse,
    _validate_url,
    public_get,
)
from research_bot.course.contracts import MarketMetadata, UnsupportedCapability

NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append((url, timeout))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def response(payload, status=200, headers=()):
    return PublicResponse(status, headers, json.dumps(payload).encode())


def adapter(payload, **kwargs):
    transport = FakeTransport([response(payload)])
    result = BitpinReadOnlyAdapter(
        transport=transport, clock=lambda: NOW, sleep=lambda _: None, **kwargs
    )
    return result, transport


def test_documented_markets_preserve_decimal_precision_units_and_missing_minimum():
    ex, tx = adapter(
        [
            {
                "symbol": "BTC_IRT",
                "base": "BTC",
                "quote": "IRT",
                "tradable": True,
                "price_precision": 0,
                "base_amount_precision": 8,
            }
        ]
    )
    item = ex.get_markets()[0]
    assert item.quote_unit == "TOMAN"
    assert item.quality_status == "METADATA_INCOMPLETE_MINIMUM_NOTIONAL"
    assert tx.calls[0][0] == "https://api.bitpin.org/api/v1/mkt/markets/"
    with pytest.raises(UnsupportedCapability):
        ex.get_market_metadata("BTC/IRT")


@pytest.mark.parametrize("payload", [{"results": []}, [{"symbol": "BTC_IRT"}]])
def test_schema_is_not_guessed(payload):
    ex, _ = adapter(payload)
    with pytest.raises(BitpinDataError):
        ex.get_markets()


def test_ticker_exact_decimal_timestamp_and_availability():
    ex, _ = adapter(
        [
            {
                "symbol": "BTC_USDT",
                "price": "0.123456789123456789",
                "low": "0.1",
                "high": "0.2",
                "timestamp": NOW.timestamp() - 1,
            }
        ]
    )
    tick = ex.get_tickers()[0]
    assert tick.price == Decimal("0.123456789123456789")
    assert tick.available_at == NOW
    tick.validate_at(NOW)
    with pytest.raises(BitpinDataError):
        tick.validate_at(NOW - timedelta(seconds=1))
    with pytest.raises(BitpinDataError):
        tick.validate_at(NOW + timedelta(seconds=20))


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-1", "0"])
def test_invalid_ticker_financial_values_rejected(price):
    ex, _ = adapter(
        [
            {
                "symbol": "BTC_USDT",
                "price": price,
                "low": "1",
                "high": "2",
                "timestamp": NOW.timestamp(),
            }
        ]
    )
    with pytest.raises(BitpinDataError):
        ex.get_tickers()


def test_book_missing_time_sequence_is_not_invented():
    ex, _ = adapter({"bids": [["100.1", "2"]], "asks": [["100.2", "3"]]})
    book = ex.get_order_book("BTC/USDT")
    assert book.bids == ((Decimal("100.1"), Decimal(2)),)
    assert book.exchange_timestamp is None and book.sequence_id is None
    assert book.quality_status.startswith("DATA_UNAVAILABLE")
    metadata = MarketMetadata(
        "bitpin",
        "BTC/USDT",
        "BTC",
        "USDT",
        Decimal(".1"),
        Decimal(".01"),
        Decimal(1),
        "USDT",
    )
    with pytest.raises(UnsupportedCapability):
        book.to_snapshot(metadata)


@pytest.mark.parametrize(
    "book",
    [
        {"bids": [], "asks": [["2", "1"]]},
        {"bids": [["2", "1"]], "asks": [["1", "1"]]},
        {"bids": [["1", "1"], ["1", "2"]], "asks": [["2", "1"]]},
        {"bids": [["1", "-1"]], "asks": [["2", "1"]]},
        {"bids": [["1", "1", "secret"]], "asks": [["2", "1"]]},
    ],
)
def test_book_schema_ordering_and_sizes_fail_closed(book):
    ex, _ = adapter(book)
    with pytest.raises(BitpinDataError):
        ex.get_order_book("BTC/USDT")


def candle(opened, resolution="4h"):
    stamp = opened.timestamp()
    return {
        "open": "10",
        "high": "12",
        "low": "9",
        "close": "11",
        "volume": "1.12",
        "ts": stamp,
        "time": stamp * 1000,
        "resolution": resolution,
    }


def test_closed_candles_only_receipt_time_not_relabelled_prospective():
    start = NOW - timedelta(hours=8)
    ex, tx = adapter([candle(start), candle(NOW)])
    data = ex.get_historical_candles("BTC/USDT", start, NOW)
    assert len(data) == 1 and data[0].available_at == NOW
    assert "symbol=BTC_USDT" in tx.calls[0][0] and "res=4h" in tx.calls[0][0]
    assert data[0].volume == Decimal("1.12")
    with pytest.raises(BitpinDataError):
        data[0].validate_at(start + timedelta(hours=4))


def test_candle_resolution_silent_fallback_rejected():
    start = NOW - timedelta(hours=8)
    ex, _ = adapter([candle(start, "30m")])
    with pytest.raises(BitpinDataError, match="INVALID_CANDLE_SCHEMA"):
        ex.get_historical_candles("BTC/USDT", start, NOW)


def test_calendar_month_unimplemented_and_unknown_resolutions_blocked():
    ex, tx = adapter([])
    for resolution in ("1M", "2h", "oops"):
        with pytest.raises(UnsupportedCapability):
            ex.get_historical_candles(
                "BTC/USDT", NOW - timedelta(days=1), NOW, resolution
            )
    assert not tx.calls


def test_rate_limit_retry_after_and_get_schedule():
    elapsed = [0.0]
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        elapsed[0] += seconds

    tx = FakeTransport([response({}, 429, (("Retry-After", "2"),)), response([])])
    ex = BitpinReadOnlyAdapter(
        transport=tx,
        clock=lambda: NOW,
        sleep=sleep,
        monotonic=lambda: elapsed[0],
        jitter=lambda: 0,
    )
    assert ex.get_markets() == ()
    assert len(tx.calls) == 2 and sum(waits) >= 2


def test_long_rate_limit_deferred_not_ignored():
    tx = FakeTransport([response({}, 429, (("Retry-After", "86398"),))])
    ex = BitpinReadOnlyAdapter(
        transport=tx, monotonic=lambda: 0, clock=lambda: NOW, sleep=lambda _: None
    )
    with pytest.raises(BitpinDataError, match="RATE_LIMITED_RETRY_AFTER_DEFERRED"):
        ex.get_markets()
    with pytest.raises(BitpinDataError, match="CIRCUIT_OPEN"):
        ex.get_markets()
    assert len(tx.calls) == 1


def test_timeout_retry_exhaustion_then_circuit_open_redacts_errors():
    tx = FakeTransport(
        [TimeoutError("API_TOKEN=neverlog"), TimeoutError("password=secret")]
    )
    ex = BitpinReadOnlyAdapter(
        transport=tx, attempts=2, failure_threshold=1, sleep=lambda _: None
    )
    with pytest.raises(BitpinDataError, match="NETWORK_UNAVAILABLE") as info:
        ex.get_markets()
    assert "secret" not in str(info.value)
    with pytest.raises(BitpinDataError, match="CIRCUIT_OPEN"):
        ex.get_markets()
    assert len(tx.calls) == 2


def test_auth_private_execution_and_server_clock_are_explicitly_unsupported(
    monkeypatch,
):
    monkeypatch.setenv("BITPIN_API_TOKEN", "valid-looking-token")
    ex, tx = adapter([])
    for capability in (
        "authenticate",
        "refresh_session",
        "get_balances",
        "get_active_orders",
        "get_order_history",
        "get_order_status",
        "get_deposit_address",
        "get_supported_networks",
        "place_market_order",
        "place_limit_order",
        "place_stop_limit_order",
        "cancel_order",
        "place_bulk_orders",
    ):
        with pytest.raises(UnsupportedCapability):
            ex.require_capability(capability)
        assert not hasattr(ex, capability)
    with pytest.raises(UnsupportedCapability):
        ex.get_server_time()
    assert not tx.calls


@pytest.mark.parametrize(
    "url",
    [
        "http://api.bitpin.org/api/v1/mkt/markets/",
        "https://evil.invalid/api/v1/mkt/markets/",
        "https://api.bitpin.org/api/v1/odr/orders/",
        "https://api.bitpin.org/api/v1/mkt/markets/?token=secret",
        "https://api.bitpin.org/api/v1/mkt/markets/#foo",
    ],
)
def test_transport_url_allowlist_blocks_untrusted_or_private_requests(url):
    with pytest.raises(BitpinDataError):
        _validate_url(url)


def test_no_import_or_constructor_network_and_no_order_method():
    ex, tx = adapter([])
    assert not tx.calls
    assert not hasattr(ex, "submit_order")
    assert public_get.__defaults__ is None


def test_local_clock_regression_rejected():
    moments = iter((NOW, NOW - timedelta(seconds=1)))
    ex = BitpinReadOnlyAdapter(
        transport=FakeTransport([response([])]),
        clock=lambda: next(moments),
        sleep=lambda _: None,
    )
    with pytest.raises(BitpinDataError, match="LOCAL_CLOCK_REGRESSION"):
        ex.get_markets()
