"""Verified public Bitpin GETs; no credential, authenticated or order transport.

ENGINEERING_EXTENSION. Official documentation provenance is in
``docs/C_BITPIN_API_DECISION.md``. Network I/O occurs only on explicit reads.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import random
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .contracts import (
    BookSnapshot,
    MarketMetadata,
    UnsupportedCapability,
    aware,
    decimal,
)

BASE_URL = "https://api.bitpin.org"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
RESOLUTIONS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "3h": 10800,
    "4h": 14400,
    "6h": 21600,
    "1d": 86400,
    "1w": 604800,
}
PUBLIC_CAPABILITIES = frozenset(
    {"get_markets", "get_tickers", "get_order_book", "get_historical_candles"}
)


class BitpinDataError(RuntimeError):
    """Redacted provider error with a stable reason code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class PublicResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes


class PublicGetPort(Protocol):
    def __call__(self, url: str, timeout: float) -> PublicResponse: ...


class ResearchDataPort(Protocol):
    def get_markets(self) -> tuple[MarketObservation, ...]: ...
    def get_tickers(self) -> tuple[TickerObservation, ...]: ...
    def get_order_book(self, symbol: str) -> BookObservation: ...
    def get_market_metadata(self, symbol: str) -> MarketMetadata: ...
    def get_server_time(self) -> datetime: ...
    def get_historical_candles(
        self, symbol: str, start: datetime, end: datetime, resolution: str = "4h"
    ) -> tuple[CandleObservation, ...]: ...


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validate_url(url: str) -> None:
    parts = urlsplit(url)
    allowed = parts.path in {
        "/api/v1/mkt/markets/",
        "/api/v1/mkt/tickers/",
        "/api/v1/mkt/candles/",
    }
    allowed = allowed or bool(
        re.fullmatch(r"/api/v1/mth/orderbook/[A-Z0-9]+_[A-Z0-9]+/", parts.path)
    )
    try:
        query = parse_qs(parts.query, strict_parsing=True) if parts.query else {}
    except ValueError:
        raise BitpinDataError("PUBLIC_QUERY_NOT_ALLOWLISTED") from None
    if query and (
        parts.path != "/api/v1/mkt/candles/"
        or set(query) != {"symbol", "from", "to", "res"}
        or any(len(v) != 1 for v in query.values())
    ):
        raise BitpinDataError("PUBLIC_QUERY_NOT_ALLOWLISTED")
    if (
        parts.scheme != "https"
        or parts.netloc != "api.bitpin.org"
        or parts.fragment
        or not allowed
    ):
        raise BitpinDataError("PUBLIC_ENDPOINT_NOT_ALLOWLISTED")


def public_get(url: str, timeout: float) -> PublicResponse:
    _validate_url(url)
    req = Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "modular-crypto-research-course-readonly/1",
        },
    )
    try:
        with build_opener(_NoRedirects()).open(req, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise BitpinDataError("RESPONSE_TOO_LARGE")
            return PublicResponse(
                response.status, tuple(response.headers.items()), body
            )
    except HTTPError as exc:
        # Provider response bodies can contain sensitive diagnostics; never log them.
        return PublicResponse(exc.code, tuple(exc.headers.items()), b"")
    except (URLError, TimeoutError, OSError):
        raise BitpinDataError("NETWORK_UNAVAILABLE") from None


def _symbol(value: str) -> str:
    if not isinstance(value, str):
        raise BitpinDataError("INVALID_SYMBOL")
    result = value.strip().upper().replace("/", "_")
    if not re.fullmatch(r"[A-Z0-9]+_[A-Z0-9]+", result):
        raise BitpinDataError("INVALID_SYMBOL")
    return result


def _timestamp(value: object) -> datetime:
    try:
        seconds = decimal(value, positive=True)
        return datetime.fromtimestamp(float(seconds), timezone.utc)
    except (ValueError, OverflowError, TypeError, ArithmeticError):
        raise BitpinDataError("INVALID_EXCHANGE_TIMESTAMP") from None


def _number(value: object, *, positive: bool = True) -> Decimal:
    try:
        return decimal(value, positive=positive)
    except (ValueError, TypeError, ArithmeticError):
        raise BitpinDataError("INVALID_FINANCIAL_VALUE") from None


@dataclass(frozen=True)
class MarketObservation:
    symbol: str
    base: str
    quote: str
    quote_unit: str
    tradable: bool
    price_precision: int
    base_amount_precision: int
    received_at: datetime
    available_at: datetime
    source_hash: str
    quality_status: str = "METADATA_INCOMPLETE_MINIMUM_NOTIONAL"
    venue: str = "bitpin"
    data_version: str = "BITPIN_PUBLIC_DOCS_2026_10_10"


@dataclass(frozen=True)
class TickerObservation:
    symbol: str
    price: Decimal
    low: Decimal
    high: Decimal
    exchange_timestamp: datetime
    received_at: datetime
    available_at: datetime
    source_hash: str
    venue: str = "bitpin"
    data_version: str = "BITPIN_PUBLIC_DOCS_2026_10_10"

    def validate_at(
        self, decision_time: datetime, max_age: timedelta = timedelta(seconds=10)
    ) -> None:
        aware(decision_time)
        if (
            self.available_at > decision_time
            or decision_time - self.exchange_timestamp > max_age
        ):
            raise BitpinDataError("TICKER_UNAVAILABLE_AT_DECISION")


@dataclass(frozen=True)
class BookObservation:
    symbol: str
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    observed_at: datetime
    received_at: datetime
    available_at: datetime
    source_hash: str
    exchange_timestamp: None = None
    sequence_id: None = None
    quality_status: str = "DATA_UNAVAILABLE_MISSING_EXCHANGE_TIME_AND_SEQUENCE"
    venue: str = "bitpin"
    data_version: str = "BITPIN_PUBLIC_DOCS_2026_10_10"

    def to_snapshot(self, market: MarketMetadata) -> BookSnapshot:
        # Never invent venue timestamps or sequence identity from receipt time.
        raise UnsupportedCapability("VERIFIED_BOOK_TIMESTAMP_AND_SEQUENCE_UNAVAILABLE")


@dataclass(frozen=True)
class CandleObservation:
    symbol: str
    opening_time: datetime
    closing_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    resolution: str
    received_at: datetime
    available_at: datetime
    source_hash: str
    venue: str = "bitpin"
    data_version: str = "BITPIN_PUBLIC_DOCS_2026_10_10"

    def validate_at(self, decision_time: datetime) -> None:
        aware(decision_time)
        if self.available_at > decision_time or self.closing_time > decision_time:
            raise BitpinDataError("CANDLE_NOT_AVAILABLE_AT_DECISION")


class BitpinReadOnlyAdapter:
    """Explicit reads only. Local scheduling is per adapter, not IP-wide coordination."""

    capabilities = PUBLIC_CAPABILITIES

    def __init__(
        self,
        *,
        transport: PublicGetPort = public_get,
        timeout: float = 10,
        attempts: int = 3,
        failure_threshold: int = 3,
        cooldown: float = 30,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        jitter: Callable[[], float] = random.random,
    ) -> None:
        if (
            not math.isfinite(timeout)
            or not 0 < timeout <= 60
            or attempts not in {1, 2, 3, 4, 5}
            or failure_threshold < 1
            or not math.isfinite(cooldown)
            or cooldown <= 0
        ):
            raise ValueError("INVALID_READ_POLICY")
        self._transport, self._timeout, self._attempts = transport, timeout, attempts
        self._threshold, self._cooldown = failure_threshold, cooldown
        self._sleep, self._mono, self._clock, self._jitter = (
            sleep,
            monotonic,
            clock,
            jitter,
        )
        self._failures, self._open_until, self._next_request = 0, 0.0, 0.0
        self._lock = threading.Lock()

    def _retry_after(self, headers: tuple[tuple[str, str], ...]) -> float | None:
        raw = next((v for k, v in headers if k.lower() == "retry-after"), None)
        if raw is None:
            return None
        try:
            seconds = float(raw)
            if not math.isfinite(seconds) or seconds < 0:
                return None
        except ValueError:
            try:
                deadline = parsedate_to_datetime(raw)
                aware(deadline)
                seconds = max(0.0, (deadline - self._clock()).total_seconds())
            except (TypeError, ValueError):
                return None
        return seconds

    def _read(
        self,
        path: str,
        params: dict[str, object] | None = None,
        *,
        per_minute: int = 60,
    ) -> tuple[object, datetime, datetime, str]:
        url = BASE_URL + path + (("?" + urlencode(params)) if params else "")
        _validate_url(url)
        with self._lock:
            if self._mono() < self._open_until:
                raise BitpinDataError("CIRCUIT_OPEN")
            started = self._clock()
            aware(started)
            last_error = "NETWORK_UNAVAILABLE"
            for attempt in range(self._attempts):
                self._sleep(max(0.0, self._next_request - self._mono()))
                self._next_request = self._mono() + 60 / per_minute
                retry_after = None
                try:
                    response = self._transport(url, self._timeout)
                    if response.status == 200:
                        if len(response.body) > MAX_RESPONSE_BYTES:
                            raise BitpinDataError("RESPONSE_TOO_LARGE")
                        try:
                            payload = json.loads(
                                response.body,
                                parse_float=Decimal,
                                parse_constant=lambda _: (_ for _ in ()).throw(
                                    ValueError()
                                ),
                            )
                        except (ValueError, UnicodeError):
                            raise BitpinDataError("INVALID_JSON") from None
                        received = self._clock()
                        aware(received)
                        if received < started:
                            raise BitpinDataError("LOCAL_CLOCK_REGRESSION")
                        self._failures = 0
                        return (
                            payload,
                            started,
                            received,
                            hashlib.sha256(response.body).hexdigest(),
                        )
                    if response.status == 429:
                        last_error = "RATE_LIMITED"
                        retry_after = self._retry_after(response.headers)
                        if retry_after is not None:
                            self._next_request = max(
                                self._next_request, self._mono() + retry_after
                            )
                            # Do not block a worker for arbitrarily long provider backoffs.
                            if retry_after > 30:
                                self._open_until = self._mono() + retry_after
                                raise BitpinDataError(
                                    "RATE_LIMITED_RETRY_AFTER_DEFERRED"
                                )
                    elif response.status in {500, 502, 503, 504}:
                        last_error = "PROVIDER_UNAVAILABLE"
                    else:
                        raise BitpinDataError(f"HTTP_{response.status}")
                except (TimeoutError, OSError):
                    last_error = "NETWORK_UNAVAILABLE"
                except BitpinDataError as exc:
                    if exc.code != "NETWORK_UNAVAILABLE":
                        self._record_failure()
                        raise
                    last_error = exc.code
                if attempt + 1 < self._attempts:
                    self._sleep(
                        max(retry_after or 0, min(8.0, 2**attempt + self._jitter()))
                    )
            self._record_failure()
            raise BitpinDataError(last_error)

    def _record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            self._open_until = max(self._open_until, self._mono() + self._cooldown)

    @staticmethod
    def _rows(payload: object) -> list[dict]:
        if not isinstance(payload, list) or any(
            not isinstance(x, dict) for x in payload
        ):
            raise BitpinDataError("UNDOCUMENTED_RESPONSE_SCHEMA")
        return payload

    def get_markets(self) -> tuple[MarketObservation, ...]:
        payload, _, received, sha = self._read("/api/v1/mkt/markets/")
        results = []
        seen = set()
        for row in self._rows(payload):
            try:
                symbol, base, quote = _symbol(row["symbol"]), row["base"], row["quote"]
                pp, qp = row["price_precision"], row["base_amount_precision"]
                if (
                    symbol in seen
                    or symbol != f"{base}_{quote}"
                    or type(row["tradable"]) is not bool
                    or type(pp) is not int
                    or type(qp) is not int
                    or not 0 <= pp <= 24
                    or not 0 <= qp <= 24
                ):
                    raise BitpinDataError("INVALID_MARKET_SCHEMA")
                unit = (
                    "TOMAN" if quote == "IRT" else "RIAL" if quote == "IRR" else quote
                )
                results.append(
                    MarketObservation(
                        symbol,
                        base,
                        quote,
                        unit,
                        row["tradable"],
                        pp,
                        qp,
                        received,
                        received,
                        sha,
                    )
                )
                seen.add(symbol)
            except (KeyError, TypeError):
                raise BitpinDataError("INVALID_MARKET_SCHEMA") from None
        return tuple(results)

    def get_tickers(self) -> tuple[TickerObservation, ...]:
        payload, _, received, sha = self._read("/api/v1/mkt/tickers/", per_minute=80)
        results = []
        seen = set()
        for row in self._rows(payload):
            try:
                symbol = _symbol(row["symbol"])
                stamp = _timestamp(row["timestamp"])
                price, low, high = (_number(row[k]) for k in ("price", "low", "high"))
                if symbol in seen or low > high or stamp > received:
                    raise BitpinDataError("INVALID_TICKER_SCHEMA")
                results.append(
                    TickerObservation(
                        symbol, price, low, high, stamp, received, received, sha
                    )
                )
                seen.add(symbol)
            except KeyError:
                raise BitpinDataError("INVALID_TICKER_SCHEMA") from None
        return tuple(results)

    def get_order_book(self, symbol: str) -> BookObservation:
        symbol = _symbol(symbol)
        payload, observed, received, sha = self._read(
            f"/api/v1/mth/orderbook/{symbol}/"
        )
        if not isinstance(payload, dict):
            raise BitpinDataError("UNDOCUMENTED_RESPONSE_SCHEMA")
        try:
            sides = []
            for side in ("bids", "asks"):
                levels = payload[side]
                if not isinstance(levels, list) or not 1 <= len(levels) <= 20:
                    raise BitpinDataError("INVALID_BOOK_SCHEMA")
                if any(
                    not isinstance(level, list) or len(level) != 2 for level in levels
                ):
                    raise BitpinDataError("INVALID_BOOK_SCHEMA")
                sides.append(tuple((_number(p), _number(q)) for p, q in levels))
            bids, asks = sides
            if (
                any(a[0] <= b[0] for a, b in itertools.pairwise(bids))
                or any(a[0] >= b[0] for a, b in itertools.pairwise(asks))
                or bids[0][0] >= asks[0][0]
            ):
                raise BitpinDataError("INVALID_BOOK_ORDERING")
            return BookObservation(
                symbol, bids, asks, observed, received, received, sha
            )
        except KeyError:
            raise BitpinDataError("INVALID_BOOK_SCHEMA") from None

    def get_historical_candles(
        self, symbol: str, start: datetime, end: datetime, resolution: str = "4h"
    ) -> tuple[CandleObservation, ...]:
        aware(start)
        aware(end)
        if resolution not in RESOLUTIONS:
            # 1M is documented but calendar semantics are deliberately not approximated.
            raise UnsupportedCapability(
                "RESOLUTION_NOT_IMPLEMENTED_WITH_FIXED_DURATION"
            )
        duration = RESOLUTIONS[resolution]
        if start >= end or (end - start).total_seconds() / duration >= 10000:
            raise BitpinDataError("INVALID_CANDLE_RANGE")
        symbol = _symbol(symbol)
        payload, _, received, sha = self._read(
            "/api/v1/mkt/candles/",
            {
                "symbol": symbol,
                "from": int(start.timestamp()),
                "to": int(end.timestamp()),
                "res": resolution,
            },
        )
        results = []
        previous = None
        for row in self._rows(payload):
            try:
                opened = _timestamp(row["ts"])
                closed = opened + timedelta(seconds=duration)
                prices = {k: _number(row[k]) for k in ("open", "high", "low", "close")}
                volume = _number(row["volume"], positive=False)
                if (
                    row["resolution"] != resolution
                    or opened < start
                    or opened > end
                    or (previous is not None and opened <= previous)
                    or Decimal(str(row["time"])) != Decimal(str(row["ts"])) * 1000
                    or prices["low"] > min(prices["open"], prices["close"])
                    or prices["high"] < max(prices["open"], prices["close"])
                ):
                    raise BitpinDataError("INVALID_CANDLE_SCHEMA")
                previous = opened
                if closed > received:
                    continue  # The provider may include its still-forming candle.
                results.append(
                    CandleObservation(
                        symbol,
                        opened,
                        closed,
                        open=prices["open"],
                        high=prices["high"],
                        low=prices["low"],
                        close=prices["close"],
                        volume=volume,
                        resolution=resolution,
                        received_at=received,
                        available_at=received,
                        source_hash=sha,
                    )
                )
            except (KeyError, TypeError, ArithmeticError):
                raise BitpinDataError("INVALID_CANDLE_SCHEMA") from None
        return tuple(results)

    def get_market_metadata(self, symbol: str) -> MarketMetadata:
        _symbol(symbol)
        raise UnsupportedCapability(
            "MINIMUM_NOTIONAL_AND_INCREMENT_CONTRACT_UNVERIFIED"
        )

    def get_server_time(self) -> datetime:
        raise UnsupportedCapability("OFFICIAL_SERVER_TIME_ENDPOINT_UNVERIFIED")

    def require_capability(self, capability: str) -> None:
        if capability not in self.capabilities:
            raise UnsupportedCapability(
                "AUTHENTICATED_AND_EXECUTION_PORTS_DISABLED_OR_UNVERIFIED"
            )
