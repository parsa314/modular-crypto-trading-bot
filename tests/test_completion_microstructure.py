"""Mechanistic stress and inverse-math checks, not venue calibration evidence."""
from dataclasses import replace
from contextlib import closing
from datetime import timedelta
from decimal import Decimal as D
import json
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError
from research_bot.completion.execution.cost_preflight import ExecutionCostPreflight
from research_bot.completion.execution.impact_estimator import (
    ImpactDecision, MarketSnapshot, SquareRootImpactEstimator,
)
from research_bot.completion.execution.input_validation import SimulationStressInput
from research_bot.completion.execution.liquidity_gate import DepthAwareLiquidityGate, LiquidityDecision
from research_bot.completion.execution.event_driven_simulator import EventDrivenExecutionSimulator, OrderState
from research_bot.completion.models import CompletionPolicy, MarketRules
from research_bot.completion.journal import TradeJournal
from research_bot.jamalian_data import BookLevel, OrderBook
from test_completion_execution import request
from test_completion_strategies import T, stamp, book


def liquid(at=T, bid_qty="100", ask_qty="100"):
    return OrderBook("BTC_USDT", (BookLevel(D("99.9"), D(bid_qty)),),
        (BookLevel(D("100.1"), D(ask_qty)),), stamp(at))


class ImpactTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = MarketSnapshot("BTC_USDT", D(100), D("0.02"), D(10000), stamp())
        self.estimator = SquareRootImpactEstimator(D(1), max_participation_ratio=D(1))

    def test_inverse_has_one_volume_price_factor_and_rechecks_edge(self):
        result = self.estimator.estimate(self.snapshot, D(2), D(30), T)
        self.assertEqual(result.decision, ImpactDecision.REDUCE)
        self.assertEqual(result.max_viable_quantity_base, D("1.5625"))
        self.assertEqual(result.recommended_quantity_base, D(1))
        self.assertEqual(result.recommended_impact_bps, D(20))
        # With only 10bps remaining edge the original cap branch must NOT recommend 20bps.
        poor_edge = self.estimator.estimate(self.snapshot, D(2), D(10), T)
        self.assertLess(poor_edge.recommended_impact_bps, D(10))
        self.assertLess(poor_edge.recommended_quantity_base, D("0.25"))

    def test_ok_equality_never_ok_and_no_edge_abstains(self):
        ok = self.estimator.estimate(self.snapshot, D(1), D(21), T)
        self.assertEqual(ok.decision, ImpactDecision.OK)
        self.assertEqual(ok.recommended_quantity_base, D(1))
        equal = self.estimator.estimate(self.snapshot, D(1), D(20), T)
        self.assertNotEqual(equal.decision, ImpactDecision.OK)
        self.assertLess(equal.recommended_impact_bps, D(20))
        for edge in (D(0), D(-1), D("0.05")):
            self.assertEqual(self.estimator.estimate(self.snapshot, D(1), edge, T).decision, ImpactDecision.ABSTAIN)

    def test_participation_lot_precision_and_min_notional(self):
        bounded = SquareRootImpactEstimator(D("0.01"), max_participation_ratio=D("0.001"))
        result = bounded.estimate(self.snapshot, D(1), D(100), T)
        self.assertEqual(result.recommended_quantity_base, D("0.1"))
        self.assertEqual(result.decision, ImpactDecision.REDUCE)
        for rules in (MarketRules(quantity_step=D(2)), MarketRules(min_notional=D(200))):
            self.assertEqual(self.estimator.estimate(self.snapshot, D(2), D(30), T, rules).reason_code, "BELOW_MINIMUM_VIABLE_LOT")
        rounding = self.estimator.estimate(self.snapshot, D("0.123"), D(30), T, MarketRules(quantity_step=D("0.1")))
        self.assertEqual(rounding.recommended_quantity_base, D("0.1"))

    def test_invalid_zero_stale_future_and_units(self):
        for value in (0, -1, True, 0.1, D("NaN"), D("Infinity")):
            self.assertEqual(self.estimator.estimate(self.snapshot, value, D(30), T).decision, ImpactDecision.ABSTAIN)
        for field in ("volatility_24h", "daily_volume_quote"):
            result = self.estimator.estimate(replace(self.snapshot, **{field:D(0)}), D(1), D(30), T)
            self.assertEqual(result.reason_code, "MISSING_MARKET_STATISTICS")
            self.assertIsNone(result.estimated_impact_bps)
        for at in (T - timedelta(seconds=1), T + timedelta(seconds=31), T.replace(tzinfo=None)):
            self.assertEqual(self.estimator.estimate(self.snapshot, D(1), D(30), at).decision, ImpactDecision.ABSTAIN)
        with self.assertRaises(ValueError):
            replace(self.snapshot, mid_price=100.0)
        with self.assertRaises(ValueError):
            replace(self.snapshot, symbol="bad")
        # Scaling both quote price and quote volume leaves base sizing and bps unchanged.
        scaled = replace(self.snapshot, mid_price=D(1000), daily_volume_quote=D(100000))
        self.assertEqual(self.estimator.estimate(scaled, D(2), D(30), T), self.estimator.estimate(self.snapshot, D(2), D(30), T))
        self.assertEqual(self.snapshot.to_dict()["volatility_basis"], "DAILY_RETURN_STD")
        json.dumps(self.estimator.estimate(self.snapshot, D(0), D(30), T).to_dict(), allow_nan=False)

    def test_configuration_never_raises_existing_caps(self):
        for values in ({"k_coefficient":D(0)}, {"k_coefficient":0.25},
            {"max_impact_bps_threshold":D(26)}, {"reduce_target_bps":D(26)},
            {"max_participation_ratio":D(2)}, {"max_age":timedelta(0)}):
            with self.assertRaises(ValueError):
                SquareRootImpactEstimator(**({"k_coefficient":D(1)} | values))
        self.assertEqual(self.estimator.to_dict()["model_status"], "UNCALIBRATED_SCENARIO")


class LiquidityTests(unittest.TestCase):
    def setUp(self):
        self.gate = DepthAwareLiquidityGate()

    def test_buy_exit_uses_bids_not_asks_and_spread_reason(self):
        no_exit = self.gate.validate(liquid(bid_qty="1", ask_qty="100"), "BUY", D(1), T)
        self.assertEqual(no_exit.decision, LiquidityDecision.REJECTED_EXIT_LIQUIDITY_LOW)
        self.assertEqual(no_exit.details["exit_side"], "BID")
        wide = self.gate.validate(book(), "BUY", D(1), T)
        self.assertEqual(wide.reason_code, "SPREAD_EXCEEDS_LIMIT")
        self.assertEqual(wide.decision, LiquidityDecision.REJECTED_SPREAD_TOO_WIDE)

    def test_sell_uses_bids_and_only_reduces_long(self):
        rejected = self.gate.validate(liquid(bid_qty="1", ask_qty="100"), "SELL", D(1), T, D(1))
        self.assertEqual(rejected.decision, LiquidityDecision.REJECTED_DEPTH_INSUFFICIENT)
        allowed = self.gate.validate(liquid(bid_qty="100", ask_qty="1"), "SELL", D(1), T, D(1))
        self.assertEqual(allowed.decision, LiquidityDecision.APPROVED)
        self.assertEqual(allowed.details["post_trade_position_base"], "0")
        self.assertEqual(self.gate.validate(liquid(), "SELL", D(1), T).reason_code, "NAKED_SPOT_SELL")

    def test_band_protection_top_n_and_existing_exposure(self):
        deep = replace(liquid(ask_qty="1"), asks=(BookLevel(D("100.1"), D(1)), BookLevel(D(200), D(1000))))
        self.assertEqual(self.gate.validate(deep, "BUY", D(1), T).decision, LiquidityDecision.REJECTED_DEPTH_INSUFFICIENT)
        self.assertEqual(self.gate.validate(liquid(), "BUY", D(1), T, protection_price=D(100)).decision, LiquidityDecision.REJECTED_DEPTH_INSUFFICIENT)
        self.assertEqual(self.gate.validate(liquid(), "SELL", D(1), T, D(1), D(100)).decision, LiquidityDecision.REJECTED_DEPTH_INSUFFICIENT)
        no_exit = self.gate.validate(liquid(bid_qty="10"), "BUY", D(1), T, D(10))
        self.assertEqual(no_exit.decision, LiquidityDecision.REJECTED_EXIT_LIQUIDITY_LOW)
        ok = self.gate.validate(liquid(), "BUY", D(1), T)
        self.assertEqual(ok.approved_quantity_base, D(1))
        with self.assertRaises(TypeError):
            ok.details["entry_side"] = "BID"
        json.dumps(ok.to_dict(), allow_nan=False)
        self.assertEqual(self.gate.to_dict()["depth_lookback_levels"], 5)

    def test_invalid_and_stale_fail_closed(self):
        for side, qty, position, at in (("ASK",D(1),D(0),T), ("BUY",D(-1),D(0),T),
            ("BUY",0.1,D(0),T), ("BUY",D(1),D(-1),T), ("BUY",D(1),D(0),T+timedelta(seconds=31))):
            result = self.gate.validate(liquid(), side, qty, at, position)
            self.assertEqual(result.decision, LiquidityDecision.REJECTED_INVALID_INPUT)
            self.assertEqual(result.approved_quantity_base, D(0))
        for config in ({"max_consumption_ratio":D(2)}, {"min_exit_depth_multiple":D("0.5")},
            {"max_spread_bps":D(100)}, {"max_depth_distance":D(1)}, {"depth_lookback_levels":True}, {"max_age":timedelta(0)}):
            with self.assertRaises(ValueError):
                DepthAwareLiquidityGate(**config)


class StressTests(unittest.TestCase):
    def setUp(self):
        self.journal = TradeJournal(":memory:", "step3_stress")
        self.policy = CompletionPolicy(fee_rate=D(0), slippage_rate=D(0))
        self.sim = EventDrivenExecutionSimulator({"USDT":D(1000), "BTC":D(10)}, self.journal, self.policy)

    def tearDown(self):
        self.journal.close()

    def test_strict_pydantic_boundary_and_policy_fingerprint(self):
        stress = SimulationStressInput.model_validate({"displayed_depth_fraction":"0.05", "min_submission_interval_ms":200})
        policy = stress.to_policy(self.policy)
        self.assertEqual(policy.displayed_depth_fraction, D("0.05"))
        self.assertTrue(policy.research_only and policy.paper_execution and not policy.live_execution)
        self.assertNotEqual(policy.execution_model_sha256(), self.policy.execution_model_sha256())
        for config in ({"displayed_depth_fraction":0.1}, {"displayed_depth_fraction":"NaN"}, {"displayed_depth_fraction":"0"},
            {"displayed_depth_fraction":"1.1"}, {"min_submission_interval_ms":True}, {"min_submission_interval_ms":"1"},
            {"min_submission_interval_ms":-1}, {"live_execution":True}):
            with self.assertRaises(ValidationError):
                SimulationStressInput.model_validate(config)
        for config in ({"displayed_depth_fraction":D(0)}, {"displayed_depth_fraction":D(2)}, {"min_submission_interval_ms":True}):
            with self.assertRaises(ValueError):
                replace(self.policy, **config)

    def test_depth_haircut_shared_across_orders_and_replay_does_not_replenish(self):
        self.sim.submit(request(), True)
        self.sim.submit(request("two"), True)
        b = liquid(T + timedelta(seconds=1), ask_qty="5")
        self.sim.on_book(b)
        self.assertEqual(self.sim.order("one").filled_quantity, D("0.5"))
        self.assertEqual(self.sim.order("two").filled_quantity, D(0))
        self.assertEqual(self.sim.on_book(b, T+timedelta(seconds=2)), ())
        self.sim.on_book(liquid(T+timedelta(seconds=3), ask_qty="5"))
        self.assertEqual(self.sim.order("one").state, OrderState.FILLED)
        self.assertEqual(self.sim.order("two").filled_quantity, D(0))
        self.sim.submit(replace(request("sell"), created_at=T+timedelta(seconds=3), side="SELL", protection_price=D(99)), True)
        self.sim.on_book(liquid(T+timedelta(seconds=4), bid_qty="5", ask_qty="1"))
        self.assertEqual(self.sim.order("sell").filled_quantity, D("0.5"))
        self.assertEqual(self.journal.events("ORDER_TRANSITION")[-1]["payload"]["displayed_depth_fraction"], "0.10")

    def test_pacing_delays_submission_then_ack_not_the_reverse(self):
        self.sim.submit(request(), True)
        self.sim.submit(request("two"), True)
        self.sim.advance(T+timedelta(milliseconds=30))
        self.assertEqual(self.sim.order("one").state, OrderState.ACKNOWLEDGED)
        self.assertEqual(self.sim.order("two").state, OrderState.APPROVED)
        self.sim.advance(T+timedelta(milliseconds=110))
        self.assertEqual(self.sim.order("two").state, OrderState.SUBMITTED_SIMULATED)
        self.sim.advance(T+timedelta(milliseconds=120))
        self.assertEqual(self.sim.order("two").state, OrderState.ACKNOWLEDGED)
        self.assertEqual(len(self.journal.events("SIMULATED_RATE_LIMIT")), 1)

    def test_cancel_and_expiry_win_before_throttled_submission(self):
        self.sim.submit(request(), True)
        self.sim.submit(request("two"), True)
        self.sim.submit(request("three", expires_at=T+timedelta(milliseconds=50)), True)
        self.sim.cancel("two", T+timedelta(milliseconds=30))
        self.sim.advance(T+timedelta(seconds=1))
        self.assertEqual(self.sim.order("two").state, OrderState.CANCELLED)
        self.assertEqual(self.sim.order("three").state, OrderState.EXPIRED)
        for e in self.journal.events("ORDER_TRANSITION"):
            if e["payload"]["client_order_id"] in {"two", "three"}:
                self.assertNotEqual(e["payload"]["state"], "SUBMITTED_SIMULATED")
        self.assertEqual(self.sim.free("USDT"), D(899))

    def test_book_observed_before_ack_or_not_before_cannot_fill_later(self):
        self.sim.submit(request(not_before=T+timedelta(milliseconds=100)), True)
        self.sim.on_book(liquid(T+timedelta(milliseconds=5)), T+timedelta(seconds=1))
        self.assertEqual(self.sim.order("one").filled_quantity, D(0))
        self.sim.on_book(liquid(T+timedelta(seconds=2)))
        self.assertEqual(self.sim.order("one").state, OrderState.FILLED)

    def test_duplicate_book_advances_expiry_without_fill(self):
        self.sim.submit(request(expires_at=T+timedelta(seconds=2)), True)
        b = liquid(T+timedelta(seconds=1), ask_qty="1")
        self.sim.on_book(b)
        self.sim.on_book(b, T+timedelta(seconds=2))
        self.assertEqual(self.sim.order("one").state, OrderState.EXPIRED)
        self.assertEqual(self.sim.order("one").filled_quantity, D("0.1"))
        with self.assertRaises(ValueError):
            self.sim.on_book(b, T+timedelta(seconds=1))

    def test_multilevel_depth_fraction_and_lot_dust(self):
        self.sim.submit(replace(request(), rules=MarketRules(quantity_step=D("0.1"))), True)
        b = replace(liquid(T+timedelta(seconds=1)), asks=(BookLevel(D("100.1"),D("0.5")), BookLevel(D("100.2"),D(3))))
        self.sim.on_book(b)
        self.assertEqual(self.sim.order("one").filled_quantity, D("0.3"))
        self.assertEqual(len(self.sim.order("one").fills), 1)

    def test_resume_keeps_throttle_slot_and_ack_times(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"step3.sqlite"
            initial = {"USDT":D(1000), "BTC":D(10)}
            saved_journal = TradeJournal(path, "resume_stress")
            saved = EventDrivenExecutionSimulator(initial, saved_journal, self.policy)
            for sim in (saved, self.sim):
                sim.submit(request(), True)
                sim.submit(request("two"), True)
                sim.advance(T+timedelta(milliseconds=30))
            saved_journal.close()
            saved_journal = TradeJournal(path, "resume_stress")
            resumed = EventDrivenExecutionSimulator(initial, saved_journal, self.policy, resume=True)
            for sim in (resumed, self.sim):
                sim.on_book(liquid(T+timedelta(seconds=1), ask_qty="15"))
            self.assertEqual(resumed.orders(), self.sim.orders())
            self.assertEqual(saved_journal.events(), self.journal.events())
            saved_journal.close()

    def test_failed_checkpoint_rolls_back_throttle_and_journal(self):
        self.sim.submit(request(), True)
        self.sim.submit(request("two"), True)
        from research_bot.hypothesis import canonical_json
        before = json.loads(canonical_json(self.sim._dump()))
        events = self.journal.events()
        original = self.journal.checkpoint
        def fail(*args):
            raise RuntimeError("injected checkpoint failure")
        self.journal.checkpoint = fail
        with self.assertRaises(RuntimeError):
            self.sim.advance(T+timedelta(milliseconds=30))
        self.journal.checkpoint = original
        self.assertEqual(self.sim._dump(), before)
        self.assertEqual(self.journal.events(), events)
        self.sim.advance(T+timedelta(milliseconds=120))
        self.assertEqual(self.sim.order("two").state, OrderState.ACKNOWLEDGED)


class PreflightTests(unittest.TestCase):
    def test_journaled_preflight_composes_without_bypassing_risk_or_resizing(self):
        with closing(TradeJournal(":memory:", "cost_preflight_test")) as journal:
            estimator = SquareRootImpactEstimator(D(1), max_participation_ratio=D(1))
            preflight = ExecutionCostPreflight(estimator, DepthAwareLiquidityGate(), journal)
            snapshot = MarketSnapshot("BTC_USDT", D(100), D("0.02"), D(10000), stamp())
            approved = preflight.assess("ok", snapshot, liquid(), "BUY", D(1), D(30), D(0), T)
            self.assertTrue(approved.passed)
            sim = EventDrivenExecutionSimulator({"USDT":D(1000)}, journal)
            self.assertEqual(sim.submit(request(), approved=approved.passed and False).state, OrderState.REJECTED)
            reduced = preflight.assess("reduce", snapshot, liquid(), "BUY", D(2), D(30), D(0), T)
            self.assertFalse(reduced.passed)
            self.assertEqual(reduced.impact.decision, ImpactDecision.REDUCE)
            preflight.assess("reduce", snapshot, liquid(), "BUY", D(2), D(30), D(0), T)
            mismatch = preflight.assess("mismatch", replace(snapshot, symbol="ETH_USDT"), liquid(), "BUY", D(1), D(30), D(0), T)
            self.assertFalse(mismatch.passed)
            self.assertEqual(mismatch.impact.reason_code, "SYMBOL_MISMATCH")
            rows = journal.events("EXECUTION_COST_PREFLIGHT")
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]["payload"]["estimator"]["k"], "1")
            self.assertTrue(rows[0]["payload"]["risk_approval_still_required"])
            with self.assertRaises(ValueError):
                preflight.assess("ok", snapshot, liquid(), "BUY", D(2), D(30), D(0), T)


if __name__ == "__main__":
    unittest.main()
