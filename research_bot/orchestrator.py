from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .contracts import Decision, EvidenceStamp, SignalEvidence
from .decision import DecisionEngine
from .execution import ExecutionFill, ExecutionRequest, OrderSide, PaperExecutionEngine
from .risk import RiskDecision, RiskEngine, RiskSnapshot
from .jamalian_data import available
from .hypothesis import HypothesisCandidate
from .hypothesis_adapter import evaluate_hypothesis


@dataclass(frozen=True)
class PipelineOutcome:
    signal: SignalEvidence
    risk: RiskDecision | None
    fill: ExecutionFill | None
    status: str


class ResearchTradingOrchestrator:
    """Evidence -> decision -> independent risk -> guarded execution.

    Models provide forecasts; they do not directly send orders.  This class is
    intentionally agnostic to XGBoost/LSTM/RL so every model is evaluated under
    the same downstream costs and risk rules.
    """

    def __init__(
        self,
        *,
        decision_engine: DecisionEngine | None = None,
        risk_engine: RiskEngine | None = None,
        execution_engine: PaperExecutionEngine | None = None,
    ) -> None:
        self.decision_engine = decision_engine or DecisionEngine()
        self.risk_engine = risk_engine or RiskEngine()
        self.execution_engine = execution_engine or PaperExecutionEngine()

    def process_candidate(
        self, candidate: HypothesisCandidate, *, risk_snapshot: RiskSnapshot | None = None,
    ) -> PipelineOutcome:
        """Phase-one hypothesis -> existing decision engine -> research abstention."""
        signal = evaluate_hypothesis(candidate, self.decision_engine)
        return self.process_hypothesis(signal, risk_snapshot=risk_snapshot)

    def process_hypothesis(
        self, signal: SignalEvidence, *, risk_snapshot: RiskSnapshot | None = None,
    ) -> PipelineOutcome:
        """Inspect an unvalidated research hypothesis without invoking execution.

        Forecast promotion is a separate experiment. Candidate direction and
        scenario profits must never be passed to ``process_forecast`` as a
        calibrated expected return.
        """
        if (signal.decision is not Decision.NO_TRADE or signal.confidence != 0.0
                or any(value != 0.0 for value in (signal.expected_return, signal.expected_cost,
                    signal.risk_penalty, signal.uncertainty_penalty))
                or signal.metadata.get("research_status") != "HYPOTHESIS"
                or signal.metadata.get("execution_authorized") is not False
                or signal.metadata.get("eligible_for_v51") is not False
                or signal.metadata.get("forecast_available") is not False
                or not signal.evidence):
            raise ValueError("unvalidated hypothesis must remain non-actionable")
        for stamp in signal.evidence:
            available(stamp, signal.timestamp)
        risk = self.risk_engine.evaluate(risk_snapshot) if risk_snapshot is not None else None
        return PipelineOutcome(signal=signal, risk=risk, fill=None,
            status="RESEARCH_ONLY_UNVALIDATED_HYPOTHESIS")

    def process_forecast(
        self,
        *,
        asset: str,
        symbol: str,
        timestamp: datetime,
        expected_return: float,
        expected_cost: float,
        risk_penalty: float,
        uncertainty_penalty: float,
        confidence: float,
        currently_long: bool,
        risk_snapshot: RiskSnapshot,
        reference_price: float,
        quantity: float,
        client_order_id: str,
        evidence: tuple[EvidenceStamp, ...] = (),
        metadata: dict | None = None,
    ) -> PipelineOutcome:
        signal = self.decision_engine.evaluate(
            asset=asset,
            timestamp=timestamp,
            expected_return=expected_return,
            expected_cost=expected_cost,
            risk_penalty=risk_penalty,
            uncertainty_penalty=uncertainty_penalty,
            confidence=confidence,
            currently_long=currently_long,
            evidence=evidence,
            metadata=metadata,
        )

        actionable = signal.decision in {Decision.BUY_CANDIDATE, Decision.EXIT}
        if not actionable:
            return PipelineOutcome(signal=signal, risk=None, fill=None, status="ABSTAINED")

        risk = self.risk_engine.evaluate(risk_snapshot)
        if not risk.approved:
            return PipelineOutcome(signal=signal, risk=risk, fill=None, status="RISK_REJECTED")

        side = OrderSide.BUY if signal.decision is Decision.BUY_CANDIDATE else OrderSide.SELL
        fill = self.execution_engine.execute(
            ExecutionRequest(
                client_order_id=client_order_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                reference_price=reference_price,
                created_at=timestamp,
            )
        )
        return PipelineOutcome(signal=signal, risk=risk, fill=fill, status="EXECUTED_PAPER")
