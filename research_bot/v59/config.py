from __future__ import annotations

from dataclasses import asdict, dataclass
import math


ABSOLUTE_RISK_CEILINGS = {
    "risk_per_trade": 0.0025,
    "max_asset_weight": 0.35,
    "max_gross_exposure": 0.70,
    "drawdown_kill": 0.05,
    "max_cvar95": 0.035,
}


@dataclass(frozen=True)
class FinancialConstitution:
    risk_per_trade: float = 0.0025
    max_asset_weight: float = 0.35
    max_gross_exposure: float = 0.70
    drawdown_kill: float = 0.05
    max_cvar95: float = 0.035
    min_cash_buffer: float = 0.05
    max_turnover_per_step: float = 0.70

    def __post_init__(self) -> None:
        values = asdict(self)
        for name, value in values.items():
            if isinstance(value, bool):
                raise ValueError(f"{name} must be numeric")
            value = float(value)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
            object.__setattr__(self, name, value)
        for name, ceiling in ABSOLUTE_RISK_CEILINGS.items():
            if getattr(self, name) > ceiling:
                raise ValueError(f"{name} cannot exceed V59 absolute ceiling {ceiling}")
        if not 0 <= self.min_cash_buffer < 1:
            raise ValueError("min_cash_buffer must be in [0,1)")
        if not 0 < self.max_turnover_per_step <= 1:
            raise ValueError("max_turnover_per_step must be in (0,1]")


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
