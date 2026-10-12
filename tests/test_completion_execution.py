from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import tempfile
from pathlib import Path
import unittest

from research_bot.completion.journal import TradeJournal
from research_bot.completion.models import CompletionPolicy, MarketRules
from research_bot.completion.execution.event_driven_simulator import EventDrivenExecutionSimulator, OrderRequest, OrderState
from research_bot.completion.execution.multileg import SequentialCycleSimulator
from research_bot.completion.strategies.triangular_arb_hypothesis import TriangularArbitrageHypothesis, ArbitrageParameters
from test_completion_strategies import T, book, triangle


def request(identity="one", **kwargs):
    return OrderRequest(identity, "BTC_USDT", "BUY", D("1"), D("101"), T, **kwargs)


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.journal = TradeJournal(":memory:", "completion_execution_tests")
        self.sim = EventDrivenExecutionSimulator({"USDT": D("300")}, self.journal,
            CompletionPolicy(fee_rate=D("0.01"), slippage_rate=D(0), displayed_depth_fraction=D(1)))

    def tearDown(self):
        self.journal.close()

    def test_complete_lifecycle_partial_fill_fee_and_shared_depth(self):
        self.sim.submit(request(), True)
        self.sim.submit(request("two"), True)
        self.sim.on_book(book(quantity="0.4", at=T + timedelta(seconds=1)))
        self.assertEqual(self.sim.order("one").state, OrderState.PARTIALLY_FILLED)
        self.assertEqual(self.sim.order("two").filled_quantity, D(0))
        self.assertEqual(self.sim.balances()["BTC"], D("0.396"))
        self.assertEqual(self.sim.free("USDT"), D("98.4"))
        self.sim.on_book(book(quantity="0.6", at=T + timedelta(seconds=2)))
        states = [e["payload"]["state"] for e in self.journal.events("ORDER_TRANSITION") if e["payload"]["client_order_id"] == "one"]
        self.assertEqual(states, ["CREATED", "APPROVED", "SUBMITTED_SIMULATED", "ACKNOWLEDGED", "PARTIALLY_FILLED", "FILLED"])

    def test_latency_not_before_and_no_decision_book_fill(self):
        self.sim.submit(request(not_before=T + timedelta(seconds=2)), True)
        self.sim.on_book(book())
        self.sim.on_book(book(at=T + timedelta(seconds=1)))
        self.assertEqual(self.sim.order("one").filled_quantity, D(0))
        self.sim.on_book(book(at=T + timedelta(seconds=2)))
        self.assertEqual(self.sim.order("one").state, OrderState.FILLED)

    def test_idempotency_conflicting_retry_and_snapshot_revision(self):
        first = self.sim.submit(request(), True)
        self.assertEqual(self.sim.submit(request(), False), first)
        with self.assertRaises(ValueError):
            self.sim.submit(replace(request(), quantity=D(2)), True)
        b = book(at=T + timedelta(seconds=1))
        self.sim.on_book(b)
        self.assertEqual(self.sim.on_book(b), ())
        with self.assertRaises(ValueError):
            self.sim.on_book(book(quantity="2", at=T + timedelta(seconds=1)))

    def test_risk_balance_notional_and_naked_sell_rejection(self):
        self.assertEqual(self.sim.submit(request(), False).state, OrderState.REJECTED)
        self.assertEqual(self.sim.submit(replace(request("over"), quantity=D(10)), True).state, OrderState.REJECTED)
        self.assertEqual(self.sim.submit(replace(request("sell"), side="SELL"), True).state, OrderState.REJECTED)
        self.assertEqual(self.sim.submit(replace(request("huge"), quantity=D(100)), True).state, OrderState.REJECTED)

    def test_cancel_race_expiry_and_remaining_reservations(self):
        self.sim.submit(request(), True)
        self.sim.cancel("one", T + timedelta(seconds=1))
        self.sim.on_book(book(quantity="0.5", at=T + timedelta(seconds=1, milliseconds=5)))
        self.assertEqual(self.sim.order("one").filled_quantity, D("0.5"))
        self.sim.advance(T + timedelta(seconds=2))
        self.assertEqual(self.sim.order("one").state, OrderState.CANCELLED)
        self.assertEqual(self.sim.free("USDT"), D("250"))
        late = replace(request("expired"), created_at=T + timedelta(seconds=2), expires_at=T + timedelta(seconds=3))
        self.sim.submit(late, True)
        self.sim.on_book(book(at=T + timedelta(seconds=3)))
        self.assertEqual(self.sim.order("expired").state, OrderState.EXPIRED)

    def test_zero_latency_ack_order_and_sell_fee(self):
        self.journal.close()
        self.journal = TradeJournal(":memory:", "zero")
        self.sim = EventDrivenExecutionSimulator({"BTC": D(1)}, self.journal,
            CompletionPolicy(latency_ms=0, ack_latency_ms=0, slippage_rate=D(0), fee_rate=D("0.01"), displayed_depth_fraction=D(1)))
        self.sim.submit(replace(request(), side="SELL", protection_price=D("99")), True)
        self.sim.on_book(book(at=T + timedelta(seconds=1)))
        self.assertEqual(self.sim.order("one").state, OrderState.FILLED)
        self.assertEqual(self.sim.balances()["USDT"], D("98.01"))

    def test_price_protection_and_bad_order_precision(self):
        self.sim.submit(replace(request(), protection_price=D("99")), True)
        self.sim.on_book(book(at=T + timedelta(seconds=1)))
        self.assertEqual(self.sim.order("one").filled_quantity, D(0))
        with self.assertRaises(ValueError):
            replace(request(), quantity=D("0.000000001"))
        with self.assertRaises(ValueError):
            self.sim.advance(T)

    def test_restart_is_exact_and_policy_drift_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite"
            balances = {"USDT": D(300)}
            p = CompletionPolicy(seed=9, jitter_ms=30, slippage_rate=D(0), displayed_depth_fraction=D(1))
            left = TradeJournal(path, "restart")
            sim = EventDrivenExecutionSimulator(balances, left, p)
            sim.submit(request(), True)
            sim.on_book(book(quantity="0.4", at=T + timedelta(seconds=1)))
            left.close()
            right = TradeJournal(path, "restart")
            resumed = EventDrivenExecutionSimulator(balances, right, p)
            resumed.on_book(book(quantity="0.6", at=T + timedelta(seconds=2)))
            self.assertEqual(resumed.order("one").state, OrderState.FILLED)
            self.assertEqual(resumed.balances()["USDT"], D(200))
            with self.assertRaises(ValueError):
                EventDrivenExecutionSimulator(balances, right, replace(p, seed=10))
            right.close()

    def test_persistence_failure_rolls_back_money_and_order(self):
        original = self.journal.checkpoint
        def broken(name, payload):
            raise RuntimeError("database failure")
        self.journal.checkpoint = broken
        with self.assertRaises(RuntimeError):
            self.sim.submit(request(), True)
        self.journal.checkpoint = original
        self.assertEqual(self.sim.balances(), {"USDT": D(300)})
        self.assertEqual(self.journal.events(), ())
        self.assertEqual(self.sim.submit(request(), True).state, OrderState.APPROVED)

    def test_real_paper_gate_cannot_be_enabled_by_flag_alone(self):
        with self.assertRaises(ValueError):
            EventDrivenExecutionSimulator({"USDT": D(300)}, self.journal,
                CompletionPolicy(purpose="GRADUATED_RESEARCH"))


class MultiLegTests(unittest.TestCase):
    def setUp(self):
        self.journal = TradeJournal(":memory:", "multi")
        self.sim = EventDrivenExecutionSimulator({"IRT": D(1000)}, self.journal,
            CompletionPolicy(fee_rate=D(0), slippage_rate=D(0), displayed_depth_fraction=D(1)))
        self.cycles = SequentialCycleSimulator(self.sim, timedelta(seconds=2))
        p = ArbitrageParameters(fee_rate=D(0), slippage_buffer=D(0), non_atomic_penalty=D(0),
            residual_penalty=D(0), uncertainty_penalty=D(0))
        self.h = TriangularArbitrageHypothesis(p).generate(triangle(), D(100), T)[0]

    def tearDown(self):
        self.journal.close()

    def step(self, second, books=None):
        at = T + timedelta(seconds=second)
        for b in books or triangle(at):
            self.sim.on_book(b, at)
        self.cycles.advance(at)

    def test_sequential_legs_never_fill_atomically(self):
        identity = self.cycles.start(self.h, T, True)
        self.assertEqual(self.cycles.start(self.h, T, True), identity)
        self.step(1)
        self.assertEqual(self.cycles.cycles[identity]["index"], 1)
        self.step(2)
        self.step(3)
        self.assertEqual(self.cycles.cycles[identity]["status"], "COMPLETED")
        self.assertEqual(self.sim.balances()["IRT"], D(1025))
        self.assertEqual(self.cycles.failure_statistics()["completed"], 1)

    def test_partial_failure_residual_and_emergency_unwind(self):
        identity = self.cycles.start(self.h, T, True)
        self.step(1)
        books = triangle(T + timedelta(seconds=2))
        books[1] = book("BTC_USDT", "1.9", "2", "1", T + timedelta(seconds=2))
        self.step(2, books)
        self.step(3)
        self.assertEqual(self.cycles.cycles[identity]["status"], "FAILED_EXPIRED")
        self.assertGreater(D(self.cycles.residual(identity)["BTC"]), 0)
        orders = self.cycles.emergency_unwind(identity, triangle(T + timedelta(seconds=3)), T + timedelta(seconds=3))
        self.assertTrue(orders)
        self.step(4)
        self.assertEqual(self.cycles.failure_statistics()["failed"], 1)
        self.assertGreater(self.sim.balances()["IRT"], D(900))

    def test_unwind_requires_terminal_cycle(self):
        identity = self.cycles.start(self.h, T, True)
        with self.assertRaises(ValueError):
            self.cycles.emergency_unwind(identity, triangle(), T)

    def test_actual_receipts_below_later_leg_minimum_fail_closed(self):
        self.sim = EventDrivenExecutionSimulator({"IRT": D(1000)}, self.journal,
            CompletionPolicy(fee_rate=D("0.01"), slippage_rate=D(0), displayed_depth_fraction=D(1)))
        self.cycles = SequentialCycleSimulator(self.sim)
        generator = TriangularArbitrageHypothesis(ArbitrageParameters(fee_rate=D(0), slippage_buffer=D(0),
            non_atomic_penalty=D(0), residual_penalty=D(0), uncertainty_penalty=D(0)),
            {"BTC_USDT": MarketRules(min_notional=D("9.95"))})
        candidate = generator.generate(triangle(), D(100), T)[0]
        identity = self.cycles.start(candidate, T, True)
        self.step(1)
        self.assertEqual(self.cycles.cycles[identity]["status"], "FAILED_MIN_NOTIONAL")
        self.assertEqual(len(self.cycles.cycles[identity]["orders"]), 1)
        self.assertEqual(D(self.cycles.residual(identity)["USDT"]), D("9.90"))
        self.assertEqual(self.cycles.failure_statistics()["failed"], 1)


if __name__ == "__main__":
    unittest.main()
