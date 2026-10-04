"""Explicit spot exchange boundary; importing this module places no orders.

Dry-run clients contain no private credentials and reject private methods.
Order creation is attempted exactly once. Any incomplete acknowledgement stays
ambiguous and must be reconciled by its durable client ID before another order.
This adapter does not change the research service's execution firewall.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext
import math
from numbers import Integral
import os
import re
import time
from typing import Mapping

import ccxt
import pandas as pd

from .ensemble_features import validate_ohlcv


class BelowMinimumOrder(ValueError):
    """Expected no-op: a positive requested trade is below an exchange minimum."""


def _number(value, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite real number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} must be a finite real number") from None
    if not math.isfinite(result) or (result <= 0 if positive else result < 0):
        raise ValueError(f"{name} is outside its finite nonnegative domain")
    return result


def _milliseconds(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer in milliseconds")
    return int(value)


def _failure(operation: str, error: Exception) -> RuntimeError:
    # No server error text, response body, signed URL, or credentials in errors.
    kind = re.sub(r"[^A-Za-z0-9_]", "", type(error).__name__)
    return RuntimeError(f"{operation} ({kind})")


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    timestamp_ms: int
    timestamp_source: str = "exchange"

    def __post_init__(self):
        bid = _number(self.bid, "bid", positive=True)
        ask = _number(self.ask, "ask", positive=True)
        _milliseconds(self.timestamp_ms, "quote timestamp")
        if ask < bid:
            raise ValueError("crossed order book")
        if self.timestamp_source not in {"exchange", "local-receipt"}:
            raise ValueError("unknown quote timestamp source")
        object.__setattr__(self, "bid", bid)
        object.__setattr__(self, "ask", ask)

    @property
    def mid(self) -> float:
        return self.bid / 2 + self.ask / 2

    @property
    def spread_bps(self) -> float:
        return (self.ask - self.bid) / self.mid * 10_000


class CcxtSpotExchange:
    """CoinEx, Binance or OKX spot-only adapter with explicit mode selection."""

    def __init__(self, exchange_id: str, symbol: str, *, mode="dry-run", environ=None, client=None):
        if exchange_id not in {"coinex", "binance", "okx"}:
            raise ValueError("unsupported spot exchange")
        if mode not in {"dry-run", "testnet", "live"}:
            raise ValueError("mode must be dry-run, testnet or live")
        if not isinstance(symbol, str) or ":" in symbol or symbol.count("/") != 1:
            raise ValueError("a unified unleveraged spot symbol is required")
        self.exchange_id, self.symbol, self.mode = exchange_id, symbol, mode
        credentials = {}
        if mode != "dry-run":
            environment = os.environ if environ is None else environ
            names = {"apiKey": "BOT_EXCHANGE_API_KEY", "secret": "BOT_EXCHANGE_API_SECRET"}
            if exchange_id == "okx":
                names["password"] = "BOT_EXCHANGE_API_PASSWORD"
            for key, variable in names.items():
                value = environment.get(variable)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"required credential variable {variable} is missing")
                credentials[key] = value
        options = {"defaultType": "spot", "defaultMarginMode": None,
                   "marginMode": None,
                   "maxRetriesOnFailure": 0, "maxRetriesOnFailureDelay": 0}
        self.client = client if client is not None else getattr(ccxt, exchange_id)({
            "enableRateLimit": True, "timeout": 15_000, "options": options, **credentials,
        })
        if getattr(self.client, "id", exchange_id) != exchange_id:
            raise ValueError("client exchange identity mismatch")
        self.client.options = dict(getattr(self.client, "options", {}) or {})
        self.client.verbose = False
        self.client.options.update(options)
        self._restrict_options()
        # Setting, rather than reading, prevents injected dry-run clients from
        # using credentials while loading currencies/markets.
        for key in ("apiKey", "secret", "password"):
            setattr(self.client, key, credentials.get(key, ""))
        if mode == "testnet":
            if exchange_id == "coinex":
                raise ValueError("CoinEx spot sandbox is unsupported")
            try:
                self.client.set_sandbox_mode(True)
            except Exception as error:
                raise _failure("Sandbox initialization failed", error) from None
        try:
            markets = self.client.load_markets()
            market = markets[symbol]
        except Exception as error:
            raise _failure("Spot market initialization failed", error) from None
        if not isinstance(market, dict):
            raise ValueError("invalid spot market metadata")
        if market.get("symbol") != symbol or market.get("spot") is not True or market.get("type") != "spot":
            raise ValueError("market must be the requested spot market")
        if market.get("active") is not True:
            raise ValueError("spot market must be explicitly active")
        if any(market.get(name) for name in ("contract", "swap", "future", "option", "leveraged")):
            raise ValueError("derivative or leveraged markets are forbidden")
        if market.get("leverage") is not None and _number(market["leverage"], "market leverage") > 1:
            raise ValueError("leveraged markets are forbidden")
        base, quote = market.get("base"), market.get("quote")
        if not isinstance(base, str) or not isinstance(quote, str) or f"{base}/{quote}" != symbol:
            raise ValueError("invalid base/quote market metadata")
        if re.search(r"(?:[2-9][LS]|[1-9][0-9]+[LS]|UP|DOWN|BULL|BEAR)$", base):
            raise ValueError("leveraged token symbols are forbidden")
        self.market = dict(market)
        self.base, self.quote = base, quote

    def _restrict_options(self):
        self.client.verbose = False
        self.client.options.update({"defaultType": "spot", "marginMode": None, "defaultMarginMode": None,
                                    "maxRetriesOnFailure": 0, "maxRetriesOnFailureDelay": 0})
        # CCXT permits path-specific retry and margin overrides. They must not
        # bypass the global rule, including on an injected client.
        for key, value in list(self.client.options.items()):
            if isinstance(value, dict):
                self.client.options[key] = {name: item for name, item in value.items()
                                            if name not in {"maxRetriesOnFailure", "maxRetriesOnFailureDelay",
                                                            "marginMode", "defaultMarginMode"}}

    def _private(self):
        if self.mode == "dry-run":
            raise RuntimeError("Private exchange operations are disabled in dry-run mode")

    def fetch_quote(self, now_ms: int, max_age_ms: int) -> Quote:
        now = _milliseconds(now_ms, "now_ms")
        age = _milliseconds(max_age_ms, "max_age_ms")
        try:
            started = time.monotonic_ns()
            book = self.client.fetch_order_book(self.symbol, limit=5)
            duration_ns = time.monotonic_ns() - started
            if duration_ns < 0 or duration_ns > age * 1_000_000:
                raise ValueError("order-book request exceeded the freshness budget")
            received_ms = now + duration_ns // 1_000_000
            if not isinstance(book, dict):
                raise ValueError("invalid order book")
            for side in ("bids", "asks"):
                if not isinstance(book.get(side), list) or not book[side] or len(book[side][0]) < 2:
                    raise ValueError("missing top of book")
                _number(book[side][0][1], "top-of-book amount", positive=True)
            timestamp, source = book.get("timestamp"), "exchange"
            if timestamp is None and self.exchange_id == "binance":
                # Binance spot REST depth does not carry an exchange timestamp.
                # Receipt freshness is explicitly weaker than server freshness.
                timestamp, source = received_ms, "local-receipt"
            quote = Quote(book["bids"][0][0], book["asks"][0][0], timestamp, source)
            if quote.timestamp_ms > received_ms or received_ms - quote.timestamp_ms > age:
                raise ValueError("stale or future order book")
            return quote
        except Exception as error:
            raise _failure("Quote validation failed", error) from None

    def fetch_closed_candles(self, timeframe: str, limit: int, now_ms: int) -> pd.DataFrame:
        """Return raw closed OHLCV with UTC bar-open timestamps; never repair gaps.

        A maximum of 299 bars keeps the one-request path within OKX's 300-bar
        page limit while leaving room for its currently forming candle.
        """
        now = _milliseconds(now_ms, "now_ms")
        if isinstance(limit, bool) or not isinstance(limit, Integral) or not 2 <= limit <= 299:
            raise ValueError("closed candle limit must be an integer in [2, 299]")
        if not isinstance(timeframe, str) or not re.fullmatch(r"[1-9][0-9]*[mhdw]", timeframe):
            raise ValueError("a fixed-duration minute/hour/day/week timeframe is required")
        duration = int(ccxt.Exchange.parse_timeframe(timeframe) * 1000)
        try:
            rows = self.client.fetch_ohlcv(self.symbol, timeframe=timeframe, limit=int(limit) + 1)
            if not isinstance(rows, list) or not rows or any(not isinstance(row, (list, tuple)) or len(row) != 6 for row in rows):
                raise ValueError("invalid OHLCV response")
            stamps = [_milliseconds(row[0], "bar timestamp") for row in rows]
            if any(right - left != duration for left, right in zip(stamps, stamps[1:])):
                raise ValueError("unsorted, duplicate or missing OHLCV bars")
            if stamps[-1] > now:
                raise ValueError("future bar timestamp")
            closed = [row for row in rows if row[0] + duration <= now]
            if len(closed) < limit:
                raise ValueError("insufficient closed candles")
            if now - (closed[-1][0] + duration) >= duration:
                raise ValueError("latest closed candle is stale")
            result = pd.DataFrame(closed[-int(limit):], columns=["timestamp", "open", "high", "low", "close", "volume"])
            result["timestamp"] = pd.to_datetime(result["timestamp"], unit="ms", utc=True)
            return validate_ohlcv(result)
        except Exception as error:
            raise _failure("Closed candle validation failed", error) from None

    def fetch_balances(self) -> dict:
        self._private()
        try:
            balances = self.client.fetch_balance({"type": "spot"})
            if not isinstance(balances, dict):
                raise ValueError("invalid balance response")
            free, total = balances.get("free"), balances.get("total")
            if not isinstance(free, dict) or not isinstance(total, dict):
                raise ValueError("incomplete normalized balance response")
            result = {}
            for currency, label in ((self.base, "base"), (self.quote, "quote")):
                if (currency in free) != (currency in total):
                    raise ValueError("incomplete currency balance")
                available = _number(free.get(currency, 0), "free balance")
                combined = _number(total.get(currency, 0), "total balance")
                if available > combined:
                    raise ValueError("free balance exceeds total")
                result[f"{label}_free"], result[f"{label}_total"] = available, combined
            result["other_total"] = {}
            for currency, value in total.items():
                combined = _number(value, "other total balance")
                if currency not in {self.base, self.quote} and combined > 0:
                    result["other_total"][currency] = combined
            return result
        except Exception as error:
            raise _failure("Balance validation failed", error) from None

    def prepare_order(self, side: str, quantity: float, limit_price: float, max_order_quote: float) -> dict:
        """Round quantity down and price inward to preserve the caller's bound."""
        if side not in {"buy", "sell"}:
            raise ValueError("order side must be buy or sell")
        requested = _number(quantity, "quantity", positive=True)
        price = _number(limit_price, "limit price", positive=True)
        maximum = _number(max_order_quote, "maximum order quote", positive=True)
        try:
            amount_precision = self.market["precision"]["amount"]
            if self.client.precisionMode == ccxt.TICK_SIZE:
                amount_quantum = Decimal(str(amount_precision))
            elif self.client.precisionMode == ccxt.DECIMAL_PLACES:
                amount_quantum = Decimal(10) ** -int(amount_precision)
            else:
                amount_quantum = None
            if amount_quantum is not None and amount_quantum.is_finite() and amount_quantum > 0 and Decimal(str(requested)) < amount_quantum:
                raise BelowMinimumOrder("quantity is below exchange precision minimum")
            amount_string = self.client.amount_to_precision(self.symbol, requested)
            amount = _number(amount_string, "rounded quantity")
            if amount == 0:
                raise BelowMinimumOrder("quantity rounds below exchange precision minimum")
            if Decimal(str(amount_string)) > Decimal(str(requested)):
                raise ValueError("amount rounding increased quantity")
            precision = self.market["precision"]["price"]
            mode = self.client.precisionMode
            with localcontext() as context:
                context.prec = 50
                desired = Decimal(str(price))
                if mode == ccxt.TICK_SIZE:
                    tick = Decimal(str(precision))
                elif mode == ccxt.DECIMAL_PLACES:
                    tick = Decimal(10) ** -int(precision)
                elif mode == ccxt.SIGNIFICANT_DIGITS:
                    tick = Decimal(10) ** (desired.adjusted() - int(precision) + 1)
                else:
                    raise ValueError("unsupported exchange precision mode")
                if not tick.is_finite() or tick <= 0:
                    raise ValueError("invalid price tick")
                rounding = ROUND_FLOOR if side == "buy" else ROUND_CEILING
                rounded = (desired / tick).to_integral_value(rounding=rounding) * tick
                price = _number(rounded, "rounded limit price", positive=True)
                transmitted = self.client.price_to_precision(self.symbol, price)
                if Decimal(str(transmitted)) != rounded:
                    raise ValueError("CCXT price precision changed the bounded limit")
                notional = Decimal(str(amount_string)) * rounded
                if notional > Decimal(str(maximum)):
                    raise ValueError("order exceeds maximum quote notional")
                limits = self.market.get("limits", {})
                for category, value in (("amount", Decimal(str(amount_string))), ("price", rounded), ("cost", notional)):
                    bounds = limits.get(category, {}) or {}
                    minimum, ceiling = bounds.get("min"), bounds.get("max")
                    if minimum is not None and value < Decimal(str(_number(minimum, "market minimum"))):
                        raise BelowMinimumOrder("order is below exchange minimum")
                    if ceiling is not None and value > Decimal(str(_number(ceiling, "market maximum"))):
                        raise ValueError("order exceeds exchange maximum")
        except BelowMinimumOrder:
            raise
        except Exception as error:
            raise _failure("Order preparation failed", error) from None
        return {"symbol": self.symbol, "side": side, "amount": amount, "price": price,
                "max_order_quote": maximum}

    @staticmethod
    def _client_id(value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9]{1,32}", value):
            raise ValueError("client order ID must contain 1 to 32 ASCII alphanumeric characters")
        return value

    def _reported_cost(self, information: dict, filled: float) -> float | None:
        """Verify cost provenance; CCXT safe_order may invent filled * limit."""
        cost_keys = {"coinex": ("filled_value",),
                     "binance": ("cummulativeQuoteQty", "cumulativeQuoteQty", "cumQuote"),
                     "okx": ()}[self.exchange_id]
        costs = [_number(information[key], "reported fill value") for key in cost_keys
                 if information.get(key) not in (None, "")]
        if costs:
            if any(not math.isclose(value, costs[0], rel_tol=1e-8) for value in costs[1:]):
                raise ValueError("conflicting reported quote costs")
            return costs[0]
        average_keys = {"coinex": ("avg_entry_price",),
                        "binance": ("avgPrice", "avgFilledPrice"),
                        "okx": ("avgPx",)}[self.exchange_id]
        for key in average_keys:
            if information.get(key) not in (None, "", "0", 0):
                average = _number(information[key], "reported execution average", positive=True)
                return _number(filled * average, "actual filled quote cost")
        if self.exchange_id == "binance":
            records = information.get("fills", information.get("trades"))
            if isinstance(records, list) and records:
                amounts, costs = [], []
                for record in records:
                    if not isinstance(record, dict):
                        raise ValueError("invalid actual fill record")
                    quantity = record.get("qty", record.get("q", record.get("quantity")))
                    price = record.get("price", record.get("p"))
                    if quantity is None or price is None:
                        return None
                    amount = _number(quantity, "actual fill quantity", positive=True)
                    actual_price = _number(price, "actual fill price", positive=True)
                    amounts.append(amount)
                    costs.append(_number(amount * actual_price, "actual fill cost"))
                if math.isclose(math.fsum(amounts), filled, rel_tol=1e-8, abs_tol=math.ulp(filled) * 8):
                    return _number(math.fsum(costs), "complete actual fill value")
        return None

    def _normalize_order(self, raw, *, client_id=None, intent=None) -> dict:
        if not isinstance(raw, dict):
            raise ValueError("invalid order acknowledgement")
        order_id, returned_id = raw.get("id"), raw.get("clientOrderId")
        if not isinstance(order_id, str) or not order_id or not isinstance(returned_id, str) or not returned_id:
            raise ValueError("missing order identity")
        if client_id is not None and returned_id != client_id:
            raise ValueError("order client identity mismatch")
        if raw.get("symbol") != self.symbol or raw.get("side") not in {"buy", "sell"}:
            raise ValueError("order market identity mismatch")
        amount = _number(raw.get("amount"), "order amount", positive=True)
        filled = _number(raw.get("filled"), "filled quantity")
        remaining = _number(raw.get("remaining"), "remaining quantity")
        tolerance = max(math.ulp(amount) * 8, amount * 1e-8)
        if filled > amount + tolerance or not math.isclose(filled + remaining, amount, abs_tol=tolerance, rel_tol=1e-8):
            raise ValueError("inconsistent fill quantities")
        status = raw.get("status")
        if status == "open":
            normalized = "PARTIAL" if filled > 0 else "OPEN"
            if remaining <= tolerance:
                raise ValueError("open order without remaining quantity")
        elif status == "closed":
            if remaining > tolerance or not math.isclose(filled, amount, abs_tol=tolerance, rel_tol=1e-8):
                raise ValueError("closed order missing complete fill evidence")
            normalized = "FILLED"
        elif status in {"canceled", "cancelled", "expired"}:
            normalized = "CANCELLED"
        elif status == "rejected":
            if filled != 0:
                raise ValueError("rejected order contains fills")
            normalized = "REJECTED"
        else:
            raise ValueError("unknown order status")
        information = raw.get("info")
        if filled > 0 and isinstance(information, dict):
            cost = self._reported_cost(information, filled)
        else:
            cost = None if raw.get("cost") is None else _number(raw["cost"], "filled quote cost")
        if cost is not None and ((filled == 0 and cost != 0) or (filled > 0 and cost <= 0)):
            raise ValueError("cost disagrees with fill evidence")
        if intent is not None:
            if raw["side"] != intent["side"] or not math.isclose(amount, intent["amount"], abs_tol=tolerance, rel_tol=1e-8):
                raise ValueError("order intent mismatch")
            if raw.get("price") is not None and not math.isclose(_number(raw["price"], "order price", positive=True), intent["price"], rel_tol=1e-8):
                raise ValueError("order limit mismatch")
            if cost is not None and filled > 0:
                limit_notional = filled * intent["price"]
                if ((intent["side"] == "buy" and cost > limit_notional * (1 + 1e-8))
                        or (intent["side"] == "sell" and cost < limit_notional * (1 - 1e-8))):
                    raise ValueError("fill cost violates the bounded limit")
        raw_fees = raw.get("fees")
        if self.exchange_id == "coinex" and isinstance(information, dict) and "base_fee" in information:
            # CCXT 4.5.77 only exposes CoinEx's quote_fee in its unified fee.
            # The API's separate base/quote fields state their currency exactly.
            discount = _number(information.get("discount_fee", 0), "discount fee")
            if discount > 0:
                raise ValueError("discount fee currency requires separate verified evidence")
            raw_fees = [{"currency": self.base, "cost": information["base_fee"]},
                        {"currency": self.quote, "cost": information.get("quote_fee")}]
        elif not raw_fees and raw.get("fee") is not None:
            raw_fees = [raw["fee"]]
        if raw_fees is not None and not isinstance(raw_fees, list):
            raise ValueError("invalid fee evidence")
        if raw_fees and any(isinstance(fee, dict) and fee.get("cost") is None for fee in raw_fees):
            raw_fees = None  # CCXT may emit a placeholder fee with no cost.
        # Missing/empty unified fees do not prove zero fees. Preserve that gap
        # for the controller's explicitly bounded account reconciliation.
        fees = [] if raw_fees else None
        for fee in raw_fees or []:
            if not isinstance(fee, dict) or not isinstance(fee.get("currency"), str) or not fee["currency"]:
                raise ValueError("incomplete fee evidence")
            fees.append({"currency": fee["currency"], "cost": _number(fee.get("cost"), "fee cost")})
        return {"id": order_id, "clientOrderId": returned_id, "symbol": self.symbol,
                "side": raw["side"], "amount": amount, "filled": filled, "remaining": remaining,
                "status": normalized, "cost": cost, "fees": fees}

    def submit_order(self, intent: Mapping, client_order_id: str) -> dict:
        self._private()
        identifier = self._client_id(client_order_id)
        if not isinstance(intent, Mapping) or intent.get("symbol") != self.symbol:
            raise ValueError("invalid order intent")
        prepared = self.prepare_order(intent.get("side"), intent.get("amount"), intent.get("price"), intent.get("max_order_quote"))
        if any(prepared[key] != intent.get(key) for key in prepared):
            raise ValueError("intent must retain exchange-precision preparation")
        if self.exchange_id == "coinex":
            params = {"timeInForce": "IOC", "client_id": identifier}
        elif self.exchange_id == "binance":
            params = {"timeInForce": "IOC", "newClientOrderId": identifier}
        else:
            params = {"timeInForce": "IOC", "clOrdId": identifier, "tdMode": "cash"}
        try:
            # Never retry this operation or forward arbitrary caller params.
            self._restrict_options()
            raw = self.client.create_order(self.symbol, "limit", prepared["side"], prepared["amount"], prepared["price"], params)
            return self._normalize_order(raw, client_id=identifier, intent=prepared)
        except Exception as error:
            raise _failure("Ambiguous order submission; reconciliation required", error) from None

    def fetch_order(self, order_id: str) -> dict:
        self._private()
        if not isinstance(order_id, str) or not order_id:
            raise ValueError("exchange order ID is required")
        try:
            result = self._normalize_order(self.client.fetch_order(order_id, self.symbol))
            if result["id"] != order_id:
                raise ValueError("exchange order identity mismatch")
            return result
        except Exception as error:
            raise _failure("Order recovery failed", error) from None

    def fetch_open_orders(self) -> list[dict]:
        self._private()
        try:
            rows = self.client.fetch_open_orders(self.symbol)
            if not isinstance(rows, list):
                raise ValueError("invalid open-order response")
            return [self._normalize_order(row) for row in rows]
        except Exception as error:
            raise _failure("Open-order recovery failed", error) from None

    def fetch_account_open_orders(self) -> list[dict]:
        """Detect any pending spot exposure across the dedicated account.

        CoinEx stops and every pinned OKX algo category require separate read
        endpoints. Binance spot openOrders includes its stop/take-profit orders.
        Raw response containers are checked before parsing: CCXT's convenient
        default of [] for a missing ``data`` field cannot establish emptiness.
        Only an empty result from every required endpoint permits a new intent.
        No symbol filter or timestamp filter is applied.
        """
        self._private()
        try:
            if getattr(self.client, "has", {}).get("fetchOpenOrders") is not True:
                raise ccxt.NotSupported("account open orders are unavailable")
            self._restrict_options()
            if self.exchange_id == "coinex":
                queries = [("v2PrivateGetSpotPendingOrder", {"market_type": "SPOT"}),
                           ("v2PrivateGetSpotPendingStopOrder", {"market_type": "SPOT"})]
            elif self.exchange_id == "binance":
                # This native endpoint is spot-only; omitting symbol deliberately
                # incurs its higher account-wide request weight.
                queries = [("privateGetOpenOrders", {})]
            else:
                queries = [("privateGetTradeOrdersPending", {"instType": "SPOT"})]
                for category in ("conditional", "oco", "trigger", "move_order_stop", "iceberg", "twap"):
                    queries.append(("privateGetTradeOrdersAlgoPending", {"instType": "SPOT", "ordType": category}))
            for name, params in queries:
                method = getattr(self.client, name, None)
                if not callable(method):
                    raise ccxt.NotSupported("required account order endpoint is unavailable")
                response = method(params)
                if self.exchange_id == "binance":
                    rows = response
                else:
                    if not isinstance(response, dict) or str(response.get("code")) != "0":
                        raise ValueError("incomplete account order response")
                    rows = response.get("data")
                    if rows == [] and isinstance(response.get("pagination"), dict) and response["pagination"].get("has_next") is True:
                        raise ValueError("empty account order page with unresolved pagination")
                if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                    raise ValueError("invalid account order container")
                if rows:
                    # Presence alone blocks trading. Do not normalize another
                    # market using this adapter's configured symbol or limits.
                    return rows
            return []
        except Exception as error:
            raise _failure("Account-wide open-order check failed", error) from None

    def find_order(self, client_id: str, known_venue_id: str | None = None) -> dict | None:
        """Recover an exact identity, or leave an unfound intent unresolved.

        Absence from the venue's limited history is not proof of rejection and
        must never authorize resubmission. A caller must keep that intent blocked.
        """
        self._private()
        identifier = self._client_id(client_id)
        if known_venue_id is not None:
            result = self.fetch_order(known_venue_id)
            if result["clientOrderId"] != identifier:
                raise RuntimeError("Recovered order client identity mismatch")
            return result
        try:
            matches = []
            for method in (self.client.fetch_open_orders, self.client.fetch_closed_orders):
                rows = method(self.symbol)
                if not isinstance(rows, list):
                    raise ValueError("invalid recovery response")
                for raw in rows:
                    if isinstance(raw, dict) and raw.get("clientOrderId") == identifier:
                        matches.append(self._normalize_order(raw, client_id=identifier))
            unique = {order["id"]: order for order in matches}
            if len(unique) > 1:
                raise ValueError("duplicate client order identity")
            return next(iter(unique.values()), None)
        except Exception as error:
            raise _failure("Client-order recovery unresolved", error) from None
