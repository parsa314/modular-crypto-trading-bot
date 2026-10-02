"""V59 next-generation trading research kernel.

V59 is research-only. No module in this package is authorized to create
PAPER or LIVE orders.
"""

from .config import FinancialConstitution, V59Config
from .contracts import (
    Direction,
    GateStatus,
    Regime,
    SignalCandidate,
    ModelPrediction,
    UncertaintyAssessment,
    EconomicDecision,
    FinancialDecision,
    PortfolioState,
    FinalResearchDecision,
)
from .orchestrator import V59DecisionOrchestrator

__all__ = [
    "FinancialConstitution",
    "V59Config",
    "Direction",
    "GateStatus",
    "Regime",
    "SignalCandidate",
    "ModelPrediction",
    "UncertaintyAssessment",
    "EconomicDecision",
    "FinancialDecision",
    "PortfolioState",
    "FinalResearchDecision",
    "V59DecisionOrchestrator",
]
