from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import unittest

from research_bot.contracts import EvidenceStamp
from research_bot.hypothesis import HypothesisCandidate, HypothesisSide
from research_bot.jamalian_data import BookLevel, OrderBook
from research_bot.completion.journal import TradeJournal, FirstSeenRegistry
from research_bot.completion.models import Inventory, MarketRules, CompletionPolicy, ScenarioEconomics
from research_bot.completion.strategies.grid_hypothesis import GridParameters, GridHypothesisGenerator
from research_bot.completion.strategies.triangular_arb_hypothesis import (
    ArbitrageParameters, TriangularArbitrageHypothesis, conversion_graph, walk_book,
)

T = datetime(2026, 10, 11, tzinfo=timezone.utc)


def stamp(at=T):
    return EvidenceStamp("fixture", at, at, lineage="a" * 64, reliability_tier="EXCHANGE_VERIFIED")


def book(symbol="BTC_USDT", bid="99", ask="100", quantity="100", at=T):
    return OrderBook(symbol, (BookLevel(D(bid), D(quantity)),),
        (BookLevel(D(ask), D(quantity)),), stamp(at))


def triangle(at=T):
    return [book("USDT_IRT", "9", "10", at=at), book("BTC_USDT", "1.9", "2", at=at),
            book("BTC_IRT", "25", "26", at=at)]


def inventory(base="1", quote="1000", at=T, reserved=None):
    return Inventory({"BTC": D(base), "USDT": D(quote)}, reserved or {}, stamp(at))


def grid(spacing="arithmetic", **kwargs):
    return GridHypothesisGenerator(GridParameters("BTC_USDT", D("90"), D("110"), 5, D("1"), spacing, **kwargs))


class GridTests(unittest.TestCase):
    def test_empty_full_and_balanced_inventory(self):
        self.assertEqual(grid().generate(D("100"), inventory("0", "0"), D(0), stamp(), T), [])
        sells = grid().generate(D("100"), inventory("10", "0"), D(0), stamp(), T)
        self.assertEqual({h.side for h in sells}, {HypothesisSide.SELL})
        buys = grid().generate(D("100"), inventory("0", "1000"), D(0), stamp(), T)
        self.assertEqual({h.side for h in buys}, {HypothesisSide.BUY})
        results = grid().generate(D("100"), inventory(), D(0), stamp(), T)
        self.assertTrue(all(isinstance(h, HypothesisCandidate) for h in results))
        self.assertEqual(len(results), 3)  # Two buys, one sell; never naked sell.

    def test_capital_exhaustion_and_external_reservation(self):
        self.assertEqual(grid().generate(D("100"), inventory("0", "100"), D("100"), stamp(), T), [])
        results = grid().generate(D("100"), inventory("0", "100"), D(0), stamp(), T)
        self.assertEqual(len(results), 1)
        self.assertEqual(D(results[0].metadata["reservation"]), D("90"))

    def test_duplicate_snapshot_does_not_add_reservations(self):
        g = grid()
        first = g.generate(D("100"), inventory(), D(0), stamp(), T)
        self.assertEqual(g.generate(D("100"), inventory(), D(0), stamp(), T), first)
        self.assertEqual(len(g.levels), 3)
        self.assertEqual(g.generate(D("100"), inventory(), D(0), stamp(), T + timedelta(seconds=1)), [])

    def test_recenter_requires_releases_and_multiple_observations(self):
        g = grid()
        first = g.generate(D("100"), inventory(), D(0), stamp(), T)
        self.assertEqual(g.generate(D("120"), inventory(), D(0), stamp(), T + timedelta(seconds=1)), [])
        self.assertEqual(g.status, "SUSPENDED_OUTSIDE_RANGE")
        self.assertEqual(len(g.pending_cancel_ids), 3)
        for h in first:
            g.release(h.metadata["level_id"], "CANCELLED")
        self.assertEqual(g.generate(D("120"), inventory(), D(0), stamp(), T + timedelta(seconds=2)), [])
        self.assertEqual(g.epoch, 0)  # Re-polling one snapshot is not new evidence.
        results = g.generate(D("120"), inventory(), D(0), stamp(T + timedelta(seconds=3)), T + timedelta(seconds=3))
        self.assertEqual(g.epoch, 1)
        self.assertEqual(g.lower, D("108"))
        self.assertTrue(results)

    def test_no_recenter_with_outstanding_external_capital(self):
        g = grid(recenter_after_snapshots=1)
        self.assertEqual(g.generate(D("120"), inventory(), D("1"), stamp(), T), [])
        self.assertEqual(g.epoch, 0)

    def test_geometric_prices_and_invalid_rounded_grid(self):
        g = GridHypothesisGenerator(GridParameters("BTC_USDT", D("100"), D("400"), 3, D("1"), "geometric"))
        result = g.generate(D("210"), inventory("2", "1000"), D(0), stamp(), T)
        self.assertEqual({D(h.metadata["price"]) for h in result}, {D("100"), D("200"), D("400")})
        with self.assertRaises(ValueError):
            GridHypothesisGenerator(GridParameters("BTC_USDT", D("1"), D("1.01"), 10, D("1"), "geometric"))

    def test_submitted_levels_not_reserved_twice_and_state_restart(self):
        g = grid()
        result = g.generate(D("100"), inventory(), D(0), stamp(), T)
        for h in result:
            g.mark_submitted(h.metadata["level_id"])
        restarted = grid()
        restarted.restore(g.checkpoint())
        self.assertEqual(restarted.checkpoint(), g.checkpoint())
        with self.assertRaises(ValueError):
            g.release(result[0].metadata["level_id"], "PARTIALLY_FILLED")

    def test_stale_inventory_bad_params_and_model_economics(self):
        with self.assertRaises(ValueError):
            grid().generate(D("100"), inventory(), D(0), stamp(), T + timedelta(minutes=1))
        for changes in ({"fee_rate": D(1)}, {"levels": 1}, {"quantity": D("0.000000001")}, {"spacing": "bad"}):
            p = GridParameters("BTC_USDT", D("90"), D("110"), 5, D("1"))
            with self.assertRaises(ValueError):
                replace(p, **changes)
        h = grid().generate(D("100"), inventory(), D(0), stamp(), T)[0]
        e = ScenarioEconomics.from_metadata(h.metadata)
        self.assertEqual(e.expected_return, D(10) / D(90))
        self.assertGreater(e.expected_cost, 0)
        self.assertFalse(h.metadata["prospective_evidence"])


class ArbitrageTests(unittest.TestCase):
    def generator(self, **kwargs):
        return TriangularArbitrageHypothesis(ArbitrageParameters(fee_rate=D(0), slippage_buffer=D(0),
            non_atomic_penalty=D(0), residual_penalty=D(0), uncertainty_penalty=D(0), **kwargs))

    def test_exact_three_leg_units_and_no_double_cost(self):
        h = self.generator().generate(triangle(), D("100"), T)[0]
        self.assertIs(h.side, HypothesisSide.MULTI_LEG)
        self.assertEqual(D(h.metadata["final_currency_amount_scenario"]), D("125"))
        self.assertEqual(ScenarioEconomics.from_metadata(h.metadata).net_edge, D("0.25"))
        self.assertEqual(len(h.metadata["failure_paths"]), 6)
        self.assertFalse(h.metadata["historical_backfill_is_prospective"])

    def test_depth_vwap_walk_not_top_only(self):
        b = replace(book(ask="10", bid="9"), asks=(BookLevel(D("10"), D("1")), BookLevel(D("20"), D("1"))))
        edge = conversion_graph([b])["USDT", "BTC"]
        leg = walk_book(edge, D("15"), MarketRules(), D("0.01"), D(0))
        self.assertEqual(leg.base_quantity, D("1.25"))
        self.assertEqual(leg.vwap, D("12"))
        self.assertEqual(leg.received, D("1.2375"))
        self.assertFalse(leg.depth_exhausted)

    def test_sizing_reduces_budget_when_second_leg_is_shallow(self):
        books = triangle()
        books[1] = replace(books[1], asks=(BookLevel(D("2"), D("2")),))
        h = self.generator().generate(books, D("100"), T)[0]
        self.assertLess(D(h.metadata["budget"]), D("100"))
        self.assertTrue(all(not leg["depth_exhausted"] for leg in h.metadata["legs"]))

    def test_fees_risk_and_threshold_can_close_opportunity(self):
        p = ArbitrageParameters(fee_rate=D("0.2"), non_atomic_penalty=D("0.1"))
        g = TriangularArbitrageHypothesis(p)
        self.assertEqual(g.generate(triangle(), D("100"), T), [])
        self.assertEqual(g.diagnostics["status"], "CLOSED")
        self.assertEqual(self.generator(threshold=D("0.25")).generate(triangle(), D("100"), T), [])

    def test_decay_measures_sampled_open_and_closed_duration(self):
        g = self.generator()
        g.generate(triangle(), D("100"), T)
        g.generate(triangle(T + timedelta(seconds=2)), D("100"), T + timedelta(seconds=2))
        self.assertEqual(g.diagnostics["observed_duration_seconds"], "2")
        books = triangle(T + timedelta(seconds=3))
        books[-1] = book("BTC_IRT", "15", "16", at=T + timedelta(seconds=3))
        self.assertEqual(g.generate(books, D("100"), T + timedelta(seconds=3)), [])
        self.assertEqual(g.diagnostics["observed_duration_seconds"], "3")
        with self.assertRaises(ValueError):
            g.generate(triangle(), D("100"), T)

    def test_generalized_cycle_and_currency_mismatch(self):
        p = ArbitrageParameters(cycle=("AAA", "BBB", "CCC", "AAA"), fee_rate=D(0), slippage_buffer=D(0))
        books = [book("BBB_AAA", "9", "10"), book("CCC_BBB", "1.9", "2"), book("CCC_AAA", "25", "26")]
        self.assertTrue(TriangularArbitrageHypothesis(p).generate(books, D("100"), T))
        with self.assertRaises(ValueError):
            self.generator().generate(books, D("100"), T)

    def test_skew_duplicate_min_notional_and_future_evidence(self):
        books = triangle()
        books[0] = replace(books[0], stamp=stamp(T - timedelta(seconds=2)))
        with self.assertRaises(ValueError):
            self.generator().generate(books, D("100"), T)
        with self.assertRaises(ValueError):
            conversion_graph([books[1], books[1]])
        g = TriangularArbitrageHypothesis(rules={"USDT_IRT": MarketRules(min_notional=D("100000"))})
        self.assertEqual(g.generate(triangle(), D("100"), T), [])
        with self.assertRaises(ValueError):
            self.generator().generate(triangle(T + timedelta(seconds=1)), D("100"), T)


class JournalAndFirstSeenTests(unittest.TestCase):
    def setUp(self):
        self.journal = TradeJournal(":memory:", "completion_tests")

    def tearDown(self):
        self.journal.close()

    def test_registration_idempotency_and_historical_cannot_be_promoted(self):
        registry = FirstSeenRegistry(self.journal)
        first = registry.register("one", stamp(), {"price": "100"}, T, "HISTORICAL")
        self.assertFalse(first["prospective_clean"])
        self.assertEqual(registry.register("one", stamp(), {"price": "100"}, T + timedelta(seconds=1), "HISTORICAL"), first)
        with self.assertRaises(ValueError):
            registry.register("one", stamp(), {"price": "100"}, T, "PROSPECTIVE")
        self.assertEqual(len(self.journal.events("FIRST_SEEN")), 1)

    def test_clean_late_and_receipt_only_registration(self):
        registry = FirstSeenRegistry(self.journal)
        self.assertTrue(registry.register("clean", stamp(), {}, T, "PROSPECTIVE")["prospective_clean"])
        self.assertFalse(registry.register("late", stamp(), {}, T + timedelta(seconds=61), "PROSPECTIVE")["prospective_clean"])
        self.assertFalse(registry.register("unknown", replace(stamp(), reliability_tier="RECEIPT_TIME_ONLY"), {}, T, "PROSPECTIVE")["prospective_clean"])
        self.assertFalse(registry.report()["prospective_clean"])

    def test_generator_candidates_logged_in_authorized_paper_mode(self):
        policy = CompletionPolicy()
        self.assertTrue(policy.paper_execution)
        self.assertFalse(policy.live_execution)
        for h in grid().generate(D("100"), inventory(), D(0), stamp(), T):
            self.journal.append(h.hypothesis_id, "HYPOTHESIS", T, h.to_dict())
        h = TriangularArbitrageHypothesis().generate(triangle(), D("100"), T)[0]
        self.journal.append(h.hypothesis_id, "HYPOTHESIS", T, h.to_dict())
        self.assertEqual(len(self.journal.events("HYPOTHESIS")), 4)

    def test_transaction_failure_rolls_back_and_corruption_rejects(self):
        with self.assertRaises(RuntimeError):
            with self.journal.atomic():
                self.journal.append("one", "STATE", T, {})
                raise RuntimeError("failure")
        self.assertEqual(self.journal.events(), ())
        self.journal.checkpoint("state", {"money": D("123.456")})
        self.assertEqual(self.journal.restore("state")["money"], "123.456")
        self.journal.connection.execute("UPDATE completion_checkpoints SET digest='bad'")
        with self.assertRaises(ValueError):
            self.journal.restore("state")


if __name__ == "__main__":
    unittest.main()
