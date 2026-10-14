"""Four independent causal strategy candidates; deliberately no order API."""

from .common import StrategySignal, closed_bars
from .ichimoku_strategy import evaluate as evaluate_ichimoku
from .ict_strategy import evaluate as evaluate_ict
from .smc_strategy import evaluate as evaluate_smc
from .al_brooks_strategy import evaluate as evaluate_al_brooks
from .confluence import ConfluenceDecision, combine


def evaluate_all(frame, *, as_of):
    """Each evaluator receives the same PIT-filtered input; no shared alpha."""
    return {
        "ICHIMOKU": evaluate_ichimoku(frame, as_of=as_of),
        "ICT": evaluate_ict(frame, as_of=as_of),
        "SMC": evaluate_smc(frame, as_of=as_of),
        "AL_BROOKS": evaluate_al_brooks(frame, as_of=as_of),
    }


__all__ = ["StrategySignal", "ConfluenceDecision", "closed_bars", "evaluate_all", "combine",
           "evaluate_ichimoku", "evaluate_ict", "evaluate_smc", "evaluate_al_brooks"]
