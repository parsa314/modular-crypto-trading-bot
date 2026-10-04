import copy
import math

import ccxt
import pandas as pd
import pytest

from research_bot.live_exchange import BelowMinimumOrder, CcxtSpotExchange, Quote


SYMBOL = "BTC/USDT"
ENV = {"BOT_EXCHANGE_API_KEY": "test-key", "BOT_EXCHANGE_API_SECRET": "test-secret",
       "BOT_EXCHANGE_API_PASSWORD": "test-password"}
CLIENT_ID = "mb0123456789abcdef0123456789"


def market(exchange_id="coinex"):
    return {
        "id": "BTC-USDT" if exchange_id == "okx" else "BTCUSDT", "symbol": SYMBOL,
        "base": "BTC", "quote": "USDT", "baseId": "BTC", "quoteId": "USDT",
        "type": "spot", "spot": True, "active": True, "contract": False,
        "swap": False, "future": False, "option": False, "margin": False,
        "linear": None, "inverse": None, "contractSize": None,
        "settle": None, "settleId": None, "expiry": None, "expiryDatetime": None,
        "strike": None, "optionType": None, "taker": .001, "maker": .001,
        "precision": {"amount": .001, "price": .01},
        "limits": {"amount": {"min": .001, "max": 10}, "price": {"min": .01, "max": 1000000},
                   "cost": {"min": 5, "max": 100000}}, "info": {"orderTypes": ["LIMIT", "MARKET"]},
    }


class FakeClient:
    precisionMode = ccxt.TICK_SIZE

    def __init__(self, exchange_id="coinex"):
        self.id = exchange_id
        self.options = {"createOrder": {"marginMode": "isolated"}, "spot/order": {"maxRetriesOnFailure": 4}}
        self.calls = []
        self.markets = {SYMBOL: market(exchange_id)}
        self.book = {"bids": [[100, 1]], "asks": [[101, 1]], "timestamp": 1000}
        self.bars = [[i * 3_600_000, 100, 101, 99, 100, 20] for i in range(4)]
        self.balance = {"free": {"BTC": 0, "USDT": 100}, "total": {"BTC": 0, "USDT": 100}}
        self.response_override = {}
        self.error = None
        self.open_orders = []
        self.closed_orders = []
        self.venue_order = None
        self.has = {"fetchOpenOrders": True}
        self.account_orders = {}

    def load_markets(self):
        self.calls.append("load_markets")
        return self.markets

    def set_sandbox_mode(self, enabled):
        self.calls.append(("sandbox", enabled))

    def fetch_order_book(self, symbol, limit):
        self.calls.append(("book", symbol, limit))
        return self.book

    def fetch_ohlcv(self, symbol, timeframe, limit):
        self.calls.append(("ohlcv", symbol, timeframe, limit))
        return self.bars

    def fetch_balance(self, params):
        self.calls.append(("balance", params))
        return self.balance

    def amount_to_precision(self, symbol, amount):
        return ccxt.decimal_to_precision(amount, ccxt.TRUNCATE, self.markets[symbol]["precision"]["amount"], self.precisionMode)

    def price_to_precision(self, symbol, price):
        return ccxt.decimal_to_precision(price, ccxt.ROUND, self.markets[symbol]["precision"]["price"], self.precisionMode)

    def create_order(self, symbol, order_type, side, amount, price, params):
        self.calls.append(("create", symbol, order_type, side, amount, price, params))
        if self.error:
            raise self.error
        identifier = params.get("client_id", params.get("newClientOrderId", params.get("clOrdId")))
        order = {"id": "venue1", "clientOrderId": identifier, "symbol": symbol, "side": side,
                 "amount": amount, "price": price, "filled": amount, "remaining": 0,
                 "status": "closed", "cost": amount * price, "fees": []}
        order.update(self.response_override)
        self.venue_order = order
        return order

    def fetch_order(self, order_id, symbol):
        self.calls.append(("fetch_order", order_id, symbol))
        return self.venue_order

    def fetch_open_orders(self, symbol):
        self.calls.append(("open_orders", symbol))
        return self.open_orders

    def fetch_closed_orders(self, symbol):
        self.calls.append(("closed_orders", symbol))
        return self.closed_orders

    def _account_query(self, name, params):
        self.calls.append(("account_orders", name, dict(params)))
        key = params.get("ordType", name)
        rows = self.account_orders.get(key, [])
        return rows if self.id == "binance" else {"code": "0", "data": rows}

    def v2PrivateGetSpotPendingOrder(self, params):
        return self._account_query("regular", params)

    def v2PrivateGetSpotPendingStopOrder(self, params):
        return self._account_query("stop", params)

    def privateGetOpenOrders(self, params):
        return self._account_query("regular", params)

    def privateGetTradeOrdersPending(self, params):
        return self._account_query("regular", params)

    def privateGetTradeOrdersAlgoPending(self, params):
        return self._account_query("algo", params)


def adapter(exchange_id="coinex", mode="dry-run", client=None):
    client = FakeClient(exchange_id) if client is None else client
    return CcxtSpotExchange(exchange_id, SYMBOL, mode=mode, environ=ENV, client=client)


def intent(exchange):
    return exchange.prepare_order("buy", .5019, 100.009, 100)


def test_dry_run_never_reads_credentials_and_disables_private_methods():
    class ForbiddenEnvironment:
        def get(self, key):
            raise AssertionError("dry-run accessed a private credential")
    client = FakeClient()
    client.apiKey = "injected-key"
    exchange = CcxtSpotExchange("coinex", SYMBOL, environ=ForbiddenEnvironment(), client=client)
    assert client.apiKey == client.secret == client.password == ""
    exchange.fetch_quote(1100, 100)
    for operation in (exchange.fetch_balances, exchange.fetch_open_orders,
                      exchange.fetch_account_open_orders,
                      lambda: exchange.fetch_order("venue1"), lambda: exchange.find_order(CLIENT_ID),
                      lambda: exchange.submit_order(intent(exchange), CLIENT_ID)):
        with pytest.raises(RuntimeError, match="disabled"):
            operation()
    assert not any(isinstance(call, tuple) and call[0] in {"balance", "create", "fetch_order", "open_orders"} for call in client.calls)


@pytest.mark.parametrize("exchange_id", ["binance", "okx"])
def test_testnet_is_configured_before_first_request(exchange_id):
    exchange = adapter(exchange_id, mode="testnet")
    assert exchange.client.calls[:2] == [("sandbox", True), "load_markets"]
    assert exchange.client.apiKey == ENV["BOT_EXCHANGE_API_KEY"]
    assert exchange.client.options["defaultType"] == "spot"
    assert exchange.client.options["maxRetriesOnFailure"] == 0
    assert "marginMode" not in exchange.client.options["createOrder"]
    assert "maxRetriesOnFailure" not in exchange.client.options["spot/order"]


def test_unsupported_coinex_testnet_never_loads_markets():
    client = FakeClient()
    with pytest.raises(ValueError, match="unsupported"):
        adapter(mode="testnet", client=client)
    assert client.calls == []


@pytest.mark.parametrize("exchange_id,missing", [("coinex", "BOT_EXCHANGE_API_KEY"),
                                                ("binance", "BOT_EXCHANGE_API_SECRET"),
                                                ("okx", "BOT_EXCHANGE_API_PASSWORD")])
def test_private_modes_require_credentials_without_disclosing_values(exchange_id, missing):
    environment = {key: value for key, value in ENV.items() if key != missing}
    with pytest.raises(ValueError, match=missing):
        CcxtSpotExchange(exchange_id, SYMBOL, mode="live", environ=environment, client=FakeClient(exchange_id))


@pytest.mark.parametrize("changes", [{"spot": False}, {"active": None}, {"active": False},
                                    {"type": "swap"}, {"contract": True}, {"leverage": 2},
                                    {"base": "BTC3L", "symbol": SYMBOL}])
def test_non_spot_or_ambiguous_markets_fail_closed(changes):
    client = FakeClient()
    client.markets[SYMBOL].update(changes)
    with pytest.raises(ValueError):
        adapter(client=client)


def test_quote_validation_and_spread():
    quote = adapter().fetch_quote(1100, 100)
    assert quote.mid == 100.5
    assert quote.spread_bps == pytest.approx(1 / 100.5 * 10000)
    assert Quote(100, 100, 1000).spread_bps == 0


@pytest.mark.parametrize("changes", [{"timestamp": None}, {"timestamp": 999}, {"timestamp": 1200},
                                    {"bids": []}, {"asks": [[99, 1]]}, {"bids": [[0, 1]]},
                                    {"asks": [[math.inf, 1]]}, {"bids": [[100, 0]]}])
def test_unfresh_or_invalid_quotes_are_rejected(changes):
    exchange = adapter()
    exchange.client.book.update(changes)
    with pytest.raises(RuntimeError, match="Quote validation failed"):
        exchange.fetch_quote(1100, 100)


def test_binance_missing_server_timestamp_uses_labeled_bounded_receipt_clock(monkeypatch):
    exchange = adapter("binance")
    exchange.client.book["timestamp"] = None
    ticks = iter([1_000_000_000, 1_020_000_000])
    monkeypatch.setattr("research_bot.live_exchange.time.monotonic_ns", lambda: next(ticks))
    quote = exchange.fetch_quote(1100, 100)
    assert quote.timestamp_ms == 1120 and quote.timestamp_source == "local-receipt"
    ticks = iter([1_000_000_000, 1_101_000_000])
    monkeypatch.setattr("research_bot.live_exchange.time.monotonic_ns", lambda: next(ticks))
    with pytest.raises(RuntimeError, match="Quote validation failed"):
        exchange.fetch_quote(1100, 100)


def test_exchange_timestamp_generated_during_request_is_not_mislabeled_future(monkeypatch):
    exchange = adapter()
    exchange.client.book["timestamp"] = 1110
    ticks = iter([1_000_000_000, 1_020_000_000])
    monkeypatch.setattr("research_bot.live_exchange.time.monotonic_ns", lambda: next(ticks))
    quote = exchange.fetch_quote(1100, 100)
    assert quote.timestamp_source == "exchange" and quote.timestamp_ms == 1110


def test_closed_candles_exclude_forming_bar_and_use_bar_open_utc_timestamps():
    exchange = adapter()
    exchange.client.bars[-1][4] = math.nan  # The forming bar must never enter features.
    frame = exchange.fetch_closed_candles("1h", 3, now_ms=3_600_000 * 3 + 1200)
    assert len(frame) == 3
    assert frame.timestamp.iloc[-1] == pd.Timestamp("1970-01-01T02:00:00Z")
    assert str(frame.timestamp.dt.tz) == "UTC"
    assert frame.close.tolist() == [100, 100, 100]
    assert exchange.client.calls[-1][-1] == 4


@pytest.mark.parametrize("problem", ["duplicate", "reverse", "gap", "stale", "future", "nan_close", "too_few"])
def test_bad_candle_history_is_never_silently_repaired(problem):
    exchange = adapter()
    rows = exchange.client.bars
    now = 3_600_000 * 3 + 1200
    if problem == "duplicate":
        rows[1][0] = rows[0][0]
    elif problem == "reverse":
        rows.reverse()
    elif problem == "gap":
        rows[2][0] += 1000
    elif problem == "stale":
        now = 3_600_000 * 5
    elif problem == "future":
        rows[-1][0] += 3_600_000
    elif problem == "nan_close":
        rows[1][4] = math.nan
    else:
        exchange.client.bars = rows[:2]
    with pytest.raises(RuntimeError, match="Closed candle validation failed"):
        exchange.fetch_closed_candles("1h", 3, now_ms=now)


def test_balances_retain_other_assets_and_reject_incomplete_evidence():
    exchange = adapter(mode="live")
    exchange.client.balance["total"]["BNB"] = .2
    balances = exchange.fetch_balances()
    assert balances == {"base_free": 0, "base_total": 0, "quote_free": 100,
                        "quote_total": 100, "other_total": {"BNB": .2}}
    exchange.client.balance["free"]["BTC"] = 1
    with pytest.raises(RuntimeError, match="Balance validation failed"):
        exchange.fetch_balances()
    exchange.client.balance["free"]["BTC"] = 0
    exchange.client.balance["total"]["USDT"] = None
    with pytest.raises(RuntimeError):
        exchange.fetch_balances()


def test_precision_rounding_preserves_bounded_prices_and_quantity():
    exchange = adapter()
    buy = exchange.prepare_order("buy", .5019, 100.009, 100)
    sell = exchange.prepare_order("sell", .5019, 100.001, 100)
    assert buy["amount"] == sell["amount"] == .501
    assert buy["price"] == 100.0
    assert sell["price"] == 100.01
    assert "clientOrderId" not in buy


@pytest.mark.parametrize("args", [("buy", .001, 100, 100), ("buy", 11, 100, 2000),
                                 ("buy", 1, 100, 99), ("buy", .0001, 100, 100),
                                 ("buy", 1, math.inf, 100), ("sell", -1, 100, 100)])
def test_market_limits_and_maximum_notional_cannot_be_bypassed(args):
    with pytest.raises((ValueError, RuntimeError)):
        adapter().prepare_order(*args)


def test_only_genuine_minimum_constraints_use_the_noop_exception():
    exchange = adapter()
    with pytest.raises(BelowMinimumOrder):
        exchange.prepare_order("buy", .0001, 100, 100)
    with pytest.raises(BelowMinimumOrder):
        exchange.prepare_order("buy", .01, 100, 100)
    with pytest.raises(RuntimeError) as captured:
        exchange.prepare_order("buy", 1, 100, 99)
    assert not isinstance(captured.value, BelowMinimumOrder)
    exchange.market["precision"]["price"] = None
    with pytest.raises(RuntimeError) as captured:
        exchange.prepare_order("buy", 1, 100, 100)
    assert not isinstance(captured.value, BelowMinimumOrder)


@pytest.mark.parametrize("exchange_id,id_field", [("coinex", "client_id"), ("binance", "newClientOrderId"), ("okx", "clOrdId")])
def test_submission_has_only_spot_limit_ioc_native_identity_params(exchange_id, id_field):
    exchange = adapter(exchange_id, mode="live")
    result = exchange.submit_order(intent(exchange), CLIENT_ID)
    call = exchange.client.calls[-1]
    assert call[2] == "limit"
    assert call[-1]["timeInForce"] == "IOC"
    assert call[-1][id_field] == CLIENT_ID
    if exchange_id == "okx":
        assert call[-1]["tdMode"] == "cash"
    assert result["status"] == "FILLED" and result["cost"] == pytest.approx(50.1)


@pytest.mark.parametrize("identifier", ["", "contains-hyphen", "A" * 33, "فارسی"])
def test_invalid_client_ids_never_reach_create_order(identifier):
    exchange = adapter(mode="live")
    with pytest.raises(ValueError):
        exchange.submit_order(intent(exchange), identifier)
    assert not any(isinstance(call, tuple) and call[0] == "create" for call in exchange.client.calls)


@pytest.mark.parametrize("override", [{"clientOrderId": "another"}, {"symbol": "ETH/USDT"},
                                     {"side": "sell"}, {"amount": 2}, {"status": None},
                                     {"filled": None}, {"remaining": None}, {"price": 1000},
                                     {"cost": 1000}])
def test_incomplete_or_mismatched_acknowledgements_remain_ambiguous(override):
    exchange = adapter(mode="live")
    exchange.client.response_override = override
    with pytest.raises(RuntimeError, match="Ambiguous order submission"):
        exchange.submit_order(intent(exchange), CLIENT_ID)
    assert sum(isinstance(call, tuple) and call[0] == "create" for call in exchange.client.calls) == 1


def test_timeout_is_redacted_and_never_retried():
    exchange = adapter(mode="live")
    exchange.client.error = ccxt.RequestTimeout("url?apiKey=SECRET_ACCOUNT_TOKEN signed=PRIVATE")
    with pytest.raises(RuntimeError) as captured:
        exchange.submit_order(intent(exchange), CLIENT_ID)
    assert "RequestTimeout" in str(captured.value)
    assert "SECRET_ACCOUNT_TOKEN" not in str(captured.value)
    assert "PRIVATE" not in str(captured.value)
    assert captured.value.__suppress_context__
    assert sum(isinstance(call, tuple) and call[0] == "create" for call in exchange.client.calls) == 1


def test_partial_cancelled_recovery_retains_reported_cost_and_fees():
    exchange = adapter(mode="live")
    exchange.client.response_override = {"status": "canceled", "filled": .2, "remaining": .301,
                                         "cost": 19.9, "fee": {"currency": "USDT", "cost": .02}}
    result = exchange.submit_order(intent(exchange), CLIENT_ID)
    assert result["status"] == "CANCELLED"
    assert result["filled"] == .2 and result["cost"] == 19.9
    assert result["fees"] == [{"currency": "USDT", "cost": .02}]
    exchange.client.closed_orders = [exchange.client.venue_order]
    assert exchange.find_order(CLIENT_ID) == result
    assert exchange.find_order(CLIENT_ID, "venue1") == result
    assert exchange.find_order("mbNotFound") is None
    with pytest.raises(RuntimeError, match="identity mismatch"):
        exchange.find_order("mbAnother", "venue1")


def test_missing_cost_is_not_fabricated_from_the_limit_price():
    exchange = adapter(mode="live")
    exchange.client.response_override = {"cost": None}
    assert exchange.submit_order(intent(exchange), CLIENT_ID)["cost"] is None


def test_missing_fees_are_not_reported_as_known_zero():
    exchange = adapter(mode="live")
    assert exchange.submit_order(intent(exchange), CLIENT_ID)["fees"] is None


def test_coinex_raw_base_fee_is_preserved_in_addition_to_quote_fee():
    exchange = adapter(mode="live")
    exchange.client.response_override = {"fee": {"currency": "USDT", "cost": 0},
                                         "info": {"base_fee": ".001", "quote_fee": "0", "discount_fee": "0"}}
    order = exchange.submit_order(intent(exchange), CLIENT_ID)
    assert order["fees"] == [{"currency": "BTC", "cost": .001}, {"currency": "USDT", "cost": 0}]
    exchange.client.response_override["info"]["discount_fee"] = ".1"
    with pytest.raises(RuntimeError, match="Ambiguous"):
        exchange.submit_order(intent(exchange), "mbAnother")


def test_open_orders_are_normalized_and_duplicate_client_ids_block_recovery():
    exchange = adapter(mode="live")
    exchange.client.response_override = {"status": "open", "filled": .2, "remaining": .301, "cost": 20}
    result = exchange.submit_order(intent(exchange), CLIENT_ID)
    assert result["status"] == "PARTIAL"
    exchange.client.open_orders = [exchange.client.venue_order]
    assert exchange.fetch_open_orders() == [result]
    exchange.client.closed_orders = [{**exchange.client.venue_order, "id": "venue2"}]
    with pytest.raises(RuntimeError, match="unresolved"):
        exchange.find_order(CLIENT_ID)


@pytest.mark.parametrize("exchange_id", ["coinex", "binance", "okx"])
def test_account_wide_checks_detect_other_market_pending_exposure(exchange_id):
    exchange = adapter(exchange_id, mode="live")
    other_order = {"id": "external", "symbol": "ETH/USDT"}
    exchange.client.account_orders["regular"] = [other_order]
    assert exchange.fetch_account_open_orders() == [other_order]
    call = exchange.client.calls[-1]
    assert call[0] == "account_orders"
    assert not any(key in call[-1] for key in ("symbol", "market", "instId"))
    if exchange_id == "coinex":
        assert call[-1]["market_type"] == "SPOT"
    elif exchange_id == "okx":
        assert call[-1]["instType"] == "SPOT"
    else:
        assert call[-1] == {}


@pytest.mark.parametrize("exchange_id,category", [("coinex", "stop"), ("okx", "conditional"),
                                                 ("okx", "oco"), ("okx", "trigger"),
                                                 ("okx", "move_order_stop"), ("okx", "iceberg"), ("okx", "twap")])
def test_account_pending_conditionals_cannot_hide_behind_empty_regular_orders(exchange_id, category):
    exchange = adapter(exchange_id, mode="live")
    exchange.client.account_orders[category] = [{"external_pending_exposure": True}]
    assert exchange.fetch_account_open_orders()


@pytest.mark.parametrize("exchange_id,count", [("coinex", 2), ("binance", 1), ("okx", 7)])
def test_only_all_successful_empty_account_queries_establish_no_pending_orders(exchange_id, count):
    exchange = adapter(exchange_id, mode="live")
    assert exchange.fetch_account_open_orders() == []
    assert sum(isinstance(call, tuple) and call[0] == "account_orders" for call in exchange.client.calls) == count
    exchange.client.has["fetchOpenOrders"] = "emulated"
    with pytest.raises(RuntimeError, match="Account-wide"):
        exchange.fetch_account_open_orders()


def test_missing_conditional_endpoint_or_malformed_raw_container_never_means_empty():
    exchange = adapter(mode="live")
    exchange.client.v2PrivateGetSpotPendingStopOrder = None
    with pytest.raises(RuntimeError, match="Account-wide"):
        exchange.fetch_account_open_orders()
    exchange.client.v2PrivateGetSpotPendingOrder = lambda params: {"code": 0}
    with pytest.raises(RuntimeError, match="Account-wide"):
        exchange.fetch_account_open_orders()


@pytest.mark.parametrize("exchange_id", ["coinex", "binance", "okx"])
def test_pinned_ccxt_account_scope_native_routes_are_spot_only_without_symbol_filters(exchange_id):
    client = getattr(ccxt, exchange_id)({"enableRateLimit": False})
    client.set_markets([market(exchange_id)])
    client.load_markets = lambda: client.markets
    captured = []

    def request(path, api="public", method="GET", params=None, *args, **kwargs):
        captured.append((path, api, method, dict(params)))
        return [] if exchange_id == "binance" else {"code": "0", "data": []}

    client.request = request
    exchange = adapter(exchange_id, mode="live", client=client)
    assert exchange.fetch_account_open_orders() == []
    assert all(row[2] == "GET" for row in captured)
    assert all(not any(key in row[3] for key in ("symbol", "market", "instId")) for row in captured)
    if exchange_id == "coinex":
        assert {row[0] for row in captured} == {"spot/pending-order", "spot/pending-stop-order"}
        assert all(row[3] == {"market_type": "SPOT"} for row in captured)
    elif exchange_id == "binance":
        assert captured == [("openOrders", "private", "GET", {})]
    else:
        assert captured[0][0] == "trade/orders-pending"
        assert all(row[3]["instType"] == "SPOT" for row in captured)
        assert {row[3].get("ordType") for row in captured[1:]} == {"conditional", "oco", "trigger", "move_order_stop", "iceberg", "twap"}


@pytest.mark.parametrize("exchange_id", ["coinex", "binance", "okx"])
def test_pinned_ccxt_translates_native_ioc_requests_without_network(exchange_id):
    assert ccxt.__version__ == "4.5.77", "request mapping must be tested against the repository's exact CCXT pin"
    client = getattr(ccxt, exchange_id)({"enableRateLimit": False})
    client.set_markets([market(exchange_id)])
    client.load_markets = lambda: client.markets
    captured = []

    def request(path, api="public", method="GET", params=None, *args, **kwargs):
        captured.append((path, api, method, copy.deepcopy(params)))
        raise ccxt.RequestTimeout("intercepted; no network")

    client.request = request
    exchange = adapter(exchange_id, mode="live", client=client)
    with pytest.raises(RuntimeError, match="Ambiguous order submission"):
        exchange.submit_order(intent(exchange), CLIENT_ID)
    assert len(captured) == 1
    path, _, method, params = captured[0]
    assert method == "POST"
    if exchange_id == "coinex":
        assert path == "spot/order"
        assert params["type"] == "ioc" and params["market_type"] == "SPOT"
        assert params["client_id"] == CLIENT_ID
        assert "clientOrderId" not in params
    elif exchange_id == "binance":
        assert path == "order"
        assert params["type"] == "LIMIT" and params["timeInForce"] == "IOC"
        assert params["newClientOrderId"] == CLIENT_ID
    else:
        assert path == "trade/batch-orders"
        assert params[0]["ordType"] == "ioc" and params[0]["tdMode"] == "cash"
        assert params[0]["clOrdId"] == CLIENT_ID


@pytest.mark.parametrize("exchange_id,path", [("coinex", "spot/order"), ("binance", "order"), ("okx", "trade/batch-orders")])
def test_pinned_ccxt_transport_timeout_has_exactly_one_attempt(exchange_id, path):
    client = getattr(ccxt, exchange_id)({"enableRateLimit": False})
    client.set_markets([market(exchange_id)])
    client.load_markets = lambda: client.markets
    attempts = []

    def fetch(*args, **kwargs):
        attempts.append(True)
        raise ccxt.RequestTimeout("intercepted transport")

    client.fetch = fetch
    exchange = adapter(exchange_id, mode="live", client=client)
    # An injected path-specific retry override must also be neutralized.
    client.options[path] = {"maxRetriesOnFailure": 2}
    with pytest.raises(RuntimeError, match="Ambiguous order submission"):
        exchange.submit_order(intent(exchange), CLIENT_ID)
    assert len(attempts) == 1


def parsed_order_fixture(exchange_id, extra=None):
    client = getattr(ccxt, exchange_id)({"enableRateLimit": False})
    client.set_markets([market(exchange_id)])
    client.load_markets = lambda: client.markets
    if exchange_id == "coinex":
        raw = {"order_id": "venue1", "client_id": CLIENT_ID, "market": "BTCUSDT", "market_type": "SPOT",
               "side": "buy", "type": "limit", "amount": "10", "filled_amount": "10", "unfilled_amount": "0",
               "price": "100", "status": "done"}
    elif exchange_id == "binance":
        raw = {"orderId": "venue1", "clientOrderId": CLIENT_ID, "symbol": "BTCUSDT", "side": "BUY",
               "type": "LIMIT", "origQty": "10", "executedQty": "10", "price": "100", "status": "FILLED"}
    else:
        raw = {"ordId": "venue1", "clOrdId": CLIENT_ID, "instId": "BTC-USDT", "instType": "SPOT",
               "tdMode": "cash", "side": "buy", "ordType": "limit", "sz": "10", "accFillSz": "10",
               "px": "100", "state": "filled"}
    raw.update(extra or {})
    return adapter(exchange_id, mode="live", client=client), client.parse_order(raw, client.markets[SYMBOL])


@pytest.mark.parametrize("exchange_id", ["coinex", "binance", "okx"])
def test_ccxt_synthetic_limit_cost_is_discarded_when_actual_execution_value_is_missing(exchange_id):
    exchange, parsed = parsed_order_fixture(exchange_id)
    assert parsed["cost"] == 1000  # Evidence of CCXT's filled * limit substitution.
    assert exchange._normalize_order(parsed)["cost"] is None


@pytest.mark.parametrize("exchange_id,evidence", [
    ("coinex", {"filled_value": "990"}),
    ("binance", {"cummulativeQuoteQty": "990"}),
    ("binance", {"cumulativeQuoteQty": "990"}),
    ("okx", {"avgPx": "99"}),
])
def test_actual_reported_cost_or_execution_average_survives_ccxt_normalization(exchange_id, evidence):
    exchange, parsed = parsed_order_fixture(exchange_id, evidence)
    assert exchange._normalize_order(parsed)["cost"] == 990


@pytest.mark.parametrize("fills,expected", [
    ([{"qty": "10", "price": "99"}], 990),
    ([{"qty": "2", "price": "99"}], None),
    ([{"qty": "10"}], None),
])
def test_binance_fill_cost_requires_complete_actual_fill_records(fills, expected):
    exchange, parsed = parsed_order_fixture("binance", {"fills": fills})
    assert exchange._normalize_order(parsed)["cost"] == expected
