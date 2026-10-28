from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .contracts import Decision, EvidenceStamp, SignalEvidence
from .decision import DecisionEngine
from .execution import ExecutionFill, ExecutionRequest, OrderSide, PaperExecutionEngine
from .risk import RiskDecision, RiskEngine, RiskSnapshot
from .operational_risk import BrokerRiskContext, OperationalRiskEngine


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

    @classmethod
    def for_recovery_spot_h4(cls) -> ResearchTradingOrchestrator:
        """Opt-in engineering path; returns simulated fills, never broker orders."""
        return cls(risk_engine=OperationalRiskEngine())

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
        operational_context: BrokerRiskContext | None = None,
        stop_price: float | None = None,
        roundtrip_cost_bps: float = 50.0,
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

        side = OrderSide.BUY if signal.decision is Decision.BUY_CANDIDATE else OrderSide.SELL
        if isinstance(self.risk_engine, OperationalRiskEngine):
            risk = self.risk_engine.evaluate_order(
                risk_snapshot, symbol=symbol, side=side.value, quantity=quantity,
                price=reference_price, now=timestamp, context=operational_context,
                stop_price=stop_price, roundtrip_cost_bps=roundtrip_cost_bps,
            )
        else:
            risk = self.risk_engine.evaluate(risk_snapshot)
        if not risk.approved:
            return PipelineOutcome(signal=signal, risk=risk, fill=None, status="RISK_REJECTED")

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
