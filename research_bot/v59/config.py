from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from ..execution.constitution import RISK_CONSTITUTION_V1


ABSOLUTE_RISK_CEILINGS = {
    name: getattr(RISK_CONSTITUTION_V1, name) for name in
    ("risk_per_trade", "max_asset_weight", "max_gross_exposure", "drawdown_kill", "max_cvar95")
}


# Compatibility name; research and runtime now share the same immutable type.
from ..execution.constitution import RiskConstitution as FinancialConstitution


@dataclass(frozen=True)
class V59Config:
    constitution: FinancialConstitution = FinancialConstitution()
    round_trip_cost_bps: float = 24.0
    safety_margin: float = 0.0
    max_entropy: float = 0.98
    max_shift_score: float = 8.0
    min_regime_confidence: float = 0.20
    conformal_max_set_size: int = 2
    require_calibration: bool = True
    require_ai_gate: bool = True
    require_economic_gate: bool = True
    require_financial_gate: bool = True
    paper_execution: bool = False
    live_execution: bool = False

    def __post_init__(self) -> None:
        if self.paper_execution or self.live_execution:
            raise RuntimeError("V59 phase-1 rebuild is strictly research-only")
        numeric = {
            "round_trip_cost_bps": self.round_trip_cost_bps,
            "safety_margin": self.safety_margin,
            "max_entropy": self.max_entropy,
            "max_shift_score": self.max_shift_score,
            "min_regime_confidence": self.min_regime_confidence,
        }
        for name, value in numeric.items():
            if isinstance(value, bool) or not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite numeric data")
        if not 0 <= self.round_trip_cost_bps < 20_000:
            raise ValueError("round_trip_cost_bps must be in [0,20000)")
        if not 0 <= self.max_entropy <= 1:
            raise ValueError("max_entropy must be in [0,1]")
        if self.max_shift_score < 0:
            raise ValueError("max_shift_score must be nonnegative")
        if not 0 <= self.min_regime_confidence <= 1:
            raise ValueError("min_regime_confidence must be in [0,1]")
        if not isinstance(self.conformal_max_set_size, int) or self.conformal_max_set_size < 1:
            raise ValueError("conformal_max_set_size must be a positive integer")
