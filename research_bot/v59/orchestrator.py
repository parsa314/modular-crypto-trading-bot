from __future__ import annotations

from datetime import datetime

from .config import V59Config
from .contracts import (
    FinalResearchDecision,
    GateStatus,
    ModelPrediction,
    PortfolioState,
    SignalCandidate,
    UncertaintyAssessment,
)
from .decision import economic_gate
from .evidence import EvidenceLedger
from .finance import financial_gate
from .hashing import stable_hash


class V59DecisionOrchestrator:
    """One-way strategy -> AI -> uncertainty -> economics -> finance pipeline."""

    def __init__(self, config: V59Config | None = None) -> None:
        self.config = V59Config() if config is None else config
        if not isinstance(self.config, V59Config):
            raise ValueError("config must be V59Config")
        self.ledger = EvidenceLedger()
        self._seen_events: set[str] = set()

    def evaluate(
        self,
        *,
        candidate: SignalCandidate,
        prediction: ModelPrediction,
        uncertainty: UncertaintyAssessment,
        portfolio: PortfolioState,
    ) -> FinalResearchDecision:
        if candidate.event_id in self._seen_events:
            raise RuntimeError("duplicate event_id cannot be evaluated twice")
        self._seen_events.add(candidate.event_id)

        self.ledger.append(
            record_type="SIGNAL_CANDIDATE",
            payload=candidate,
            recorded_at=candidate.decision_at,
        )
        self.ledger.append(
            record_type="MODEL_PREDICTION",
            payload=prediction,
            recorded_at=prediction.available_at,
        )
        self.ledger.append(
            record_type="UNCERTAINTY_ASSESSMENT",
            payload=uncertainty,
            recorded_at=candidate.decision_at,
        )

        economics = economic_gate(candidate, prediction, uncertainty, self.config)
        self.ledger.append(
            record_type="ECONOMIC_DECISION",
            payload=economics,
            recorded_at=candidate.decision_at,
        )

        finance = financial_gate(candidate, economics, portfolio, self.config)
        self.ledger.append(
            record_type="FINANCIAL_DECISION",
            payload=finance,
            recorded_at=candidate.entry_time,
        )

        if finance.status is GateStatus.PASS:
            status = GateStatus.PASS
            action = "ADMITTED_RESEARCH_SIMULATION"
            reason = "ALL_MANDATORY_GATES_PASSED"
            approved = finance.approved_notional
        else:
            status = GateStatus.VETO if finance.status is GateStatus.VETO else GateStatus.ABSTAIN
            action = "NO_TRADE"
            reason = finance.reason
            approved = 0.0

        audit_hash = stable_hash(
            {
                "event_id": candidate.event_id,
                "candidate_identity": candidate.identity_hash,
                "economic_status": economics.status.value,
                "financial_status": finance.status.value,
                "ledger_hash_before_final": self.ledger.ledger_hash,
                "action": action,
                "approved_notional": approved,
            }
        )
        final = FinalResearchDecision(
            event_id=candidate.event_id,
            status=status,
            action=action,
            approved_notional=float(approved),
            reason=reason,
            audit_hash=audit_hash,
        )
        self.ledger.append(
            record_type="FINAL_RESEARCH_DECISION",
            payload=final,
            recorded_at=candidate.entry_time,
        )
        if not self.ledger.verify():
            raise RuntimeError("evidence ledger integrity failure")
        return final
