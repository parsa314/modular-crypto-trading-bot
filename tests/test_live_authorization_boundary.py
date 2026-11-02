"""Synthetic authorization-boundary tests; no broker or credentials."""
from __future__ import annotations

import unittest
from research_bot.live_readiness import (
    LiveProductState, LiveReadinessDecision, ReadinessStage,
    assert_live_execution_authorized, current_live_product_state,
)


class LiveAuthorizationBoundaryTests(unittest.TestCase):
    def test_product_states_are_distinct_and_current_capability_is_unimplemented(self):
        self.assertEqual(
            {state.value for state in LiveProductState},
            {"LIVE_NOT_IMPLEMENTED", "LIVE_IMPLEMENTED_DISABLED", "LIVE_READ_ONLY_VERIFIED",
             "LIVE_EXECUTION_TESTED", "LIVE_FINANCIAL_GATE_PASSED", "LIVE_RISK_GATE_PASSED",
             "LIVE_OPERATOR_APPROVAL_REQUIRED", "LIVE_AUTHORIZED", "LIVE_HALTED"},
        )
        self.assertEqual(current_live_product_state(), LiveProductState.LIVE_NOT_IMPLEMENTED)

    def test_forged_boolean_is_not_an_operator_authorization(self):
        forged = LiveReadinessDecision(
            stage=ReadinessStage.LIVE_REVIEW_CANDIDATE,
            testnet_review_eligible=True,
            live_review_eligible=True,
            live_execution_authorized=True,
            missing_gates=(),
        )
        with self.assertRaisesRegex(RuntimeError, "LIVE_EXECUTION_NOT_AUTHORIZED"):
            assert_live_execution_authorized(forged)


if __name__ == "__main__":
    unittest.main()
