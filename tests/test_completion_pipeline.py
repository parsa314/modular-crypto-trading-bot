from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
import tempfile
import unittest
from hashlib import sha256
import numpy as np
import pandas as pd

from research_bot.contracts import EvidenceStamp
from research_bot.hypothesis import HypothesisCandidate, HypothesisFamily, HypothesisSide, canonical_json
from research_bot.completion.models import Inventory, ScenarioEconomics, CompletionPolicy
from research_bot.completion.journal import TradeJournal, FirstSeenRegistry
from research_bot.completion.portfolio.conflict_arbiter import PortfolioConflictArbiter, PortfolioView, PortfolioLimits, RiskGate
from research_bot.completion.backtest.runner import BacktestRunner
from research_bot.completion.backtest.metrics import performance_metrics, average_uniqueness
from research_bot.completion.backtest.graduation import GraduationPipeline, GraduationProtocol
from research_bot.completion.strategies.bundle import StrategyBundle
from research_bot.completion.strategies.grid_hypothesis import GridHypothesisGenerator
from research_bot.completion.execution.event_driven_simulator import EventDrivenExecutionSimulator
from research_bot.completion.adapters.bitpin_readonly import BitpinReadOnly
from research_bot.exchanges.bitpin_adapter import BitpinAdapter, BitpinError
from scripts.run_completion_paper import build_bundle, synthetic_frames, run_example
from test_completion_strategies import T, stamp
from test_jamalian_integration import FixtureTransport, response


def hypothesis(symbol="BTC_USDT", quantity="1", side=HypothesisSide.BUY, economics=None, **metadata):
    e = economics or ScenarioEconomics(D("0.02"), D("0.001"), D("0.001"), D("0.001"), D(1))
    return HypothesisCandidate("test_model", HypothesisFamily.GRID, symbol, side, T, T + timedelta(milliseconds=20),
        ("test",), (stamp(),), {}, {"economics": e.to_dict(), "market_spread_fraction": "0.001", **metadata,
            "plan": [{"symbol": symbol, "side": "SELL" if side is HypothesisSide.SELL else "BUY", "quantity": quantity,
                "limit_price": "100", "quantity_step": "0.00000001", "price_tick": "0.01"}]})


def view(balances=None, reserved=None, peak="1000", **kwargs):
    balances = balances or {"USDT": D(1000), "BTC": D(0)}
    return PortfolioView(Inventory(balances, reserved or {}, stamp()),
        {"USDT": D(1), "BTC": D(100), "ETH": D(100), "SOL": D(100), "XRP": D(100)}, stamp(),
        "USDT", D(peak), **kwargs)


class ArbitrationTests(unittest.TestCase):
    def setUp(self):
        self.journal = TradeJournal(":memory:", "arbiter_tests")
        self.arbiter = PortfolioConflictArbiter(self.journal)

    def tearDown(self):
        self.journal.close()

    def reason(self, h, portfolio=None, arbiter=None):
        return (arbiter or self.arbiter).resolve([h], portfolio or view(), T).rejected[0].reason

    def test_ranked_approval_reason_codes_and_journal(self):
        one, other = hypothesis(), hypothesis(quantity="1.1")
        result = self.arbiter.resolve([one, other], view(), T)
        self.assertEqual(len(result.approved), 1)
        self.assertEqual(result.rejected[0].reason, "ASSET_CONFLICT")
        self.assertEqual(len(self.journal.events("ARBITRATION")), 2)
        self.assertEqual(self.reason(result.approved[0]), "DUPLICATE_HYPOTHESIS")

    def test_regime_correlation_and_uniqueness_are_explicit_score_inputs(self):
        h = hypothesis()
        result = self.arbiter.resolve([h], view(active_assets=("ETH",)), T,
            correlations={("BTC", "ETH"): D("0.8")}, regime_fit={h.strategy_id: D("0.7")}, context_stamp=stamp())
        record = self.journal.events("ARBITRATION")[0]["payload"]
        self.assertEqual(record["correlation_penalty"], "0.8")
        self.assertEqual(record["regime_fit"], "0.7")
        self.assertEqual(record["uniqueness"], "1")
        with self.assertRaises(ValueError):
            self.arbiter.resolve([h], view(), T, regime_fit={h.strategy_id: D(1)})

    def test_limits_cannot_increase_and_invalid_context_is_closed(self):
        for kwargs in ({"max_asset_weight": D("0.36")}, {"min_cash_buffer": D("0.01")}, {"max_positions": 4}):
            with self.assertRaises(ValueError):
                PortfolioLimits(**kwargs)
        with self.assertRaises(ValueError):
            self.arbiter.resolve([hypothesis()], view(), T, correlations={("BTC", "ETH"): D(2)}, context_stamp=stamp())
        with self.assertRaises(ValueError):
            self.arbiter.resolve([hypothesis(), hypothesis()], view(), T)

    def test_decision_abstention_missing_sizing_and_simultaneity(self):
        low = ScenarioEconomics(D("0.02"), D(0), D(0), D(0), D("0.1"))
        self.assertEqual(self.reason(hypothesis(economics=low)), "DECISION_ABSTAINED")
        h = hypothesis()
        missing = replace(h, metadata={})
        self.assertEqual(self.reason(missing), "UNCALIBRATED_OR_UNSIZED_HYPOTHESIS")
        with self.assertRaises(ValueError):
            self.arbiter.resolve([h], view(), T + timedelta(seconds=1))

    def test_capital_capacity_turnover_and_concentration_reasons(self):
        self.assertEqual(self.reason(hypothesis(quantity="11")), "CAPITAL_EXHAUSTED")
        self.assertEqual(self.reason(hypothesis(quantity="6")), "TURNOVER_LIMIT")
        self.assertEqual(self.reason(hypothesis(quantity="4")), "CONCENTRATION_LIMIT")
        self.assertEqual(self.reason(hypothesis(), view(active_ids=("a", "b", "c"))), "POSITION_CAPACITY")

    def test_spread_slippage_cash_and_gross_exposure_reasons(self):
        self.assertEqual(self.reason(hypothesis(market_spread_fraction="0.01")), "SPREAD_LIMIT")
        self.assertEqual(self.reason(hypothesis(depth_slippage_fraction="0.01")), "SLIPPAGE_LIMIT")
        self.assertEqual(self.reason(hypothesis(quantity="0.1"), view(reserved={"USDT": D(950)})), "CASH_BUFFER_LIMIT")
        limited = PortfolioConflictArbiter(self.journal, risk_gate=RiskGate(PortfolioLimits(max_gross_exposure=D("0.6"))))
        portfolio = view({"USDT": D(400), "BTC": D(1), "ETH": D(5)})
        self.assertEqual(self.reason(hypothesis(), portfolio, limited), "GROSS_EXPOSURE_LIMIT")

    def test_drawdown_tail_risk_and_reducing_sell(self):
        self.assertEqual(self.reason(hypothesis(), view(peak="1200")), "DRAWDOWN_OR_EQUITY_LIMIT")
        self.assertEqual(self.reason(hypothesis(), view(recent_returns=(D("-0.1"),) * 20)), "CVAR_LIMIT")
        result = self.arbiter.resolve([hypothesis(side=HypothesisSide.SELL)], view({"BTC": D(1), "USDT": D(900)}), T)
        self.assertEqual(len(result.approved), 1)

    def test_persistence_failure_restores_arbiter_memory(self):
        original = self.journal.checkpoint
        self.journal.checkpoint = lambda name, data: (_ for _ in ()).throw(RuntimeError("disk"))
        with self.assertRaises(RuntimeError):
            self.arbiter.resolve([hypothesis()], view(), T)
        self.journal.checkpoint = original
        self.assertEqual(len(self.arbiter.resolve([hypothesis()], view(), T).approved), 1)


class ReplayTests(unittest.TestCase):
    def test_four_strategy_paper_and_restart_matches_uninterrupted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "resume.sqlite"
            first = run_example(path, 32)
            final = run_example(path, 40)
            cold = run_example(Path(directory) / "cold.sqlite", 40)
            self.assertEqual(final, cold)
            self.assertEqual(len(final["strategies_observed"]), 4)
            self.assertGreater(final["fills"], 0)
            self.assertFalse(final["first_seen"]["prospective_clean"])
            self.assertFalse(final["economic_validation"])

    def test_ohlcv_only_replay_uses_labeled_modeled_depth(self):
        journal = TradeJournal(":memory:", "ohlcv")
        runner = BacktestRunner(journal, StrategyBundle(), {"USDT": D(100), "BTC": D(1)})
        frame = replace(synthetic_frames(1)[0], books=(), data_class="HISTORICAL")
        record = runner.step(frame)
        self.assertEqual(record["fill_model"], "OHLCV_SYNTHETIC_DEPTH")
        self.assertEqual(runner.step(frame), record)
        with self.assertRaises(ValueError):
            runner.step(replace(frame, volume=D(101)))
        self.assertEqual(runner.report()["mode"], "BACKTEST")
        journal.close()

    def test_engineering_paper_rejects_real_data_and_strategy_drift(self):
        journal = TradeJournal(":memory:", "guard")
        runner = BacktestRunner(journal, build_bundle(), {"USDT": D(1000), "IRT": D(1000), "BTC": D(1)}, CompletionPolicy(), "IRT")
        with self.assertRaises(ValueError):
            runner.step(replace(synthetic_frames(1)[0], data_class="HISTORICAL"))
        runner.step(synthetic_frames(1)[0])
        changed = build_bundle()
        changed.arbitrage_budget = D(99)
        with self.assertRaises(ValueError):
            BacktestRunner(journal, changed, {"USDT": D(1000), "IRT": D(1000), "BTC": D(1)}, CompletionPolicy(), "IRT")
        journal.close()

    def test_backtest_cannot_self_register_prospective_evidence(self):
        journal = TradeJournal(":memory:", "original_capture_required")
        try:
            runner = BacktestRunner(journal, StrategyBundle(), {"USDT": D(100), "BTC": D(1)})
            with self.assertRaisesRegex(ValueError, "cannot create prospective"):
                runner.step(replace(synthetic_frames(1)[0], data_class="PROSPECTIVE"))
            self.assertFalse(journal.events())
        finally:
            journal.close()

    def test_runner_failure_rolls_back_all_collaborators(self):
        journal = TradeJournal(":memory:", "rollback")
        runner = BacktestRunner(journal, build_bundle(), {"USDT": D(1000), "IRT": D(1000), "BTC": D(1)}, CompletionPolicy(), "IRT")
        original = journal.checkpoint
        def broken(name, data):
            if name == "runner":
                raise RuntimeError("storage failure")
            original(name, data)
        journal.checkpoint = broken
        with self.assertRaises(RuntimeError):
            runner.step(synthetic_frames(1)[0])
        journal.checkpoint = original
        self.assertEqual(journal.events(), ())
        self.assertEqual(runner.report()["status"], "EMPTY_REPLAY")
        self.assertEqual(runner.step(synthetic_frames(1)[0])["data_class"], "SYNTHETIC")
        journal.close()

    def test_metrics_independent_reference_and_uniqueness(self):
        intervals = [(T, T + timedelta(seconds=2)), (T + timedelta(seconds=1), T + timedelta(seconds=3))]
        self.assertEqual(average_uniqueness(intervals), D("0.75"))
        metrics = performance_metrics([D(100), D(110), D(99)], [D(10), D(-5)], intervals, 252)
        self.assertEqual(metrics["max_drawdown"], "0.1")
        self.assertEqual(metrics["profit_factor"], "2")
        self.assertEqual(metrics["win_rate"], "0.5")
        with self.assertRaises(ValueError):
            performance_metrics([D(0)], [], [], 252)

    def test_complete_trial_diagnostics_match_actual_replays(self):
        journals = [TradeJournal(":memory:", f"costed_trial_{i}") for i in range(2)]
        try:
            runners = []
            for i, journal in enumerate(journals):
                bundle = build_bundle()
                if i:
                    bundle.grid = GridHypothesisGenerator(replace(bundle.grid.parameters, quantity=D("0.05")))
                bundle = StrategyBundle(grid=bundle.grid)
                runners.append(BacktestRunner(journal, bundle, {"USDT": D(1000), "IRT": D(1000), "BTC": D(1)},
                    CompletionPolicy(mode="BACKTEST"), "IRT"))
            reports = [runner.run(synthetic_frames(121)) for runner in runners]
            index = pd.DatetimeIndex([row["timestamp"] for row in reports[0]["curve"][1:]])
            matrix = pd.DataFrame({f"trial_{i}": [float(D(v)) for v in report["metrics"]["net_returns"]]
                for i, report in enumerate(reports)}, index=index)
            result = runners[0].diagnose_trials(matrix, index + pd.Timedelta(milliseconds=500), "trial_0")
            self.assertIsNotNone(result["metrics"]["pbo"])
            self.assertEqual(len(journals[0].events("VALIDATION_DIAGNOSTIC")), 1)
            self.assertFalse(result["economic_validation"])
            matrix.iloc[0, 0] = 99
            with self.assertRaises(ValueError):
                runners[0].diagnose_trials(matrix, index, "trial_0")
        finally:
            for journal in journals:
                journal.close()

    def test_caught_nested_transaction_failure_does_not_commit_partial_events(self):
        journal = TradeJournal(":memory:", "nested")
        with journal.atomic():
            journal.append("keep", "TEST", T, {})
            try:
                with journal.atomic():
                    journal.append("rollback", "TEST", T, {})
                    raise RuntimeError("failed nested mutation")
            except RuntimeError:
                pass
        self.assertEqual([e["event_id"] for e in journal.events()], ["keep"])
        journal.close()

    def test_readonly_facade_has_no_order_or_cancel_api(self):
        transport = FixtureTransport([response([{"symbol": "BTC_USDT", "price": "100.123456789"}]),
            response({"bids": [["99", "1"]], "asks": [["100", "1"]]})])
        facade = BitpinReadOnly(BitpinAdapter(transport=transport, clock=lambda: T))
        self.assertEqual(facade.get_all_prices()[0].price, D("100.123456789"))
        self.assertEqual(facade.get_orderbook("BTC_USDT").symbol, "BTC_USDT")
        with self.assertRaises(BitpinError):
            facade.get_my_balance()
        self.assertFalse(hasattr(facade, "place_order"))
        self.assertFalse(hasattr(facade, "cancel_order"))


class GraduationTests(unittest.TestCase):
    def setUp(self):
        self.journal = TradeJournal(":memory:", "graduation_tests")
        self.registry = FirstSeenRegistry(self.journal)

    def tearDown(self):
        self.journal.close()

    def fixture(self, n=121):
        index = pd.date_range(T, periods=n, freq="h")
        a = 0.003 + np.sin(np.arange(n - 1) * 0.7) * 0.001
        b = 0.001 + np.cos(np.arange(n - 1) * 0.3) * 0.002
        matrix = pd.DataFrame({"a": a, "b": b}, index=index[1:])
        times = [t.to_pydatetime() for t in index]
        backtest = {"mode": "BACKTEST", "purpose": "GRADUATED_RESEARCH", "frames": n,
            "dataset_sha256": "a" * 64, "source_event_times": [t.isoformat() for t in times],
            "configuration_sha256": "c" * 64, "execution_model_sha256": "d" * 64,
            "curve": [{"timestamp": t.isoformat()} for t in times],
            "metrics": {"net_total_return": "0.1", "net_returns": [str(v) for v in a]}}
        for i, at in enumerate(times):
            self.registry.register(str(i), stamp(at), {"test_fixture": i}, at, "PROSPECTIVE")
        protocol = GraduationProtocol("b" * 40, "a" * 64, ("a", "b"), "a", "test_model", True, True,
            "c" * 64, "d" * 64,
            block_length=3, bootstrap_runs=100)
        return backtest, protocol, matrix, pd.DatetimeIndex(matrix.index + pd.Timedelta(minutes=5))

    def test_supported_gate_journals_certificate_and_statistics(self):
        backtest, protocol, returns, ends = self.fixture()
        result = GraduationPipeline(self.journal).evaluate(backtest, protocol, returns, ends, self.registry, T)
        self.assertTrue(result["paper_allowed"], result["reasons"])
        self.assertGreater(result["diagnostics"]["dsr_per_period"]["a"], .95)
        self.assertEqual(self.journal.restore("graduation"), result)
        self.assertFalse(result["live_execution"])

    def test_synthetic_or_dirty_first_seen_cannot_graduate(self):
        backtest, protocol, returns, ends = self.fixture()
        backtest["purpose"] = "SYNTHETIC_ENGINEERING"
        result = GraduationPipeline(self.journal).evaluate(backtest, protocol, returns, ends, self.registry, T)
        self.assertFalse(result["paper_allowed"])
        self.assertIn("SYNTHETIC_CANNOT_GRADUATE", result["reasons"])

    def test_short_data_protocol_flags_trial_inventory_and_path_tampering(self):
        backtest, protocol, returns, ends = self.fixture(10)
        result = GraduationPipeline(self.journal).evaluate(backtest, protocol, returns, ends, self.registry, T)
        self.assertFalse(result["paper_allowed"])
        self.assertTrue(any("VALIDATION_SUPPORT" in r for r in result["reasons"]))
        protocol = replace(protocol, oos_locked=False)
        returns.iloc[0, 0] = 99
        result = GraduationPipeline(self.journal).evaluate(backtest, protocol, returns, ends, self.registry, T)
        self.assertIn("OOS_OR_EXECUTION_PROTOCOL_UNVERIFIED", result["reasons"])
        self.assertIn("SELECTED_TRIAL_DOES_NOT_MATCH_SIMULATOR_RETURNS", result["reasons"])

    def test_first_seen_coverage_and_negative_results_block_graduation(self):
        backtest, protocol, returns, ends = self.fixture()
        backtest["source_event_times"].append((T + timedelta(days=10)).isoformat())
        backtest["metrics"]["net_total_return"] = "-0.01"
        result = GraduationPipeline(self.journal).evaluate(backtest, protocol, returns, ends, self.registry, T)
        self.assertIn("FIRST_SEEN_NOT_CLEAN", result["reasons"])
        self.assertIn("NONPOSITIVE_NET_ECONOMIC_PERFORMANCE", result["reasons"])

    def test_certificate_binds_execution_and_strategy_configuration(self):
        backtest, protocol, returns, ends = self.fixture()
        result = GraduationPipeline(self.journal).evaluate(backtest, replace(protocol, configuration_sha256="e" * 64),
            returns, ends, self.registry, T)
        self.assertIn("FROZEN_CONFIGURATION_MISMATCH", result["reasons"])
        policy = CompletionPolicy(purpose="GRADUATED_RESEARCH")
        bundle = build_bundle()
        configuration = sha256(canonical_json({"strategies": bundle.configuration(),
            "execution_model_sha256": policy.execution_model_sha256(),
            "admission": {"admission": None, "portfolio_limits":
                {k: str(v) for k, v in PortfolioLimits().__dict__.items()}}}).encode()).hexdigest()
        self.journal.checkpoint("graduation", {"paper_allowed": True, "eligible_strategy_ids": ["test_model"],
            "configuration_sha256": configuration, "execution_model_sha256": policy.execution_model_sha256()})
        runner = BacktestRunner(self.journal, bundle, {"USDT": D(1000), "IRT": D(1000), "BTC": D(1)}, policy, "IRT")
        self.assertEqual(runner.configuration_sha256(), configuration)
        with self.assertRaisesRegex(ValueError, "configuration"):
            BacktestRunner(self.journal, bundle, {"USDT": D(1000), "IRT": D(1000), "BTC": D(1)}, policy, "IRT",
                portfolio_limits=PortfolioLimits(max_asset_weight=D("0.10")))
        with self.assertRaisesRegex(ValueError, "fresh prospective"):
            runner.step(replace(synthetic_frames(1)[0], data_class="HISTORICAL"))
        with self.assertRaisesRegex(ValueError, "verified clean"):
            runner.step(replace(synthetic_frames(1)[0], data_class="PROSPECTIVE"))
        self.assertEqual(self.registry.report()["count"], 121)  # Failed paper frame rolled back.
        changed = build_bundle()
        changed.arbitrage_budget = D(99)
        with self.assertRaisesRegex(ValueError, "configuration"):
            BacktestRunner(self.journal, changed, {"USDT": D(1000), "IRT": D(1000), "BTC": D(1)}, policy, "IRT")
        with self.assertRaisesRegex(ValueError, "execution model"):
            EventDrivenExecutionSimulator({"USDT": D(100)}, self.journal, replace(policy, fee_rate=D("0.002")))


if __name__ == "__main__":
    unittest.main()
