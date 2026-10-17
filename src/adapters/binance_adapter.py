"""Binance **Spot Testnet only** REST adapter; public reads need no API key.

No CCXT global sandbox state, production URL, or credentials in constructor
arguments. Private request credentials come solely from testnet-named env vars.
"""
from __future__ import annotations

from decimal import Decimal
import hashlib
import hmac
import json
import math
import os
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request

import pandas as pd

from .base import AdapterError, ExecutionRejected, MarketAdapter, OrderReceipt, floor_step, positive, side, validate_bars


class BinanceAdapter(MarketAdapter):
    mode = "binance_spot_testnet"
    BASE_URL = "https://testnet.binance.vision"
    _TIMEFRAMES = {"M15": "15m", "H1": "1h", "H4": "4h", "D1": "1d",
                   "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}

    def __init__(self, *, enable_orders: bool = False, base_url: str = BASE_URL,
                 max_order_notional: float = 100.0, timeout: float = 15.0) -> None:
        if base_url.rstrip("/") != self.BASE_URL:
            raise ExecutionRejected("Only https://testnet.binance.vision is allowed")
        self._base_url = self.BASE_URL
        self.enable_orders = bool(enable_orders)
        self.max_order_notional = positive(max_order_notional, "max_order_notional")
        self.timeout = positive(timeout, "timeout")

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None,
                 *, private: bool = False) -> Any:
        # Revalidate on every request so a mutated instance cannot redirect keys.
        if self._base_url != self.BASE_URL or not path.startswith("/api/v3/"):
            raise ExecutionRejected("Testnet endpoint validation failed")
        values = dict(params or {})
        headers = {"User-Agent": "modular-crypto-ai-paper/1.0"}
        if private:
            key, secret = os.environ.get("BINANCE_TESTNET_API_KEY"), os.environ.get("BINANCE_TESTNET_API_SECRET")
            if not key or not secret:
                raise ExecutionRejected("Set BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_API_SECRET for testnet orders")
            values.update(timestamp=int(time.time() * 1000), recvWindow=5000)
            query = urlencode(values)
            values["signature"] = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
            headers["X-MBX-APIKEY"] = key
        query = urlencode(values)
        url = self._base_url + path
        body = None
        if method in {"POST", "DELETE"}:
            body = query.encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif query:
            url += "?" + query
        request = Request(url, data=body, headers=headers, method=method)
        # urllib normally follows redirects and can forward API-key headers.
        # Restrict redirects even for public reads to keep the venue explicit.
        from urllib.request import HTTPRedirectHandler, build_opener

        class RejectRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, hdrs, newurl):
                raise AdapterError("Binance endpoint redirect rejected")

        try:
            with build_opener(RejectRedirect()).open(request, timeout=self.timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            # Provider exception strings/URLs can contain signed query values.
            raise AdapterError(f"Binance testnet HTTP {exc.code}; no simulation substituted") from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise AdapterError("Binance testnet connection/response failed; no simulation substituted") from None

    @staticmethod
    def _symbol(symbol: str) -> str:
        normalized = symbol.upper().replace("/", "")
        if not normalized.isalnum() or not normalized.endswith("USDT"):
            raise ExecutionRejected("Only USDT-quoted spot symbols are supported")
        return normalized

    def get_data(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        if not 1 <= int(count) <= 20000:
            raise ValueError("count must be between 1 and 20000 closed bars")
        if timeframe not in self._TIMEFRAMES:
            raise ValueError("Supported timeframes: M15, H1, H4, D1")
        normalized, requested = self._symbol(symbol), int(count)
        now_ms = int(time.time() * 1000)
        end_time, closed = None, []
        while len(closed) < requested:
            limit = min(1000, requested - len(closed) + 1)
            params = {"symbol": normalized, "interval": self._TIMEFRAMES[timeframe], "limit": limit}
            if end_time is not None:
                params["endTime"] = end_time
            rows = self._request("GET", "/api/v3/klines", params)
            if not isinstance(rows, list) or len(rows) > limit:
                raise AdapterError("Binance testnet returned an invalid history page")
            if not rows:
                break
            if any(not isinstance(row, list) or len(row) < 7 for row in rows):
                raise AdapterError("Binance testnet returned invalid kline records")
            opens = [int(row[0]) for row in rows]
            if opens != sorted(set(opens)) or (end_time is not None and opens[-1] > end_time):
                raise AdapterError("Binance testnet returned unordered/overlapping history pages")
            if any(int(row[0]) < 0 or int(row[0]) > now_ms or int(row[6]) <= int(row[0]) for row in rows):
                raise AdapterError("Binance testnet returned invalid candle times")
            closed.extend(row for row in rows if int(row[6]) < now_ms)
            if len(rows) < limit or opens[0] <= 0:
                break  # Testnet history/reset boundary; do not fabricate older bars.
            end_time = opens[0] - 1  # Inclusive API bound must precede this page.
        closed = sorted(closed, key=lambda row: int(row[0]))[-requested:]
        frame = pd.DataFrame([row[1:6] for row in closed], columns=["open", "high", "low", "close", "volume"],
                             index=pd.to_datetime([row[0] for row in closed], unit="ms", utc=True))
        result = validate_bars(frame)
        result.attrs.update(venue=self.mode, requested_count=requested, returned_count=len(result),
                            complete_requested_history=len(result) == requested)
        return result

    def get_price(self, symbol: str) -> float:
        result = self._request("GET", "/api/v3/ticker/price", {"symbol": self._symbol(symbol)})
        return positive(result["price"], "price")

    def _rules(self, symbol: str) -> dict[str, Any]:
        info = self._request("GET", "/api/v3/exchangeInfo", {"symbol": symbol})
        markets = info.get("symbols", [])
        if len(markets) != 1 or markets[0].get("status") != "TRADING" or not markets[0].get("isSpotTradingAllowed", False):
            raise ExecutionRejected("Symbol is not an active spot market")
        market = markets[0]
        market["filters_by_type"] = {item["filterType"]: item for item in market["filters"]}
        return market

    @staticmethod
    def _market_lot(filters: dict[str, Any]) -> dict[str, Any]:
        lot = filters.get("MARKET_LOT_SIZE", {})
        return lot if float(lot.get("stepSize", 0)) > 0 else filters["LOT_SIZE"]

    def normalize_rebalance(self, symbol: str, signed_delta: float) -> float:
        """Quantize a requested change toward zero; routine untradeable dust skips.

        This does not alter account holdings or claim a small residual is flat.
        Actual order submission retains balance/notional-limit safety checks.
        """
        delta = float(signed_delta)
        if not math.isfinite(delta):
            raise ExecutionRejected("signed_delta must be finite")
        if delta == 0:
            return 0.0
        symbol = self._symbol(symbol)
        filters = self._rules(symbol)["filters_by_type"]
        lot = self._market_lot(filters)
        quantity = floor_step(abs(delta), float(lot["stepSize"]))
        if quantity <= 0 or quantity < float(lot["minQty"]):
            return 0.0
        minimum = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))
        if quantity * self.get_price(symbol) < float(minimum.get("minNotional", 0)):
            return 0.0
        return quantity if delta > 0 else -quantity

    def _balances(self) -> dict[str, dict[str, float]]:
        data = self._request("GET", "/api/v3/account", private=True)
        if not data.get("canTrade"):
            raise ExecutionRejected("Testnet account trading permission unavailable")
        balances = {}
        for row in data["balances"]:
            free, locked = float(row["free"]), float(row.get("locked", 0))
            if not math.isfinite(free) or not math.isfinite(locked) or free < 0 or locked < 0:
                raise AdapterError("Binance testnet returned invalid inventory")
            balances[row["asset"]] = {"free": free, "locked": locked, "total": free + locked}
        return balances

    def _inventory_marks(self, balances: dict[str, dict[str, float]]) -> dict[str, float]:
        assets = {asset for asset, balance in balances.items() if asset != "USDT" and balance["total"] > 0}
        if not assets:
            return {}
        quotes = self._request("GET", "/api/v3/ticker/price")
        if not isinstance(quotes, list):
            raise AdapterError("Binance testnet inventory marks unavailable")
        prices = {row["symbol"]: row["price"] for row in quotes}
        marks = {}
        for asset in sorted(assets):
            if asset + "USDT" not in prices:
                raise AdapterError("A nonzero testnet holding has no USDT mark; equity is unavailable")
            marks[asset] = positive(prices[asset + "USDT"], "inventory mark")
        return marks

    def get_balance(self) -> dict[str, Any]:
        """Actual spot free cash and marked total inventory, including locked funds."""
        balances = self._balances()
        marks = self._inventory_marks(balances)
        cash = balances.get("USDT", {"free": 0.0, "total": 0.0})
        equity = cash["total"] + sum(balances[asset]["total"] * mark for asset, mark in marks.items())
        return {"cash": cash["free"], "equity": equity, "currency": "USDT", "balances": balances}

    def get_positions(self, symbols: list[str] | tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        """Reconcile actual base-asset holdings; historical entry price is unknown."""
        balances = self._balances()
        selected = None if symbols is None else {self._symbol(symbol) for symbol in symbols}
        held = {asset: balance for asset, balance in balances.items()
                if asset != "USDT" and balance["total"] > 0 and (selected is None or asset + "USDT" in selected)}
        marks = self._inventory_marks(held)
        return [{"symbol": asset + "USDT", "size": balance["total"], "free_size": balance["free"],
                 "locked_size": balance["locked"], "direction": "buy", "mark_price": marks[asset],
                 "notional_account_currency": balance["total"] * marks[asset],
                 "entry_price": None, "mode": self.mode} for asset, balance in held.items()]

    def place_order(self, symbol: str, direction: str, size: float, *,
                    stop_loss: float | None = None, take_profit: float | None = None,
                    **kwargs: Any) -> OrderReceipt:
        if not self.enable_orders:
            raise ExecutionRejected("Binance testnet orders are disabled")
        if stop_loss is not None or take_profit is not None:
            raise ExecutionRejected("Spot bracket stops are not implemented; no unprotected substitute order sent")
        if kwargs:
            raise ExecutionRejected("Unsupported Binance order options")
        symbol, direction = self._symbol(symbol), side(direction)
        market = self._rules(symbol)
        filters = market["filters_by_type"]
        lot = self._market_lot(filters)
        quantity = floor_step(positive(size, "size"), float(lot["stepSize"]))
        if quantity < float(lot["minQty"]) or quantity > float(lot["maxQty"]):
            raise ExecutionRejected("Order size is outside testnet market lot limits")
        price = self.get_price(symbol)
        notional = quantity * price
        if notional > self.max_order_notional:
            raise ExecutionRejected("Order exceeds configured testnet notional limit")
        minimum = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))
        if notional < float(minimum.get("minNotional", 0)):
            raise ExecutionRejected("Order is below testnet minimum notional")
        balances = self._balances()
        asset = market["quoteAsset"] if direction == "buy" else market["baseAsset"]
        needed = notional * 1.002 if direction == "buy" else quantity
        if balances.get(asset, {"free": 0})["free"] < needed:
            raise ExecutionRejected("Insufficient testnet balance; spot shorting is unsupported")
        result = self._request("POST", "/api/v3/order", {
            "symbol": symbol, "side": direction.upper(), "type": "MARKET",
            "quantity": format(Decimal(str(quantity)), "f"), "newOrderRespType": "FULL",
        }, private=True)
        executed = float(result.get("executedQty", 0))
        quote = float(result.get("cummulativeQuoteQty", 0))
        # NEW/PARTIALLY_FILLED are returned as such; no invented fill/mark price.
        return OrderReceipt(str(result["orderId"]), symbol, direction, executed,
                            quote / executed if executed else 0.0,
                            str(result["status"]), self.mode)
