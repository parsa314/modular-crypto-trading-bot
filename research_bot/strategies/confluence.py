"""Four-arm score aggregation with conflict veto and fixed research weights.

With equal weights (0.25 each), normalized 0..12 = 4*sum(weight*0..3).
Threshold 7 is a *hypothesis* to be evaluated after realistic costs, not an
optimized or scientifically established trade decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Mapping

from .common import Action, StrategySignal


STYLES = ("ICHIMOKU", "ICT", "SMC", "AL_BROOKS")
EQUAL_WEIGHTS: dict[str, float] = {style: 0.25 for style in STYLES}


@dataclass(frozen=True)
class ConfluenceDecision:
    action: Action
    score_0_to_12: float
    agreeing_styles: tuple[str, ...]
    reason: str
    decision_at: datetime


def combine(signals: Mapping[str, StrategySignal], *, as_of: datetime,
            weights: Mapping[str, float] | None = None,
            strong_threshold: float = 7.0,
            min_agreeing_styles: int = 3) -> ConfluenceDecision:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of requires timezone")
    if set(signals) != set(STYLES) or any(signals[k].style != k for k in STYLES):
        raise ValueError("EXACT_FOUR_INDEPENDENT_STYLE_SIGNALS_REQUIRED")
    if set(weights or EQUAL_WEIGHTS) != set(STYLES):
        raise ValueError("WRONG_WEIGHT_KEYS")
    w = dict(weights if weights is not None else EQUAL_WEIGHTS)
    if not all(math.isfinite(float(v)) and float(v) >= 0 for v in w.values()):
        raise ValueError("INVALID_WEIGHTS")
    if not math.isclose(sum(float(v) for v in w.values()), 1.0, abs_tol=1e-10):
        raise ValueError("WEIGHTS_MUST_SUM_TO_ONE")
    if not math.isfinite(strong_threshold) or not 0 < strong_threshold <= 12:
        raise ValueError("INVALID_THRESHOLD")
    if not 1 <= min_agreeing_styles <= 4:
        raise ValueError("INVALID_MIN_AGREEMENT")
    times = {s.decision_at for s in signals.values()}
    if len(times) != 1:
        raise ValueError("MISALIGNED_SIGNALS")
    aligned_at = next(iter(times))
    if aligned_at > as_of:
        raise ValueError("FUTURE_SIGNAL")
    buys = tuple(k for k in STYLES if signals[k].action == "BUY")
    sells = tuple(k for k in STYLES if signals[k].action == "SELL")
    if buys and sells:
        return ConfluenceDecision("HOLD", 0.0, (), "CONFLICT_VETO", aligned_at)
    side: Action = "BUY" if buys else "SELL" if sells else "HOLD"
    agreeing = buys or sells
    score = 4.0 * sum(float(w[k]) * signals[k].score for k in agreeing)
    if len(agreeing) < min_agreeing_styles:
        return ConfluenceDecision("HOLD", score, agreeing, "INSUFFICIENT_INDEPENDENT_AGREEMENT", aligned_at)
    if score < strong_threshold:
        return ConfluenceDecision("HOLD", score, agreeing, "SCORE_BELOW_PREREGISTERED_THRESHOLD", aligned_at)
    return ConfluenceDecision(side, score, agreeing, "RESEARCH_SIGNAL_ONLY_NOT_EXECUTION", aligned_at)
