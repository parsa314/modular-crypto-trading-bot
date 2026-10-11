"""Offline course event labels under an explicit conservative OHLCV fill model.

No broker, authorization, risk engine or order transport exists in this module.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

import pandas as pd

from .contracts import HypothesisCandidate, UnsupportedCapability, aware, digest
from .strategies import CourseStrategy, macd, rsi_adjusted, validated_candles

SUPPORTED_BASELINES = frozenset({
    "sma_crossover", "bollinger_reversion", "rsi_reversal", "donchian_breakout",
    "macd_crossover", "technical_confluence",
})


@dataclass(frozen=True)
class CourseBaselineEvent:
    event_id: str
    candidate_event_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    venue: str
    direction: int
    decision_time: datetime
    evidence_available_at: datetime
    entry_time: datetime
    exit_time: datetime
    label_available_at: datetime
    entry_price: Decimal
    exit_price: Decimal
    outcome: Literal["TP", "SL", "TIMEOUT", "INDICATOR_EXIT"]
    gross_return: Decimal
    round_trip_cost: Decimal
    net_return: Decimal
    bars_held: int
    config_hash: str
    dataset_hash: str
    fill_model: str = "ENGINEERING_EXTENSION_AVAILABLE_PREFIX_OPEN_OHLC_V2"
    exit_timestamp_resolution: str = "BAR_OPEN_OR_CONSERVATIVE_BAR_CLOSE_BOUND"
    short_financing_status: str = "HYPOTHETICAL_RETURN_ONLY_NO_MARGIN_OR_BORROW_VALIDATION"

    def __post_init__(self) -> None:
        for field in ("decision_time", "evidence_available_at", "entry_time", "exit_time", "label_available_at"):
            aware(getattr(self, field))
        if not self.evidence_available_at <= self.decision_time < self.entry_time <= self.exit_time <= self.label_available_at:
            raise ValueError("INVALID_LABEL_EVENT_TIMING")
        if self.direction not in {-1, 1} or self.bars_held < 1:
            raise ValueError("INVALID_LABEL_EVENT_DIRECTION")
        for value in (self.entry_price, self.exit_price):
            if not value.is_finite() or value <= 0:
                raise ValueError("INVALID_LABEL_EVENT_PRICE")
        for value in (self.gross_return, self.round_trip_cost, self.net_return):
            if not value.is_finite():
                raise ValueError("NONFINITE_LABEL_RETURN")
        if self.round_trip_cost < 0 or self.net_return != self.gross_return-self.round_trip_cost:
            raise ValueError("INVALID_LABEL_COST_ACCOUNTING")


def _indicator_exit(strategy: CourseStrategy, prefix: pd.DataFrame, direction: int) -> bool:
    p = strategy.parameters
    close = prefix.close.astype(float)
    sid = strategy.strategy_id
    if sid == "sma_crossover":
        fast = close.rolling(int(p["fast"])).mean()
        slow = close.rolling(int(p["slow"])).mean()
        return bool(fast.iloc[-2] >= slow.iloc[-2] and fast.iloc[-1] < slow.iloc[-1])
    if sid == "bollinger_reversion":
        return bool(close.iloc[-1] >= close.rolling(int(p["window"])).mean().iloc[-1])
    if sid == "rsi_reversal":
        current = rsi_adjusted(close, int(p["window"])).iloc[-1]
        return bool(current >= p["overbought"] if direction == 1 else current <= p["oversold"])
    if sid == "donchian_breakout":
        high = prefix.high.astype(float).rolling(int(p["window"])).max().iloc[-1]
        low = prefix.low.astype(float).rolling(int(p["window"])).min().iloc[-1]
        midpoint = (high+low)/2
        return bool(close.iloc[-1] < midpoint if direction == 1 else close.iloc[-1] > midpoint)
    line, signal = macd(close, int(p["fast"]), int(p["slow"]), int(p["signal"]),
                        price_adjust=sid == "macd_crossover")
    return bool(line.iloc[-2] > signal.iloc[-2] and line.iloc[-1] < signal.iloc[-1]
                if direction == 1 else
                line.iloc[-2] < signal.iloc[-2] and line.iloc[-1] > signal.iloc[-1])


def replay_course_events(frame: pd.DataFrame, strategy: CourseStrategy, *, symbol: str,
                         venue: str, cost_bps: Decimal | float | None = None
                         ) -> tuple[CourseBaselineEvent, ...]:
    """Return matured events only, single position, six source candle baselines.

    Required input adds aware ``open_time`` to the strategy candle contract.
    Entry is the next bar's observed open strictly after signal availability;
    delayed signals cannot receive a retrospective next-open fill. Intrabar
    execution is an explicit model, not proof of a realizable fill. No final
    dataset-boundary liquidation is invented. Cost defaults to zero so the
    existing evaluator can charge its registered round-trip scenario once.
    """
    if strategy.strategy_id not in SUPPORTED_BASELINES:
        raise UnsupportedCapability("COURSE_TICKER_OR_BOOK_LIFECYCLE_REQUIRES_SEPARATE_PROTOCOL")
    if not strategy.enabled:
        return ()
    if not isinstance(frame, pd.DataFrame) or frame.empty or "open_time" not in frame:
        raise ValueError("EXPLICIT_OPEN_TIMESTAMPS_REQUIRED")
    data = frame.copy(deep=True)
    for field in ("open_time", "close_time", "available_at"):
        stamps = [pd.Timestamp(value) for value in data[field]]
        if any(pd.isna(value) or value.tzinfo is None for value in stamps):
            raise ValueError("INVALID_REPLAY_TIMESTAMP")
        data[field] = [value.tz_convert("UTC") for value in stamps]
    if data.open_time.duplicated().any() or not data.open_time.is_monotonic_increasing:
        raise ValueError("DUPLICATE_OR_UNSORTED_OPEN_TIMESTAMPS")
    if len(data) > 1 and any(a > b for a, b in zip(data.close_time.iloc[:-1], data.open_time.iloc[1:])):
        raise ValueError("OVERLAPPING_CANDLE_INTERVALS")
    final_time = data.available_at.max().to_pydatetime()
    validated_candles(data, final_time, max_age_seconds=int(strategy.parameters["max_age_seconds"]))
    data = data.reset_index(drop=True)
    cost = Decimal(0) if cost_bps is None else Decimal(str(cost_bps))/Decimal(10000)
    if not cost.is_finite() or cost < 0:
        raise ValueError("INVALID_ROUND_TRIP_COST")
    p = strategy.parameters
    events: list[CourseBaselineEvent] = []
    position: tuple[HypothesisCandidate, int, datetime, Decimal] | None = None
    pending_exit: Literal["TIMEOUT", "INDICATOR_EXIT"] | None = None
    exit_known_at: datetime | None = None
    for i in range(1, len(data)):
        row = data.iloc[i]
        open_time = row.open_time.to_pydatetime()
        open_price = Decimal(str(row.open))
        close_bound = row.close_time.to_pydatetime()
        label_time = row.available_at.to_pydatetime()
        # Real contiguous candles close exactly on the next open. Select only
        # the prefix actually available strictly before this execution time;
        # never shift provider timestamps to manufacture a next-open fill.
        previous = data.iloc[:i]
        previous = previous.loc[previous.available_at < open_time]
        if position is None:
            if len(previous) < int(p["warmup"]):
                continue
            decision_time = previous.available_at.max().to_pydatetime()
            if strategy._direction(previous, p) == 0:
                continue
            candidates = strategy.generate(previous, symbol=symbol, venue=venue, decision_time=decision_time)
            if not candidates or candidates[0].direction == 0:
                continue
            candidate = candidates[0]
            if candidate.earliest_execution_time > open_time:
                continue
            position = (candidate, i, open_time, open_price)
        candidate, entry_i, entry_time, entry_price = position
        direction = candidate.direction
        stop = entry_price*(Decimal(1)-Decimal(direction)*Decimal(str(p["stop_loss"])))
        target = entry_price*(Decimal(1)+Decimal(direction)*Decimal(str(p["take_profit"])))
        outcome: Literal["TP", "SL", "TIMEOUT", "INDICATOR_EXIT"] | None = None
        exit_price = None
        exit_time = close_bound
        gap_stop = open_price <= stop if direction == 1 else open_price >= stop
        # Stops dominate pending indicator exits after an adverse open gap.
        if gap_stop:
            outcome, exit_price, exit_time = "SL", open_price, open_time
        # A deterministic requested close is only eligible at a future open.
        elif pending_exit is not None and exit_known_at is not None and exit_known_at < open_time:
            outcome, exit_price, exit_time = pending_exit, open_price, open_time
        else:
            low, high = Decimal(str(row.low)), Decimal(str(row.high))
            stop_touched = low <= stop if direction == 1 else high >= stop
            target_touched = high >= target if direction == 1 else low <= target
            if stop_touched:
                outcome, exit_price = "SL", stop
            elif target_touched:
                # Favorable gaps are conservatively capped at target.
                outcome, exit_price = "TP", target
        if outcome is not None and exit_price is not None:
            gross = Decimal(direction)*(exit_price/entry_price-Decimal(1))
            dataset_hash = digest(data.iloc[:i+1].to_dict(orient="records"))
            record: dict[str, Any] = {
                "fill_model": "ENGINEERING_EXTENSION_AVAILABLE_PREFIX_OPEN_OHLC_V2",
                "candidate_event_id": candidate.event_id, "strategy_id": candidate.strategy_id,
                "strategy_version": candidate.strategy_version, "symbol": symbol, "venue": venue,
                "direction": direction, "decision_time": candidate.decision_time,
                "evidence_available_at": candidate.evidence_available_at,
                "entry_time": entry_time, "exit_time": exit_time, "label_available_at": label_time,
                "entry_price": entry_price, "exit_price": exit_price, "outcome": outcome,
                "gross_return": gross, "round_trip_cost": cost, "net_return": gross-cost,
                "bars_held": i-entry_i+1, "config_hash": candidate.config_hash,
                "dataset_hash": dataset_hash,
            }
            event = CourseBaselineEvent(event_id=digest(record), **record)
            events.append(event)
            position, pending_exit, exit_known_at = None, None, None
            # No same-open reentry: conservative extension, not source fill truth.
            continue
        # Preserve the first known close request. Repeated conditions must not
        # reset its availability and postpone it forever on contiguous bars.
        if pending_exit is None:
            if _indicator_exit(strategy, data.iloc[:i+1], direction):
                pending_exit, exit_known_at = "INDICATOR_EXIT", label_time
            elif i-entry_i+1 >= int(p["timeout_bars"]):
                pending_exit, exit_known_at = "TIMEOUT", label_time
    return tuple(events)


def event_records(events: tuple[CourseBaselineEvent, ...]) -> tuple[dict[str, Any], ...]:
    """Serializable copies for the existing evaluator and evidence ledger."""
    return tuple(asdict(event) for event in events)
