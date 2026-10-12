from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cognitive_core.bridge import DetectorEvent, Forecast

from .contracts import Decision, EvidenceStamp, SignalEvidence
from .decision import DecisionEngine
from .execution import ExecutionFill, ExecutionRequest, OrderSide, PaperExecutionEngine
from .risk import RiskDecision, RiskEngine, RiskSnapshot


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

    def process_detector_event(
        self, event: DetectorEvent, forecast: Forecast | None = None,
    ) -> PipelineOutcome:
        """Evaluate canonical research evidence without risk sizing or execution.

        Journal consumers may persist this outcome. BUY_CANDIDATE here is only
        the existing decision engine's fixture result, never an order permission.
        """
        from .cognitive_core.bridge import bridge_signal

        signal = bridge_signal(event, forecast, decision_engine=self.decision_engine)
        return PipelineOutcome(signal, None, None, "RESEARCH_EVIDENCE_ONLY")

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
