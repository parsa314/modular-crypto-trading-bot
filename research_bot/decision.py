from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from .contracts import Decision, EvidenceStamp, SignalEvidence


@dataclass(frozen=True)
class DecisionThresholds:
    min_confidence: float = 0.58
    min_net_alpha: float = 0.0005
    exit_net_alpha: float = -0.0005


class DecisionEngine:
    """Convert forecasts into trade/no-trade decisions after costs and uncertainty.

    This implements the project's central research rule:
    Net Alpha = Expected Return - Expected Cost - Risk Penalty - Uncertainty Penalty.
    A high raw forecast is not actionable when net alpha is insufficient.
    """

    def __init__(self, thresholds: DecisionThresholds | None = None):
        self.thresholds = thresholds or DecisionThresholds()

    def evaluate(
        self,
        *,
        asset: str,
        timestamp: datetime,
        expected_return: float,
        expected_cost: float,
        risk_penalty: float,
        uncertainty_penalty: float,
        confidence: float,
        currently_long: bool = False,
        evidence: tuple[EvidenceStamp, ...] = (),
        metadata: dict | None = None,
    ) -> SignalEvidence:
        confidence = float(confidence)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")
        if timestamp.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        if not all(math.isfinite(float(value)) for value in
                   (expected_return, expected_cost, risk_penalty, uncertainty_penalty)):
            raise ValueError("NONFINITE_FORECAST")
        if any(float(value) < 0 for value in (expected_cost, risk_penalty, uncertainty_penalty)):
            raise ValueError("NEGATIVE_COST_OR_PENALTY")

        net_alpha = (
            float(expected_return)
            - float(expected_cost)
            - float(risk_penalty)
            - float(uncertainty_penalty)
        )
        reasons: list[str] = []

        unavailable = [e.source for e in evidence if not e.is_available(timestamp)]
        if unavailable:
            reasons.append("POINT_IN_TIME_EVIDENCE_NOT_AVAILABLE")
            decision = Decision.REJECT
        elif confidence < self.thresholds.min_confidence:
            reasons.append("CONFIDENCE_BELOW_THRESHOLD")
            decision = Decision.NO_TRADE if not currently_long else Decision.HOLD
        elif net_alpha >= self.thresholds.min_net_alpha:
            reasons.append("POSITIVE_NET_ALPHA")
            decision = Decision.BUY_CANDIDATE if not currently_long else Decision.HOLD
        elif currently_long and net_alpha <= self.thresholds.exit_net_alpha:
            reasons.append("NEGATIVE_NET_ALPHA_EXIT")
            decision = Decision.EXIT
        else:
            reasons.append("NET_ALPHA_INSUFFICIENT_AFTER_COST_RISK_UNCERTAINTY")
            decision = Decision.NO_TRADE if not currently_long else Decision.HOLD

        return SignalEvidence(
            asset=asset,
            timestamp=timestamp,
            expected_return=float(expected_return),
            expected_cost=float(expected_cost),
            risk_penalty=float(risk_penalty),
            uncertainty_penalty=float(uncertainty_penalty),
            confidence=confidence,
            decision=decision,
            reasons=tuple(reasons),
            evidence=evidence,
            metadata=metadata or {},
        )
