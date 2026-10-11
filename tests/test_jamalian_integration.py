"""Offline behavioral checks; these fixtures are not economic evidence."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from dataclasses import replace
import json
import random
import unittest

from research_bot.contracts import Decision, EvidenceStamp
from research_bot.jamalian_data import Candle, BookLevel, OrderBook
from research_bot.strategies.jamalian_hypotheses import (
    RsiMaHypothesis, MacdRsiEmaHypothesis, GridHypothesis,
    TriangularArbitrageHypothesis, InventoryState, rolling_rsi, wilder_rsi,
)
from research_bot.event_driven_simulator import EventDrivenExecutionSimulator, SimOrder, OrderState
from research_bot.exchanges.bitpin_adapter import BitpinAdapter, HttpResponse, Credentials, WriteDisabled, BitpinError
from research_bot.jamalian_integration import JamalianResearchLedger, inventory_from_simulator
from research_bot.orchestrator import ResearchTradingOrchestrator
from research_bot.risk import RiskSnapshot
from scripts.run_jamalian_research import run


T = datetime(2026, 10, 11, tzinfo=timezone.utc)


def book(at=T, bid="99", ask="100", qty="1"):
    return OrderBook("BTC_USDT", (BookLevel(D(bid), D(qty)),),
                     (BookLevel(D(ask), D(qty)),), EvidenceStamp("fixture", at, at))


def candles(prices, interval=timedelta(hours=1)):
    return [Candle("BTC_USDT", D(str(price)), EvidenceStamp("fixture", T + interval * i, T + interval * i)) for i, price in enumerate(prices)]


class FixtureTransport:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected transport call")
        return self.responses.pop(0)


def response(data, status=200, headers=None):
    return HttpResponse(status, json.dumps(data).encode(), headers or {})


class JamalianAcceptanceTests(unittest.TestCase):
    def test_no_fill_on_decision_snapshot(self):
        sim = EventDrivenExecutionSimulator({"USDT": D("200")})
        sim.submit(SimOrder("buy", "BTC_USDT", "BUY", D("1"), D("100"), T))
        sim.on_book(book())
        self.assertEqual(sim.order("buy").state, OrderState.CREATED)
        self.assertEqual(sim.balances().get("BTC", D("0")), D("0"))

    def test_sell_before_buy_fill_rejected(self):
        sim = EventDrivenExecutionSimulator({"USDT": D("200")})
        sim.submit(SimOrder("buy", "BTC_USDT", "BUY", D("1"), D("100"), T))
        sim.submit(SimOrder("sell", "BTC_USDT", "SELL", D("1"), D("110"), T))
        self.assertEqual(sim.order("sell").state, OrderState.REJECTED)

    def test_flat_rsi_has_no_actionable_forecast(self):
        candles = [Candle("BTC_USDT", D("100"), EvidenceStamp("fixture", T + timedelta(hours=i), T + timedelta(hours=i))) for i in range(30)]
        signals = RsiMaHypothesis().generate(candles, T + timedelta(hours=29))
        self.assertEqual(signals[0].metadata["rsi"], "50")
        self.assertEqual(signals[0].decision, Decision.NO_TRADE)
        self.assertEqual(signals[0].confidence, 0)

    def test_exchange_writes_block_before_transport(self):
        transport = FixtureTransport([])
        adapter = BitpinAdapter(transport=transport)
        for name in ("place_limit_order", "place_market_order", "place_stop_limit_order",
                     "place_bulk_orders", "cancel_order", "get_deposit_address"):
            with self.subTest(method=name), self.assertRaises(WriteDisabled):
                getattr(adapter, name)("anything")
        self.assertEqual(transport.calls, [])

    def test_endpoint_allowlist_blocks_mutations_and_injection(self):
        transport = FixtureTransport([])
        adapter = BitpinAdapter(transport=transport)
        for method, path in [("POST", "/api/v1/odr/orders/"), ("DELETE", "/api/v1/odr/orders/1/"),
                             ("GET", "https://other-host/"), ("GET", "/api/v1/mth/orderbook/BTC_USDT/../orders/")]:
            with self.subTest(path=path), self.assertRaises((WriteDisabled, ValueError)):
                adapter._request(method, path)
        self.assertEqual(transport.calls, [])

    def test_public_prices_need_no_auth_and_preserve_decimal(self):
        transport = FixtureTransport([response([{"symbol": "BTC_USDT", "price": "123.123456789123456789"}])])
        tickers = BitpinAdapter(transport=transport, clock=lambda: T).get_all_prices()
        self.assertEqual(tickers[0].price, D("123.123456789123456789"))
        self.assertEqual(tickers[0].stamp.reliability_tier, "RECEIPT_TIME_ONLY")
        self.assertEqual(len(tickers[0].stamp.lineage), 64)
        self.assertNotIn("Authorization", transport.calls[0][2]["headers"])

    def test_book_parser_sorts_and_rejects_crossing_or_schema_change(self):
        transport = FixtureTransport([response({"bids": [["98", "1"], ["99", "2"]], "asks": [["102", "1"], ["100", "3"]]})])
        parsed = BitpinAdapter(transport=transport, clock=lambda: T).get_orderbook("BTC_USDT")
        self.assertEqual(parsed.bids[0].price, D("99"))
        self.assertEqual(parsed.asks[0].price, D("100"))
        for data in ({"bids": [["101", "1"]], "asks": [["100", "1"]]},
                     {"bids": [], "asks": []}, {"new_schema": []}):
            with self.subTest(data=data), self.assertRaises(BitpinError):
                BitpinAdapter(transport=FixtureTransport([response(data)])).get_orderbook("BTC_USDT")

    def test_rate_limit_budget_is_bounded_and_timeout_explicit(self):
        sleeps = []
        transport = FixtureTransport([response({}, 429, {"Retry-After": "9999"})] * 3)
        with self.assertRaises(BitpinError):
            BitpinAdapter(transport=transport, retries=2, sleep=sleeps.append).get_all_prices()
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(sleeps, [5, 5])
        self.assertTrue(all(call[2]["timeout"] == 10 for call in transport.calls))

    def test_auth_is_opt_in_and_failure_never_reads_wallet(self):
        transport = FixtureTransport([])
        with self.assertRaises(BitpinError):
            BitpinAdapter(transport=transport).get_my_balance()
        self.assertFalse(transport.calls)
        transport = FixtureTransport([response({}, 401)])
        adapter = BitpinAdapter(transport=transport, credentials=Credentials("fixture_key", "fixture_secret"), auth_read=True)
        with self.assertRaises(BitpinError):
            adapter.get_my_balance()
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.calls[0][0], "POST")
        self.assertNotIn("fixture_secret", repr(adapter._credentials))

    def test_authenticated_read_token_reuse_and_expiry(self):
        now = [T]
        wallet = [{"asset": "USDT", "balance": "2.01", "frozen": "1"}]
        transport = FixtureTransport([response({"access": "fixture-token"}), response(wallet), response(wallet),
            response({"access": "next-fixture-token"}), response(wallet)])
        adapter = BitpinAdapter(transport=transport, credentials=Credentials("fixture_key", "fixture_secret"),
            auth_read=True, clock=lambda: now[0])
        self.assertEqual(adapter.get_my_balance()[0].free, D("2.01"))
        adapter.get_my_balance()
        now[0] += timedelta(seconds=60)
        adapter.get_my_balance()
        self.assertEqual([x[0] for x in transport.calls], ["POST", "GET", "GET", "POST", "GET"])

    def test_precision_and_bad_inputs_fail_closed(self):
        for value in [float("nan"), D("NaN"), D("Infinity"), D("-1"), True, 0.1]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                Candle("BTC_USDT", value, EvidenceStamp("fixture", T, T))
        with self.assertRaises(ValueError):
            SimOrder("x", "BTC_USDT", "BUY", D("0.015"), D("100"), T, quantity_step=D("0.01"))

    def test_partial_fill_and_quote_fee_accounting(self):
        sim = EventDrivenExecutionSimulator({"USDT": D("200")}, fee_rate=D("0.01"))
        sim.submit(SimOrder("buy", "BTC_USDT", "BUY", D("1"), D("100"), T))
        sim.on_book(book(T + timedelta(seconds=1), qty="0.4"))
        self.assertEqual(sim.order("buy").state, OrderState.PARTIALLY_FILLED)
        self.assertEqual(sim.balances(), {"USDT": D("159.6"), "BTC": D("0.4")})
        self.assertEqual(sim.available_balance("USDT"), D("99.0"))
        sim.on_book(book(T + timedelta(seconds=2), qty="0.6"))
        self.assertEqual(sim.order("buy").state, OrderState.FILLED)
        self.assertEqual(sim.balances(), {"USDT": D("99.0"), "BTC": D("1.0")})
        self.assertEqual(sim.order("buy").reserved, D("0"))

    def test_shared_depth_cannot_be_double_spent(self):
        sim = EventDrivenExecutionSimulator({"USDT": D("1000")}, fee_rate=D("0"))
        for identity in ["a", "b"]:
            sim.submit(SimOrder(identity, "BTC_USDT", "BUY", D("1"), D("100"), T))
        snapshot = book(T + timedelta(seconds=1), qty="1.5")
        sim.on_book(snapshot)
        self.assertEqual(sim.balances()["BTC"], D("1.5"))
        self.assertEqual(sim.order("b").filled_quantity, D("0.5"))
        self.assertEqual(sim.on_book(snapshot), ())
        self.assertEqual(sim.balances()["BTC"], D("1.5"))
        with self.assertRaises(ValueError):
            sim.on_book(replace(snapshot, asks=(BookLevel(D("100"), D("10")),)))

    def test_order_idempotency_and_conflicting_retry(self):
        sim = EventDrivenExecutionSimulator({"USDT": D("200")})
        request = SimOrder("x", "BTC_USDT", "BUY", D("1"), D("100"), T)
        first = sim.submit(request)
        self.assertEqual(sim.submit(request), first)
        self.assertEqual(len(sim.events), 1)
        with self.assertRaises(ValueError):
            sim.submit(replace(request, quantity=D("2")))

    def test_pending_cancel_preserves_reservation_and_may_race_fill(self):
        sim = EventDrivenExecutionSimulator({"USDT": D("200")}, fee_rate=D("0"), cancel_latency=timedelta(seconds=5))
        sim.submit(SimOrder("x", "BTC_USDT", "BUY", D("1"), D("100"), T))
        sim.cancel("x", T + timedelta(seconds=1))
        self.assertEqual(sim.available_balance("USDT"), D("100"))
        sim.on_book(book(T + timedelta(seconds=2), qty="0.5"))
        self.assertEqual(sim.order("x").state, OrderState.CANCEL_PENDING)
        sim.advance(T + timedelta(seconds=6))
        self.assertEqual(sim.order("x").state, OrderState.CANCELLED)
        self.assertEqual(sim.balances()["BTC"], D("0.5"))
        self.assertEqual(sim.available_balance("USDT"), D("150"))

    def test_cancel_ack_precedes_same_timestamp_fill(self):
        sim = EventDrivenExecutionSimulator({"USDT": D("200")}, cancel_latency=timedelta(seconds=1))
        sim.submit(SimOrder("x", "BTC_USDT", "BUY", D("1"), D("100"), T))
        sim.cancel("x", T + timedelta(seconds=1))
        sim.on_book(book(T + timedelta(seconds=2)))
        self.assertEqual(sim.order("x").state, OrderState.CANCELLED)
        self.assertNotIn("BTC", sim.balances())

    def test_expiry_releases_funds_in_chronological_event_order(self):
        sim = EventDrivenExecutionSimulator({"USDT": D("1000")})
        for identity, seconds in [("late", 10), ("early", 5)]:
            sim.submit(SimOrder(identity, "BTC_USDT", "BUY", D("1"), D("100"), T,
                expires_at=T + timedelta(seconds=seconds)))
        sim.advance(T + timedelta(seconds=20))
        self.assertTrue(all(o.state == OrderState.EXPIRED for o in [sim.order("late"), sim.order("early")]))
        self.assertEqual(sim.available_balance("USDT"), D("1000"))
        timestamps = [event.timestamp for event in sim.events]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_latency_and_price_protection_prevent_optimistic_fills(self):
        sim = EventDrivenExecutionSimulator({"USDT": D("200")}, latency=timedelta(seconds=3), slippage_rate=D("0.01"))
        sim.submit(SimOrder("x", "BTC_USDT", "BUY", D("1"), D("100"), T, order_type="MARKET"))
        sim.on_book(book(T + timedelta(seconds=1)))
        sim.on_book(book(T + timedelta(seconds=3)))
        self.assertEqual(sim.order("x").filled_quantity, D("0"))
        sim.on_book(book(T + timedelta(seconds=4), bid="97", ask="98"))
        self.assertEqual(sim.order("x").state, OrderState.FILLED)
        self.assertLess(sim.balances()["USDT"], D("101.02"))

    def test_stop_limit_requires_trigger_and_limit_cross(self):
        sim = EventDrivenExecutionSimulator({"USDT": D("500")})
        sim.submit(SimOrder("stop", "BTC_USDT", "BUY", D("1"), D("105"), T,
            order_type="STOP_LIMIT", stop_price=D("103")))
        sim.on_book(book(T + timedelta(seconds=1), bid="100", ask="101"))
        self.assertFalse(sim.order("stop").triggered)
        sim.on_book(book(T + timedelta(seconds=2), bid="106", ask="107"))
        self.assertTrue(sim.order("stop").triggered)
        self.assertEqual(sim.order("stop").filled_quantity, D("0"))
        sim.on_book(book(T + timedelta(seconds=3), bid="103", ask="104"))
        self.assertEqual(sim.order("stop").state, OrderState.FILLED)

    def test_property_inventory_never_negative_with_random_partial_fills(self):
        rng = random.Random(711)
        sim = EventDrivenExecutionSimulator({"USDT": D("1000")}, fee_rate=D("0.001"))
        for i in range(10):
            at = T + timedelta(seconds=2 * i)
            sim.submit(SimOrder(str(i), "BTC_USDT", "BUY", D("2"), D("100"), at))
            sim.on_book(book(at + timedelta(seconds=1), qty=str(rng.randint(1, 10) / 10)))
            for asset in sim.balances():
                self.assertGreaterEqual(sim.available_balance(asset), 0)
                self.assertGreaterEqual(sim.balances()[asset], 0)

    def test_sell_proceeds_include_fees_and_cannot_sell_reserved_base(self):
        sim = EventDrivenExecutionSimulator({"BTC": D("1")}, fee_rate=D("0.01"))
        sim.submit(SimOrder("sell", "BTC_USDT", "SELL", D("1"), D("99"), T))
        sim.submit(SimOrder("over", "BTC_USDT", "SELL", D("1"), D("99"), T))
        self.assertEqual(sim.order("over").state, OrderState.REJECTED)
        sim.on_book(book(T + timedelta(seconds=1)))
        self.assertEqual(sim.balances(), {"BTC": D("0"), "USDT": D("98.01")})

    def test_rsi_reference_cases_and_incomplete_warmup(self):
        self.assertEqual(rolling_rsi([D("1"), D("2"), D("3")], 2), D("100"))
        self.assertEqual(wilder_rsi([D("3"), D("2"), D("1")], 2), D("0"))
        self.assertEqual(rolling_rsi([D("1"), D("2"), D("1")], 2), D("50"))
        self.assertEqual(RsiMaHypothesis().generate(candles([100] * 10), T + timedelta(hours=9)), [])

    def test_future_bars_do_not_change_earlier_signal(self):
        history = candles([100] * 25)
        at = T + timedelta(hours=24)
        generator = RsiMaHypothesis()
        previous = generator.generate(history, at)
        self.assertEqual(generator.generate(history + candles([100] * 25 + [10000])[25:], at), previous)

    def test_history_gaps_duplicates_naive_times_and_stale_data_rejected(self):
        history = candles([100] * 25)
        for invalid in [history[:-2] + history[-1:], history + history[-1:]]:
            with self.assertRaises(ValueError):
                RsiMaHypothesis().generate(invalid, T + timedelta(hours=24))
        with self.assertRaises(ValueError):
            RsiMaHypothesis().generate(history, T + timedelta(days=7))
        with self.assertRaises(ValueError):
            RsiMaHypothesis().generate(history, datetime(2026, 10, 11))

    def test_macd_long_and_short_candidates_are_non_actionable(self):
        generator = MacdRsiEmaHypothesis(fast=2, slow=3, signal=2, ema_period=3, rsi_period=2, bar_interval=timedelta(hours=1))
        for prices, direction in [([100] * 6 + [101], "LONG"), ([100] * 6 + [99], "SHORT")]:
            signal = generator.generate(candles(prices), T + timedelta(hours=6))[0]
            self.assertEqual(signal.metadata["candidate_direction"], direction)
            self.assertEqual(signal.decision, Decision.NO_TRADE)
            self.assertEqual(signal.expected_return, 0)
            self.assertFalse(signal.metadata["eligible_for_v51"])

    def test_grid_inventory_budget_and_breakout_guard(self):
        grid = GridHypothesis(lower=D("90"), upper=D("110"), intervals=4, quantity=D("1"), fee_rate=D("0"))
        inventory = InventoryState(D("1"), D("90"), EvidenceStamp("inventory", T, T), "BTC_USDT")
        signals = grid.generate(book(bid="99", ask="101"), inventory, T)
        candidates = [s for s in signals if s.metadata["candidate"]]
        self.assertEqual(len(candidates), 2)
        self.assertEqual({s.metadata["candidate_direction"] for s in candidates}, {"LONG", "SELL_INVENTORY"})
        suspended = grid.generate(book(bid="89", ask="90"), inventory, T)
        self.assertEqual(suspended[0].metadata["grid_state"], "SUSPENDED_BREAKOUT")
        self.assertFalse(suspended[0].metadata["candidate"])

    def test_grid_rejects_invalid_ticks_or_future_inventory(self):
        with self.assertRaises(ValueError):
            GridHypothesis(lower=D("90"), upper=D("100"), intervals=3, quantity=D("1"))
        grid = GridHypothesis(lower=D("90"), upper=D("110"), intervals=4, quantity=D("1"))
        inventory = InventoryState(D("1"), D("100"), EvidenceStamp("inventory", T, T + timedelta(seconds=1)), "BTC_USDT")
        with self.assertRaises(ValueError):
            grid.generate(book(), inventory, T)

    def test_grid_inventory_uses_verified_fills_and_matching_currencies(self):
        sim = EventDrivenExecutionSimulator({"USDT": D("200")}, fee_rate=D("0"))
        sim.submit(SimOrder("x", "BTC_USDT", "BUY", D("1"), D("100"), T))
        before = inventory_from_simulator(sim, "BTC_USDT", T)
        self.assertEqual(before.free_base, D("0"))
        self.assertEqual(before.free_quote, D("100"))
        sim.on_book(book(T + timedelta(seconds=1)))
        after = inventory_from_simulator(sim, "BTC_USDT", T + timedelta(seconds=1))
        self.assertEqual(after.free_base, D("1"))
        grid = GridHypothesis(lower=D("90"), upper=D("110"), intervals=4, quantity=D("1"))
        with self.assertRaises(ValueError):
            grid.generate(book(T + timedelta(seconds=1)), replace(after, symbol="ETH_USDT"), T + timedelta(seconds=1))

    def test_triangle_three_leg_calculation_units_and_risk(self):
        books = [replace(book(bid="9", ask="10", qty="100"), symbol="USDT_IRT"),
                 replace(book(bid="1", ask="2", qty="100"), symbol="BTC_USDT"),
                 replace(book(bid="25", ask="26", qty="100"), symbol="BTC_IRT")]
        signal = TriangularArbitrageHypothesis(fee_rate=D("0")).generate(books, D("100"), T)[0]
        self.assertEqual(D(signal.metadata["profit_irt_scenario"]), D("25"))
        self.assertTrue(signal.metadata["non_atomic"])
        self.assertTrue(signal.metadata["candidate"])
        self.assertEqual(signal.decision, Decision.NO_TRADE)
        self.assertFalse(signal.metadata["scenario_is_executable_profit"])
        shallow = list(books)
        shallow[0] = replace(books[0], asks=(BookLevel(D("10"), D("1")),))
        self.assertFalse(TriangularArbitrageHypothesis(fee_rate=D("0")).generate(shallow, D("100"), T)[0].metadata["candidate"])
        wrong_unit = list(books)
        wrong_unit[0] = replace(books[0], symbol="USDT_IRR")
        with self.assertRaises(ValueError):
            TriangularArbitrageHypothesis().generate(wrong_unit, D("100"), T)

    def test_triangle_skew_and_stale_books_rejected(self):
        books = [replace(book(), symbol=s) for s in TriangularArbitrageHypothesis.SYMBOLS]
        books[0] = replace(books[0], stamp=EvidenceStamp("fixture", T - timedelta(seconds=2), T))
        with self.assertRaises(ValueError):
            TriangularArbitrageHypothesis().generate(books, D("100"), T)

    def test_ledger_and_risk_path_never_invokes_execution(self):
        class ForbiddenExecution:
            def execute(self, *args, **kwargs):
                raise AssertionError("execution must not be reached")
        signal = RsiMaHypothesis().generate(candles([100] * 25), T + timedelta(hours=24))[0]
        ledger = JamalianResearchLedger(ResearchTradingOrchestrator(execution_engine=ForbiddenExecution()))
        snapshot = RiskSnapshot(100, 100, 0, 0, 0, 100, 0)
        outcome = ledger.record(signal, risk_snapshot=snapshot)
        self.assertFalse(outcome.risk.approved)
        self.assertIsNone(outcome.fill)
        ledger.record(signal, risk_snapshot=snapshot)
        exported = ledger.export()
        self.assertEqual(len(exported["records"]), 1)
        exported["records"][0]["signal"]["metadata"]["candidate"] = "mutated"
        self.assertIs(ledger.export()["records"][0]["signal"]["metadata"]["candidate"], False)
        with self.assertRaises(ValueError):
            ledger.record(replace(signal, decision=Decision.BUY_CANDIDATE))

    def test_closed_bar_cli_report_is_offline_and_not_validation(self):
        history = candles([100] * 25)
        document = {"strategy": "rsi_ma", "decision_time": (T + timedelta(hours=24)).isoformat(),
            "candles": [{"symbol": c.symbol, "close": str(c.close), "closed_at": c.stamp.observed_at.isoformat(),
                         "available_at": c.stamp.available_at.isoformat(), "source": "synthetic_smoke"} for c in history]}
        result = run(document, dataset_sha256="a" * 64)
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["mode"], "RESEARCH_ONLY")
        self.assertFalse(result["paper_execution"])
        self.assertEqual(result["validation_status"], "NOT_RUN_REQUIRES_PREREGISTERED_DATA_AND_COSTS")


if __name__ == "__main__":
    unittest.main()
