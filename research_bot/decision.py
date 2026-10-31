from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
import math

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

        inputs = (expected_return, expected_cost, risk_penalty, uncertainty_penalty)
        decimal_mode = any(isinstance(value, Decimal) for value in inputs)
        if decimal_mode:
            # float inputs here are model estimates, never restored monetary
            # precision. Conversion is explicit and rejects nonfinite outputs.
            def model_boundary(value):
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValueError("nonfinite model output")
                result = value if isinstance(value, Decimal) else Decimal(str(value))
                if not result.is_finite():
                    raise ValueError("nonfinite financial decision input")
                return result
            values = tuple(model_boundary(value) for value in inputs)
            with localcontext() as ctx:
                ctx.prec = 80
                net_alpha = values[0] - values[1] - values[2] - values[3]
            positive_threshold = Decimal(str(self.thresholds.min_net_alpha))
            exit_threshold = Decimal(str(self.thresholds.exit_net_alpha))
        else:
            values = tuple(float(value) for value in inputs)
            net_alpha = values[0] - values[1] - values[2] - values[3]
            positive_threshold = self.thresholds.min_net_alpha
            exit_threshold = self.thresholds.exit_net_alpha
        reasons: list[str] = []

        unavailable = [e.source for e in evidence if not e.is_available(timestamp)]
        if unavailable:
            reasons.append("POINT_IN_TIME_EVIDENCE_NOT_AVAILABLE")
            decision = Decision.REJECT
        elif confidence < self.thresholds.min_confidence:
            reasons.append("CONFIDENCE_BELOW_THRESHOLD")
            decision = Decision.NO_TRADE if not currently_long else Decision.HOLD
        elif net_alpha >= positive_threshold:
            reasons.append("POSITIVE_NET_ALPHA")
            decision = Decision.BUY_CANDIDATE if not currently_long else Decision.HOLD
        elif currently_long and net_alpha <= exit_threshold:
            reasons.append("NEGATIVE_NET_ALPHA_EXIT")
            decision = Decision.EXIT
        else:
            reasons.append("NET_ALPHA_INSUFFICIENT_AFTER_COST_RISK_UNCERTAINTY")
            decision = Decision.NO_TRADE if not currently_long else Decision.HOLD

        return SignalEvidence(
            asset=asset,
            timestamp=timestamp,
            expected_return=values[0],
            expected_cost=values[1],
            risk_penalty=values[2],
            uncertainty_penalty=values[3],
            confidence=confidence,
            decision=decision,
            reasons=tuple(reasons),
            evidence=evidence,
            metadata=metadata or {},
        )

