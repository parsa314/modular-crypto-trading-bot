"""V3 compatibility, PIT, immutable identity and offline resource invariants."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import ast
import json
from pathlib import Path
import tempfile
import unittest
import random

from research_bot.contracts import Decision, EvidenceStamp
from research_bot.decision import DecisionEngine, DecisionThresholds
from research_bot.hypothesis import HypothesisCandidate, HypothesisFamily, HypothesisSide
from research_bot.hypothesis_adapter import evaluate_hypothesis
from research_bot.jamalian_data import Candle
from research_bot.jamalian_phase_one import JamalianPhaseOneResearch
from research_bot.operational_event_ledger import OperationalResearchEventLedger
from research_bot.orchestrator import ResearchTradingOrchestrator
from research_bot.portfolio_conflict import (
    ArbiterConfig, CapitalProposal, PortfolioState, PortfolioConflictArbiter,
)
from research_bot.strategies.baselines import (
    MACrossParams, RSIParams, RsiMaParams, MovingAverageCrossoverHypothesis,
    RSIMeanReversionHypothesis, RsiMaConfluenceHypothesis,
)
from scripts.run_jamalian_research import run
from research_bot.exchanges.bitpin_adapter import BitpinAdapter, WriteDisabled


T = datetime(2026, 10, 11, tzinfo=timezone.utc)


def candidate(symbol="BTC_USDT", at=T, metadata=None):
    return HypothesisCandidate("JAMALIAN_MA_CROSS_V3", HypothesisFamily.TECHNICAL_BASELINE,
        symbol, HypothesisSide.LONG, at, at + timedelta(seconds=1), ("fixture",),
        (EvidenceStamp("fixture", at, at, lineage="a" * 64),), {"window": 3},
        metadata or {"test": True})


def history(prices):
    return [Candle("BTC_USDT", D(str(p)), EvidenceStamp("fixture", T + timedelta(hours=i),
        T + timedelta(hours=i), lineage="b" * 64)) for i, p in enumerate(prices)]


def state(capital="100", equity="100", exposure="0"):
    return PortfolioState("USDT", D(capital), D(equity), D(exposure), (), (),
        EvidenceStamp("fixture_capital", T, T))


def proposal(symbol, amount="60", at=T):
    return CapitalProposal(candidate(symbol, at), D(amount), D(amount))


class ContractAndBaselineTests(unittest.TestCase):
    def test_nested_metadata_is_detached_and_immutable(self):
        original = {"nested": {"list": [1, 2]}}
        h = candidate(metadata=original)
        identity = h.hypothesis_id
        original["nested"]["list"].append(3)
        self.assertEqual(h.metadata["nested"]["list"], (1, 2))
        with self.assertRaises(TypeError):
            h.metadata["nested"]["new"] = 2
        h.to_dict()["metadata"]["nested"]["list"].append(4)
        self.assertEqual(h.hypothesis_id, identity)

    def test_identity_includes_params_provenance_and_explicit_time(self):
        h = candidate()
        self.assertEqual(h.hypothesis_id, candidate().hypothesis_id)
        self.assertNotEqual(h.hypothesis_id, replace(h, parameters={"window": 4}).hypothesis_id)
        stamp = replace(h.evidence[0], lineage="c" * 64)
        self.assertNotEqual(h.hypothesis_id, replace(h, evidence=(stamp,)).hypothesis_id)
        shifted = h.timestamp.astimezone(timezone(timedelta(hours=3)))
        self.assertEqual(h.hypothesis_id, replace(h, timestamp=shifted).hypothesis_id)

    def test_invalid_time_evidence_and_numeric_metadata_rejected(self):
        h = candidate()
        for changes in ({"timestamp": T.replace(tzinfo=None)}, {"earliest_executable": T},
                        {"evidence": ()}, {"metadata": {"x": float("nan")}},
                        {"evidence": (EvidenceStamp("late", T, T + timedelta(seconds=1)),)}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(h, **changes)

    def test_adapter_abstains_even_if_metadata_contains_invented_forecast(self):
        h = candidate(metadata={"expected_return": 999, "confidence": 1})
        signal = evaluate_hypothesis(h)
        self.assertEqual(signal.decision, Decision.NO_TRADE)
        self.assertEqual(signal.expected_return, 0)
        self.assertEqual(signal.confidence, 0)
        self.assertIs(signal.metadata["forecast_available"], False)

    def test_unsafe_decision_threshold_override_fails_closed(self):
        engine = DecisionEngine(DecisionThresholds(min_confidence=0, min_net_alpha=0))
        with self.assertRaises(ValueError):
            evaluate_hypothesis(candidate(), engine)

    def test_generator_params_are_checked(self):
        for cls, kwargs in [(MACrossParams, {"fast": 30, "slow": 10}),
                            (RSIParams, {"period": 0}), (RSIParams, {"oversold": "100"}),
                            (RsiMaParams, {"ma_period": True}),
                            (MACrossParams, {"execution_delay": timedelta(0)})]:
            with self.subTest(cls=cls), self.assertRaises(ValueError):
                cls(**kwargs)

    def test_ma_actual_crossover_and_non_cross(self):
        generator = MovingAverageCrossoverHypothesis(MACrossParams(fast=2, slow=3, min_bars=4))
        prices = history([100, 100, 100, 101])
        result = generator.generate(prices, prices[-1].stamp.available_at)[0]
        self.assertIs(result.side, HypothesisSide.LONG)
        next_prices = history([100, 100, 100, 101, 102])
        self.assertIs(generator.generate(next_prices, next_prices[-1].stamp.available_at)[0].side,
                      HypothesisSide.NEUTRAL)

    def test_rsi_flat_and_falling_reference(self):
        generator = RSIMeanReversionHypothesis(RSIParams(period=2, min_bars=3))
        for prices, expected, side in [([100, 100, 100], "50", HypothesisSide.NEUTRAL),
                                       ([103, 102, 101], "0", HypothesisSide.LONG)]:
            result = generator.generate(history(prices), T + timedelta(hours=2))[0]
            self.assertEqual(result.metadata["rsi"], expected)
            self.assertIs(result.side, side)

    def test_rsi_ma_course_confluence_uses_different_windows(self):
        generator = RsiMaConfluenceHypothesis(RsiMaParams(rsi_period=2, ma_period=5, min_bars=5))
        prices = history([1, 1, 10, 9, 8])
        result = generator.generate(prices, T + timedelta(hours=4))[0]
        self.assertIs(result.side, HypothesisSide.LONG)
        self.assertEqual(result.metadata["ma"], "5.8")
        self.assertEqual(result.metadata["rsi"], "0")

    def test_all_baselines_require_warmup_and_ignore_future_bars(self):
        past = history([100] * 40)
        at = T + timedelta(hours=39)
        for generator in (MovingAverageCrossoverHypothesis(), RSIMeanReversionHypothesis(),
                          RsiMaConfluenceHypothesis()):
            with self.subTest(generator=type(generator).__name__):
                self.assertEqual(generator.generate(past[:2], T + timedelta(hours=1)), [])
                first = generator.generate(past, at)
                extended = history([100] * 40 + [10000])
                self.assertEqual(first, generator.generate(extended, at))
                self.assertIsInstance(first[0], HypothesisCandidate)
                self.assertFalse(hasattr(first[0], "decision"))

    def test_changed_old_input_close_changes_trial_identity(self):
        prices = history([100] * 40)
        generator = MovingAverageCrossoverHypothesis()
        first = generator.generate(prices, T + timedelta(hours=39))[0]
        prices[0] = replace(prices[0], close=D("99"))
        changed = generator.generate(prices, T + timedelta(hours=39))[0]
        self.assertNotEqual(first.hypothesis_id, changed.hypothesis_id)

    def test_baselines_reject_gaps_staleness_and_duplicates(self):
        prices = history([100] * 40)
        for generator in (MovingAverageCrossoverHypothesis(), RSIMeanReversionHypothesis(),
                          RsiMaConfluenceHypothesis()):
            for bars, at in [(prices[:-2] + prices[-1:], T + timedelta(hours=39)),
                             (prices + prices[-1:], T + timedelta(hours=39)),
                             (prices, T + timedelta(days=8))]:
                with self.subTest(generator=type(generator).__name__), self.assertRaises(ValueError):
                    generator.generate(bars, at)


class OperationalEventTests(unittest.TestCase):
    def test_durable_restart_and_two_writers_deduplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            left = OperationalResearchEventLedger("jam_test", path)
            right = OperationalResearchEventLedger("jam_test", path)
            args = dict(event_id="one", kind="HYPOTHESIS", timestamp=T, payload={"a": 1})
            row = left.append(**args)
            self.assertEqual(right.append(**args), row)
            right.append(event_id="two", kind="DECISION", timestamp=T, payload={"decision": "NO_TRADE"})
            export = left.export()
            self.assertEqual(len(export["records"]), 2)
            self.assertEqual(export["records"][1]["previous_sha256"], row["event_sha256"])
            restarted = OperationalResearchEventLedger("jam_test", path)
            self.assertEqual(restarted.export(), export)

    def test_tamper_incomplete_line_and_wrong_namespace_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            ledger = OperationalResearchEventLedger("jam_test", path)
            ledger.append(event_id="one", kind="HYPOTHESIS", timestamp=T, payload={"a": 1})
            saved = path.read_text()
            with self.assertRaises(ValueError):
                OperationalResearchEventLedger("other", path)
            path.write_text(saved.replace('"a":1', '"a":2'))
            with self.assertRaises(ValueError):
                OperationalResearchEventLedger("jam_test", path)
            path.write_text(saved.rstrip("\n"))
            with self.assertRaises(ValueError):
                OperationalResearchEventLedger("jam_test", path)

    def test_conflicting_retries_time_regression_and_nan_rejected(self):
        ledger = OperationalResearchEventLedger("jam_test")
        ledger.append(event_id="one", kind="HYPOTHESIS", timestamp=T, payload={"a": 1})
        for event_id, at, payload in [("one", T, {"a": 2}),
                                      ("two", T - timedelta(seconds=1), {}),
                                      ("two", T, {"a": float("nan")})]:
            with self.subTest(event_id=event_id), self.assertRaises(ValueError):
                ledger.append(event_id=event_id, kind="HYPOTHESIS", timestamp=at, payload=payload)
        exported = ledger.export()
        exported["records"][0]["payload"]["a"] = 9
        self.assertEqual(ledger.export()["records"][0]["payload"]["a"], 1)

    def test_phase_one_pipeline_records_real_abstention_without_execution(self):
        class ForbiddenExecution:
            def execute(self, *args, **kwargs):
                raise AssertionError("execution forbidden")
        ledger = OperationalResearchEventLedger("jam_test")
        pipeline = JamalianPhaseOneResearch(ledger,
            ResearchTradingOrchestrator(execution_engine=ForbiddenExecution()))
        h = candidate()
        outcome = pipeline.record(h)
        pipeline.record(h)
        self.assertIsNone(outcome.fill)
        self.assertEqual(outcome.signal.decision, Decision.NO_TRADE)
        self.assertEqual(len(ledger.export()["records"]), 2)
        with self.assertRaises(ValueError):
            pipeline.record(replace(h, strategy_id="JAMALIAN_GRID_V1"))

    def test_cli_all_three_baselines_have_separate_operational_events(self):
        prices = history([100] * 40)
        document = {"decision_time": (T + timedelta(hours=39)).isoformat(),
            "candles": [{"symbol": c.symbol, "close": str(c.close),
                "closed_at": c.stamp.observed_at.isoformat(),
                "available_at": c.stamp.available_at.isoformat(), "source": "SYNTHETIC_SMOKE"}
                for c in prices]}
        for strategy in ("ma_cross", "rsi", "rsi_ma"):
            result = run({**document, "strategy": strategy}, dataset_sha256="a" * 64)
            self.assertEqual(len(result["event_ledger"]["records"]), 2)
            self.assertEqual(result["records"][0]["signal"]["decision"], "NO_TRADE")
            self.assertEqual(result["advanced_research_gate"], "WITHHELD_PENDING_BASELINE_PROTOCOL")
        for forbidden in ("grid", "triangle", "macd_rsi_ema"):
            with self.assertRaises(ValueError):
                run({**document, "strategy": forbidden}, dataset_sha256="a" * 64)


class PortfolioInvariantTests(unittest.TestCase):
    def setUp(self):
        self.arbiter = PortfolioConflictArbiter(ArbiterConfig("jam_resources_test"))
        self.at = T + timedelta(seconds=2)

    def test_combined_reservations_cannot_overspend(self):
        proposals = (proposal("BTC_USDT"), proposal("ETH_USDT"))
        result = self.arbiter.resolve(proposals, state(), self.at)
        self.assertEqual(len(result.admitted), 1)
        self.assertEqual(result.state.available_capital, D("40"))
        self.assertEqual(result.rejected[0][1], "INSUFFICIENT_AVAILABLE_CAPITAL")
        self.assertEqual(state().available_capital, D("100"))
        self.assertFalse(result.execution_authorized)

    def test_capital_invariants_over_multiple_contention_scenarios(self):
        rng = random.Random(314)
        arbiter = PortfolioConflictArbiter(ArbiterConfig("jam_random_resource_test", max_concurrent_positions=50))
        for _ in range(30):
            capital = str(rng.randint(1, 100))
            proposals = tuple(proposal(f"COIN{i}_USDT", str(rng.randint(1, 30))) for i in range(20))
            result = arbiter.resolve(proposals, state(capital, "100"), self.at)
            total = sum((p.amount for p in result.admitted), D(0))
            self.assertLessEqual(total, D(capital))
            self.assertEqual(result.state.available_capital + total, D(capital))
            self.assertGreaterEqual(result.state.available_capital, 0)
            self.assertEqual(len(result.admitted) + len(result.rejected), len(proposals))

    def test_available_capital_already_excludes_reservations(self):
        result = self.arbiter.resolve((proposal("BTC_USDT", "40"), proposal("ETH_USDT", "40")),
                                      state("50", "100", "50"), self.at)
        self.assertEqual(len(result.admitted), 1)
        self.assertEqual(result.state.available_capital, D("10"))
        self.assertEqual(result.state.open_gross_exposure, D("90"))

    def test_rank_before_position_filter_and_input_order_invariant(self):
        arbiter = PortfolioConflictArbiter(ArbiterConfig("jam_test", max_concurrent_positions=1))
        earlier, later = proposal("BTC_USDT", "10"), proposal("ETH_USDT", "10", T + timedelta(seconds=1))
        left = arbiter.resolve((later, earlier), state(), T + timedelta(seconds=3))
        right = arbiter.resolve((earlier, later), state(), T + timedelta(seconds=3))
        self.assertEqual(left, right)
        self.assertEqual(left.admitted, (earlier,))

    def test_duplicate_symbol_and_reused_state_reject_second_reservation(self):
        p = proposal("BTC_USDT", "10")
        other = replace(p, candidate=replace(p.candidate, parameters={"window": 4}))
        result = self.arbiter.resolve((p, other), state(), self.at)
        self.assertEqual(len(result.admitted), 1)
        self.assertEqual(result.rejected[0][1], "SYMBOL_CONFLICT")
        repeated = self.arbiter.resolve(result.admitted, result.state, self.at)
        self.assertEqual(repeated.admitted, ())
        self.assertEqual(repeated.rejected[0][1], "ALREADY_RESERVED")

    def test_future_neutral_and_exposure_limit(self):
        p = proposal("BTC_USDT", "60")
        future = self.arbiter.resolve((p,), state(), T)
        self.assertEqual(future.rejected[0][1], "NOT_YET_ADMISSIBLE")
        neutral = replace(p, candidate=replace(p.candidate, side=HypothesisSide.NEUTRAL))
        self.assertEqual(self.arbiter.resolve((neutral,), state(), self.at).rejected[0][1],
                         "NO_LONG_BASELINE_CANDIDATE")
        exposure = self.arbiter.resolve((p,), state("100", "100", "50"), self.at)
        self.assertEqual(exposure.rejected[0][1], "GROSS_EXPOSURE_LIMIT")

    def test_invalid_negative_nonfinite_currency_and_duplicate_inputs(self):
        p = proposal("BTC_USDT")
        with self.assertRaises(ValueError):
            CapitalProposal(p.candidate, D("-1"), D("1"))
        with self.assertRaises(ValueError):
            CapitalProposal(p.candidate, D("NaN"), D("1"))
        with self.assertRaises(ValueError):
            self.arbiter.resolve((proposal("BTC_IRT"),), state(), self.at)
        with self.assertRaises(ValueError):
            self.arbiter.resolve((p, p), state(), self.at)

    def test_no_import_or_mutation_of_frozen_scientific_modules(self):
        root = Path(__file__).resolve().parents[1]
        for relative in ("research_bot/portfolio_conflict.py", "research_bot/operational_event_ledger.py",
                         "research_bot/jamalian_phase_one.py"):
            tree = ast.parse((root / relative).read_text())
            modules = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
            modules += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
            self.assertFalse(any("overlap_arbitration_v51" in m or "evidence_ledger_v17" in m for m in modules))


class ExplicitBitpinHostTests(unittest.TestCase):
    def test_host_is_explicit_and_writes_still_block_before_transport(self):
        class Transport:
            def request(self, method, url, **kwargs):
                from research_bot.exchanges.bitpin_adapter import HttpResponse
                self.url = url
                return HttpResponse(200, b'[{"symbol":"BTC_USDT","price":"100"}]', {})
        transport = Transport()
        adapter = BitpinAdapter(transport=transport, base_url="https://api.bitpin.org", clock=lambda: T)
        adapter.get_all_prices()
        self.assertTrue(transport.url.startswith("https://api.bitpin.org/api/v1/"))
        with self.assertRaises(WriteDisabled):
            adapter.place_market_order("BTC_USDT")
        for host in ("http://api.bitpin.org", "https://api.bitpin.org/", "https://other-host"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                BitpinAdapter(base_url=host)


if __name__ == "__main__":
    unittest.main()
