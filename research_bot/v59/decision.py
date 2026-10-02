from __future__ import annotations

import math

from .config import V59Config
from .contracts import (
    EconomicDecision,
    GateStatus,
    ModelPrediction,
    SignalCandidate,
    UncertaintyAssessment,
)


def economic_gate(
    candidate: SignalCandidate,
    prediction: ModelPrediction,
    uncertainty: UncertaintyAssessment,
    config: V59Config,
) -> EconomicDecision:
    if candidate.event_id != prediction.event_id or candidate.event_id != uncertainty.event_id:
        raise ValueError("event identity mismatch across strategy/model/uncertainty layers")
    if prediction.available_at > candidate.decision_at:
        raise ValueError("prediction was not available at decision time")
    if config.require_calibration and not prediction.calibrated:
        return EconomicDecision(
            candidate.event_id, GateStatus.ABSTAIN, -math.inf,
            config.round_trip_cost_bps, 0.0, 0.0, 0.0, 0.0,
            "UNCALIBRATED_PREDICTION",
        )
    if candidate.regime_confidence < config.min_regime_confidence:
        return EconomicDecision(
            candidate.event_id, GateStatus.ABSTAIN, -math.inf,
            config.round_trip_cost_bps, 0.0, 0.0, 0.0, 0.0,
            "LOW_REGIME_CONFIDENCE",
        )
    if prediction.entropy > config.max_entropy:
        return EconomicDecision(
            candidate.event_id, GateStatus.ABSTAIN, -math.inf,
            config.round_trip_cost_bps, 0.0, 0.0, 0.0, 0.0,
            "HIGH_ENTROPY",
        )
    if prediction.shift_score > config.max_shift_score:
        return EconomicDecision(
            candidate.event_id, GateStatus.ABSTAIN, -math.inf,
            config.round_trip_cost_bps, 0.0, 0.0, 0.0, 0.0,
            "DISTRIBUTION_SHIFT",
        )
    if uncertainty.abstain or len(uncertainty.prediction_set) > config.conformal_max_set_size:
        return EconomicDecision(
            candidate.event_id, GateStatus.ABSTAIN, -math.inf,
            config.round_trip_cost_bps, 0.0, 0.0, 0.0, 0.0,
            "CONFORMAL_UNCERTAINTY",
        )

    p = prediction.normalized_probabilities()
    entry = float(candidate.entry_price)
    reward_fraction = abs(float(candidate.target_price) - entry) / entry
    loss_fraction = abs(entry - float(candidate.stop_price)) / entry
    timeout_penalty = 0.25 * loss_fraction
    uncertainty_penalty = 0.05 * loss_fraction * float(prediction.entropy)
    cost_fraction = float(config.round_trip_cost_bps) / 10_000.0
    utility = (
        p["TP"] * reward_fraction
        - p["SL"] * loss_fraction
        - p["TIMEOUT"] * timeout_penalty
        - cost_fraction
        - uncertainty_penalty
        - float(config.safety_margin)
    )
    status = GateStatus.PASS if utility > 0 else GateStatus.VETO
    reason = "POSITIVE_EXPECTED_UTILITY" if status is GateStatus.PASS else "NON_POSITIVE_EXPECTED_UTILITY"
    return EconomicDecision(
        event_id=candidate.event_id,
        status=status,
        expected_utility=float(utility),
        round_trip_cost_bps=float(config.round_trip_cost_bps),
        reward_fraction=float(reward_fraction),
        loss_fraction=float(loss_fraction),
        timeout_penalty_fraction=float(timeout_penalty),
        uncertainty_penalty_fraction=float(uncertainty_penalty),
        reason=reason,
    )
