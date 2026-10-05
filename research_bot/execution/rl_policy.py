"""Pure offline challenger limits; no Gym/torch/model or exchange import."""
from dataclasses import dataclass, replace
import math
from numbers import Integral, Real
from .constitution import RISK_CONSTITUTION_V1, assert_risk_bindings

def _finite_number(name, value):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError(name+" must be a finite real number")
    return float(value)

@dataclass(frozen=True)
class TradingEnvConfig:
    lookback: int = 30
    initial_balance: float = 10_000.0
    transaction_cost: float = 0.001
    slippage_bps: float = 5.0
    max_asset_weight: float = RISK_CONSTITUTION_V1.max_asset_weight
    max_drawdown: float = RISK_CONSTITUTION_V1.drawdown_kill
    cvar_alpha: float = 0.95
    cvar_window: int = 100
    cvar_min_samples: int = 20
    max_cvar: float = RISK_CONSTITUTION_V1.max_cvar95

    def __post_init__(self) -> None:
        assert_risk_bindings(drawdown=self.max_drawdown, asset_weight=self.max_asset_weight, cvar=self.max_cvar)
        if self.cvar_alpha != .95:
            raise ValueError('RISK_CONSTITUTION_OVERRIDE_FORBIDDEN')
        for name in ("lookback", "cvar_window", "cvar_min_samples"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, Integral) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.cvar_min_samples > self.cvar_window:
            raise ValueError("cvar_min_samples cannot exceed cvar_window")
        for name in (
            "initial_balance", "transaction_cost", "slippage_bps",
            "max_asset_weight", "max_drawdown", "cvar_alpha", "max_cvar",
        ):
            _finite_number(name, getattr(self, name))
        if self.initial_balance <= 0:
            raise ValueError("initial_balance must be positive")
        if not 0 <= self.transaction_cost < 1:
            raise ValueError("transaction_cost must be in [0, 1)")
        if not 0 <= self.slippage_bps < 10_000:
            raise ValueError("slippage_bps must be in [0, 10000)")
        if not 0 <= self.max_asset_weight <= 1:
            raise ValueError("max_asset_weight must be in [0, 1]")
        if not 0 < self.max_drawdown < 1:
            raise ValueError("max_drawdown must be in (0, 1)")
        if not 0 < self.cvar_alpha < 1:
            raise ValueError("cvar_alpha must be in (0, 1)")
        if not 0 < self.max_cvar <= 1:
            raise ValueError("max_cvar must be in (0, 1]")


    @property
    def risk_constitution_hash(self):
        return replace(RISK_CONSTITUTION_V1, max_asset_weight=self.max_asset_weight,
            drawdown_kill=self.max_drawdown, drawdown_warning=min(RISK_CONSTITUTION_V1.drawdown_warning, self.max_drawdown*.6),
            max_cvar95=self.max_cvar).sha256
