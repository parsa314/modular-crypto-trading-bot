"""Deterministic phase-one NO_TRADE classification; no mocked profitability."""
from __future__ import annotations

import unittest

from research_bot.decision_trace import phase_one_trace


def record(matched: bool) -> dict:
    return {
        "pipeline_status": "RESEARCH_ONLY_UNVALIDATED_HYPOTHESIS",
        "execution_authorized": False,
        "risk": None,
        "signal": {
            "decision": "NO_TRADE",
            "reasons": ["CONFIDENCE_BELOW_THRESHOLD"],
            "metadata": {
                "research_status": "HYPOTHESIS",
                "forecast_available": False,
                "execution_authorized": False,
                "candidate": matched,
            },
        },
    }


class DecisionTraceTests(unittest.TestCase):
    def test_matched_rule_is_not_mislabeled_as_no_market_opportunity(self):
        result = phase_one_trace(record(True))
        self.assertEqual(result["primary_reason"], "HYPOTHESIS_ONLY_NO_FORECAST")
        self.assertEqual(result["engine_reasons"], ("CONFIDENCE_BELOW_THRESHOLD",))
        self.assertEqual(result["risk_status"], "NOT_EVALUATED")
        self.assertNotIn("RISK_VETO", result["contributing_reasons"])
        self.assertIn("EXECUTION_NOT_AUTHORIZED", result["contributing_reasons"])

    def test_unmatched_rule_is_distinguished_from_model_abstention(self):
        result = phase_one_trace(record(False))
        self.assertEqual(result["primary_reason"], "STRATEGY_RULE_NOT_MATCHED")
        self.assertIn("HYPOTHESIS_ONLY_NO_FORECAST", result["contributing_reasons"])

    def test_unsafe_synthetic_decision_is_rejected(self):
        for path, value in [
            ("forecast_available", True),
            ("execution_authorized", True),
            ("candidate", None),
        ]:
            with self.subTest(key=path):
                sample = record(True)
                sample["signal"]["metadata"][path] = value
                with self.assertRaises(ValueError):
                    phase_one_trace(sample)
        sample = record(True)
        sample["signal"]["decision"] = "BUY_CANDIDATE"
        with self.assertRaises(ValueError):
            phase_one_trace(sample)
        sample = record(True)
        sample["pipeline_status"] = "EXECUTED_PAPER"
        with self.assertRaises(ValueError):
            phase_one_trace(sample)


if __name__ == "__main__":
    unittest.main()
