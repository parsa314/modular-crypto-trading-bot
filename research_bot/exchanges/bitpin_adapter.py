"""Notebook v1 schema adapter: public GET and opt-in authenticated wallet GET.

Only POST authenticate is allowed. All trading, cancellation, bulk and address
creation calls fail before invoking the transport. API compatibility is not
claimed without a separate, current-schema read-only contract check.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import math
import re
import time
from typing import Callable, Mapping, Protocol
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..contracts import EvidenceStamp, ResearchStatus
from ..jamalian_data import BookLevel, OrderBook, Ticker, aware, currencies, decimal_value


class BitpinError(RuntimeError):
    pass


class WriteDisabled(BitpinError):
    pass


@dataclass(frozen=True)
class Credentials:
    api_key: str = field(repr=False)
    secret_key: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.api_key.strip() or not self.secret_key.strip():
            raise ValueError("both credentials are required")


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes = field(repr=False)
    headers: Mapping[str, str] = field(default_factory=dict)


class Transport(Protocol):
    def request(self, method: str, url: str, *, headers: Mapping[str, str],
                body: bytes | None, timeout: float) -> HttpResponse: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UrllibTransport:
    def request(self, method, url, *, headers, body, timeout):
        req = Request(url, data=body, headers=dict(headers), method=method)
        opener = build_opener(_NoRedirect())
        try:
            response = opener.open(req, timeout=timeout)
        except HTTPError as error:
            response = error
        with response:
            content = response.read(2_000_001)
            if len(content) > 2_000_000:
                raise BitpinError("response exceeds size limit")
            return HttpResponse(response.code, content, dict(response.headers))


@dataclass(frozen=True)
class WalletBalance:
    asset: str
    free: Decimal
    frozen: Decimal
    stamp: EvidenceStamp


class BitpinAdapter:
    BASE_URL = "https://api.bitpin.ir"
    AUTH = "/api/v1/usr/authenticate/"
    TICKERS = "/api/v1/mkt/tickers/"
    WALLET = "/api/v1/wlt/wallets/"

    def __init__(self, *, transport: Transport | None = None,
                 credentials: Credentials | None = None, auth_read: bool = False,
                 timeout: float = 10, retries: int = 2,
                 clock: Callable[[], datetime] | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        if not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("timeout must be in (0, 30]")
        if type(retries) is not int or not 0 <= retries <= 3:
            raise ValueError("retries must be an integer in [0, 3]")
        self._transport = transport or UrllibTransport()
        self._credentials = credentials
        self._auth_read = auth_read is True
        self._timeout, self._retries = timeout, retries
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep
        self._token: str | None = None
        self._expires: datetime | None = None

    def _request(self, method: str, path: str, *, headers=None, payload=None):
        permitted = (method == "GET" and path in {self.TICKERS, self.WALLET})
        if method == "GET" and path.startswith("/api/v1/mth/orderbook/"):
            symbol = path.removeprefix("/api/v1/mth/orderbook/").removesuffix("/")
            currencies(symbol)
            permitted = path == f"/api/v1/mth/orderbook/{symbol}/"
        if method == "POST" and path == self.AUTH and self._auth_read:
            permitted = True
        if not permitted or (path == self.WALLET and not self._auth_read):
            raise WriteDisabled("endpoint not allowed by read-only policy")
        body = None if payload is None else json.dumps(payload).encode()
        for attempt in range(self._retries + 1):
            try:
                response = self._transport.request(method, self.BASE_URL + path,
                    headers=headers or {}, body=body, timeout=self._timeout)
            except Exception:
                raise BitpinError("transport failed") from None
            if response.status == 429 and method == "GET" and attempt < self._retries:
                raw = next((v for k, v in response.headers.items() if k.lower() == "retry-after"), "1")
                try:
                    delay = float(raw)
                except (ValueError, TypeError):
                    delay = 1
                if not math.isfinite(delay):
                    delay = 1
                self._sleep(min(5, max(0, delay)))
                continue
            if response.status != 200:
                raise BitpinError(f"HTTP {response.status}; no data accepted")
            if len(response.body) > 2_000_000:
                raise BitpinError("response exceeds size limit")
            try:
                data = json.loads(response.body, parse_float=Decimal,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
            except (ValueError, TypeError):
                raise BitpinError("invalid JSON response") from None
            at = self._clock()
            aware(at)
            # This schema does not establish exchange event time. Do not invent it.
            stamp = EvidenceStamp("bitpin:v1:" + path, at, at,
                lineage=sha256(response.body).hexdigest(),
                reliability_tier="RECEIPT_TIME_ONLY", status=ResearchStatus.UNVERIFIED)
            return data, stamp
        raise BitpinError("retry budget exhausted")

    def login(self) -> None:
        self._token = self._expires = None
        if not self._auth_read or self._credentials is None:
            raise BitpinError("authenticated reads require explicit configuration")
        data, stamp = self._request("POST", self.AUTH,
            headers={"Content-Type": "application/json"},
            payload={"api_key": self._credentials.api_key, "secret_key": self._credentials.secret_key})
        if (not isinstance(data, dict) or not isinstance(data.get("access"), str)
                or not re.fullmatch(r"[A-Za-z0-9._~-]+", data["access"])):
            raise BitpinError("authentication failed")
        self._token = data["access"]
        # Conservative 60-second reuse cap; no unverified 15-minute expiry claim.
        self._expires = stamp.available_at + timedelta(seconds=60)

    def get_all_prices(self) -> tuple[Ticker, ...]:
        data, stamp = self._request("GET", self.TICKERS)
        try:
            if not isinstance(data, list) or not data:
                raise ValueError()
            result = tuple(Ticker(row["symbol"], decimal_value(row["price"], positive=True), stamp) for row in data)
            if len({x.symbol for x in result}) != len(result):
                raise ValueError()
            return result
        except (KeyError, TypeError, ValueError):
            raise BitpinError("invalid ticker schema") from None

    def get_orderbook(self, symbol: str) -> OrderBook:
        currencies(symbol)
        data, stamp = self._request("GET", f"/api/v1/mth/orderbook/{symbol}/")
        try:
            if not isinstance(data, dict):
                raise ValueError()
            def levels(side, reverse):
                rows = data[side]
                if not isinstance(rows, list):
                    raise ValueError()
                parsed = []
                for row in rows:
                    if not isinstance(row, list) or len(row) != 2:
                        raise ValueError()
                    parsed.append(BookLevel(decimal_value(row[0], positive=True), decimal_value(row[1], positive=True)))
                return tuple(sorted(parsed, key=lambda x: x.price, reverse=reverse))
            return OrderBook(symbol, levels("bids", True), levels("asks", False), stamp)
        except (KeyError, TypeError, ValueError):
            raise BitpinError("invalid orderbook schema") from None

    def get_my_balance(self) -> tuple[WalletBalance, ...]:
        if not self._auth_read:
            raise BitpinError("authenticated reads are disabled")
        now = self._clock()
        aware(now)
        if self._token is None or self._expires is None or now >= self._expires:
            self.login()
        try:
            data, stamp = self._request("GET", self.WALLET,
                headers={"Authorization": "Bearer " + self._token})
        except BitpinError:
            self._token = self._expires = None
            raise
        try:
            if not isinstance(data, list):
                raise ValueError()
            result = tuple(WalletBalance(row["asset"], decimal_value(row["balance"]),
                decimal_value(row["frozen"]), stamp) for row in data)
            if any(not isinstance(x.asset, str) or not x.asset.isalnum() for x in result):
                raise ValueError()
            if len({x.asset for x in result}) != len(result):
                raise ValueError()
            return result
        except (KeyError, TypeError, ValueError):
            raise BitpinError("invalid wallet schema") from None

    def _write_disabled(self, *args, **kwargs):
        raise WriteDisabled("RESEARCH_ONLY: exchange mutation permanently disabled")

    place_limit_order = _write_disabled
    place_market_order = _write_disabled
    place_stop_limit_order = _write_disabled
    place_bulk_orders = _write_disabled
    cancel_order = _write_disabled
    get_deposit_address = _write_disabled
