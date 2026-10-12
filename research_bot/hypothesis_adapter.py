"""Thin phase-one adapter. No calibration, positive forecast or authorization."""
from __future__ import annotations

from .contracts import Decision, SignalEvidence
from .decision import DecisionEngine
from .hypothesis import HypothesisCandidate, HypothesisSide


def evaluate_hypothesis(candidate: HypothesisCandidate,
                        engine: DecisionEngine | None = None) -> SignalEvidence:
    if not isinstance(candidate, HypothesisCandidate):
        raise TypeError("HypothesisCandidate required")
    metadata = {"hypothesis_id": candidate.strategy_id, "signal_id": candidate.hypothesis_id,
        "symbol": candidate.symbol, "candidate": candidate.side is HypothesisSide.LONG,
        "candidate_direction": candidate.side.value,
        "research_status": "HYPOTHESIS", "forecast_available": False,
        "cost_estimate_available": False, "execution_authorized": False,
        "eligible_for_v51": False, "phase": "PHASE_1_BASELINES",
        "candidate_record": candidate.to_dict()}
    # Zero is an abstention sentinel, not an estimate of return/cost. The absence
    # flags distinguish unavailable calibration from a calibrated zero forecast.
    signal = (engine or DecisionEngine()).evaluate(asset=candidate.symbol.split("_")[0],
        timestamp=candidate.timestamp, expected_return=0, expected_cost=0,
        risk_penalty=0, uncertainty_penalty=0, confidence=0,
        evidence=candidate.evidence, metadata=metadata)
    if signal.decision is not Decision.NO_TRADE:
        raise ValueError("decision configuration must abstain on uncalibrated hypotheses")
    return signal
