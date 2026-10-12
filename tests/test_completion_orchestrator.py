from decimal import Decimal
import unittest

from research_bot.completion.journal import TradeJournal
from research_bot.completion.models import CompletionPolicy
from research_bot.orchestrator import ResearchTradingOrchestrator
from scripts.run_completion_paper import build_bundle, synthetic_frames


class CompletionOrchestratorTests(unittest.TestCase):
    def test_explicit_composition_records_independent_paper_events(self):
        journal = TradeJournal(":memory:", "orchestrator_completion_test")
        try:
            runner = ResearchTradingOrchestrator().create_completion_runner(
                journal=journal, strategies=build_bundle(),
                balances={"USDT": Decimal(1000), "IRT": Decimal(1000), "BTC": Decimal(1)},
                policy=CompletionPolicy(), quote_currency="IRT")
            result = runner.run(synthetic_frames(4))
            self.assertEqual(result["mode"], "PAPER")
            self.assertFalse(result["live_execution"])
            self.assertTrue(journal.events("HYPOTHESIS"))
            self.assertTrue(journal.events("ARBITRATION"))
            self.assertTrue(journal.events("ORDER_TRANSITION"))
            self.assertFalse(result["first_seen"]["prospective_clean"])
        finally:
            journal.close()
