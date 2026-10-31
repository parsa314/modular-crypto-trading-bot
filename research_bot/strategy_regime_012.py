"""Fixed-parameter research regime descriptor; not a trading filter."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
import pandas as pd

from .strategies.common import atr
from .strategy_research_012 import validated_prefix


@dataclass(frozen=True)
class FixedRegimeParameters:
    parameter_id: str
    trend_lookback: int
    trend_atr_threshold: float
    high_vol_atr_fraction: float

    def __post_init__(self):
        if not self.parameter_id or isinstance(self.trend_lookback,bool) or not isinstance(self.trend_lookback,int) or not 2 <= self.trend_lookback <= 89:
            raise ValueError("INVALID_REGIME_PARAMETERS")
        if not all(math.isfinite(v) and v > 0 for v in (self.trend_atr_threshold,self.high_vol_atr_fraction)):
            raise ValueError("INVALID_REGIME_PARAMETERS")


@dataclass(frozen=True)
class FixedRegime:
    trend: str
    volatility: str
    liquidity_stress: str
    available_at: str
    parameter_id: str
    atr_fraction: float
    trend_atr_units: float
    status: str = "RESEARCH_HYPOTHESIS_NOT_VALIDATED"


def fixed_regime(frame: pd.DataFrame, *, timeframe: str, as_of: datetime,
                 parameters: FixedRegimeParameters) -> FixedRegime:
    """Classify using caller-fixed thresholds and only closed PIT observations.

    No defaults are selected or tuned here. In particular, OHLCV cannot establish
    liquidity stress, so that field remains DATA_UNAVAILABLE.
    """
    x=validated_prefix(frame,timeframe=timeframe,as_of=as_of)
    vol=float(atr(x).iloc[-1]); close=float(x.close.iloc[-1])
    if not math.isfinite(vol) or vol <= 0:
        raise ValueError("ATR_UNAVAILABLE")
    units=(close-float(x.close.iloc[-1-parameters.trend_lookback]))/vol
    trend = ("UPTREND" if units >= parameters.trend_atr_threshold else
             "DOWNTREND" if units <= -parameters.trend_atr_threshold else "RANGE")
    fraction=vol/close
    return FixedRegime(trend,"HIGH" if fraction >= parameters.high_vol_atr_fraction else "LOW",
                       "DATA_UNAVAILABLE",x.available_at.iloc[-1].isoformat(),parameters.parameter_id,
                       fraction,units)
