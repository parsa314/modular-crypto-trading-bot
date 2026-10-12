"""Phase-one pure, closed-bar baselines. Explicit event time; no invented alpha.

MA Cross is an additional research comparator. RSI and RSI+MA preserve the
course's rolling-simple RSI convention, with a neutral RSI=50 for flat prices.
Every warmed-up event emits one candidate (NEUTRAL when its rule does not fire).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Sequence

from hashlib import sha256

from ..hypothesis import HypothesisCandidate, HypothesisFamily, HypothesisSide, canonical_json
from ..jamalian_data import Candle, decimal_value
from .jamalian_hypotheses import _closed, _period, rolling_rsi


def _timing(interval: timedelta, latency: timedelta, min_bars: int) -> None:
    if not isinstance(interval, timedelta) or interval <= timedelta(0):
        raise ValueError("positive bar interval required")
    if not isinstance(latency, timedelta) or latency <= timedelta(0):
        raise ValueError("positive execution delay required")
    _period(min_bars)


@dataclass(frozen=True)
class MACrossParams:
    fast: int = 10
    slow: int = 30
    min_bars: int = 35
    bar_interval: timedelta = timedelta(hours=1)
    execution_delay: timedelta = timedelta(seconds=1)

    def __post_init__(self):
        if _period(self.fast) >= _period(self.slow):
            raise ValueError("fast period must be smaller than slow")
        _timing(self.bar_interval, self.execution_delay, self.min_bars)


@dataclass(frozen=True)
class RSIParams:
    period: int = 14
    oversold: Decimal = Decimal("30")
    min_bars: int = 20
    bar_interval: timedelta = timedelta(hours=1)
    execution_delay: timedelta = timedelta(seconds=1)

    def __post_init__(self):
        _period(self.period)
        level = decimal_value(self.oversold, positive=True)
        if level >= 100:
            raise ValueError("oversold must be below 100")
        object.__setattr__(self, "oversold", level)
        _timing(self.bar_interval, self.execution_delay, self.min_bars)


@dataclass(frozen=True)
class RsiMaParams:
    rsi_period: int = 14
    ma_period: int = 20
    oversold: Decimal = Decimal("30")
    min_bars: int = 25
    bar_interval: timedelta = timedelta(hours=1)
    execution_delay: timedelta = timedelta(seconds=1)

    def __post_init__(self):
        _period(self.rsi_period)
        _period(self.ma_period)
        level = decimal_value(self.oversold, positive=True)
        if level >= 100:
            raise ValueError("oversold must be below 100")
        object.__setattr__(self, "oversold", level)
        _timing(self.bar_interval, self.execution_delay, self.min_bars)


def _candidate(strategy, family, history, at, params, fires, details):
    parameters = asdict(params)
    for key in ("bar_interval", "execution_delay"):
        parameters[key + "_seconds"] = parameters.pop(key).total_seconds()
    input_digest = sha256(canonical_json([str(c.close) for c in history]).encode()).hexdigest()
    return HypothesisCandidate(strategy, family, history[-1].symbol,
        HypothesisSide.LONG if fires else HypothesisSide.NEUTRAL,
        at, at + params.execution_delay,
        ("BASELINE_RULE_MATCH" if fires else "BASELINE_RULE_NOT_MATCHED",),
        tuple(c.stamp for c in history), parameters,
        {**details, "input_close_sha256": input_digest,
            "research_phase": "PHASE_1", "calibration_status": "NOT_CALIBRATED"})


class MovingAverageCrossoverHypothesis:
    def __init__(self, params: MACrossParams | None = None):
        self.params = params or MACrossParams()

    def generate(self, candles: Sequence[Candle], at: datetime) -> list[HypothesisCandidate]:
        p = self.params
        history = _closed(candles, at, p.bar_interval)
        if len(history) < max(p.slow + 1, p.min_bars):
            return []
        prices = [c.close for c in history]
        sma = lambda xs, n: sum(xs[-n:], Decimal(0)) / n
        fast, slow = sma(prices, p.fast), sma(prices, p.slow)
        previous_fast, previous_slow = sma(prices[:-1], p.fast), sma(prices[:-1], p.slow)
        fires = previous_fast <= previous_slow and fast > slow
        return [_candidate("JAMALIAN_MA_CROSS_V3", HypothesisFamily.TECHNICAL_BASELINE,
            history, at, p, fires, {"fast_ma": fast, "slow_ma": slow,
                "previous_fast_ma": previous_fast, "previous_slow_ma": previous_slow})]


class RSIMeanReversionHypothesis:
    def __init__(self, params: RSIParams | None = None):
        self.params = params or RSIParams()

    def generate(self, candles: Sequence[Candle], at: datetime) -> list[HypothesisCandidate]:
        p = self.params
        history = _closed(candles, at, p.bar_interval)
        if len(history) < max(p.period + 1, p.min_bars):
            return []
        rsi = rolling_rsi([c.close for c in history], p.period)
        return [_candidate("JAMALIAN_RSI_MR_V3", HypothesisFamily.MEAN_REVERSION,
            history, at, p, rsi < p.oversold, {"rsi": rsi, "rsi_method": "ROLLING_SIMPLE"})]


class RsiMaConfluenceHypothesis:
    def __init__(self, params: RsiMaParams | None = None):
        self.params = params or RsiMaParams()

    def generate(self, candles: Sequence[Candle], at: datetime) -> list[HypothesisCandidate]:
        p = self.params
        history = _closed(candles, at, p.bar_interval)
        if len(history) < max(p.rsi_period + 1, p.ma_period, p.min_bars):
            return []
        prices = [c.close for c in history]
        rsi = rolling_rsi(prices, p.rsi_period)
        ma = sum(prices[-p.ma_period:], Decimal(0)) / p.ma_period
        return [_candidate("JAMALIAN_RSI_MA_V3", HypothesisFamily.CONFLUENCE,
            history, at, p, prices[-1] > ma and rsi < p.oversold,
            {"rsi": rsi, "ma": ma, "price": prices[-1], "rsi_method": "ROLLING_SIMPLE"})]


BASELINE_REGISTRY = {
    "ma_cross": (MovingAverageCrossoverHypothesis, MACrossParams),
    "rsi": (RSIMeanReversionHypothesis, RSIParams),
    "rsi_ma": (RsiMaConfluenceHypothesis, RsiMaParams),
}
