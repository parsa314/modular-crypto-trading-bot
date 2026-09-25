from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
import re

import numpy as np
import pandas as pd

from .contracts import Direction, StrategyArm
from .events import stable_hash
from .features import add_v58_continuous_features


FEATURE_SCHEMA_VERSION = "58.1"


@dataclass(frozen=True)
class CandidateEvent:
    event_id: str
    venue: str
    symbol: str
    timeframe: str
    event_timestamp: datetime
    strategy_arm: StrategyArm
    setup_subtype: str
    direction: Direction
    feature_schema_version: str
    contributing_families: tuple[str, ...]
    states: tuple[str, ...]
    row_index: int


def _duration(timeframe: str) -> timedelta:
    match = re.fullmatch(r"(\d+)([mhd])", timeframe.lower())
    if not match:
        raise ValueError("timeframe must match <integer>[m|h|d]")
    value, unit = int(match.group(1)), match.group(2)
    if value <= 0:
        raise ValueError("timeframe value must be positive")
    if unit == "m":
        return timedelta(minutes=value)
    if unit == "h":
        return timedelta(hours=value)
    return timedelta(days=value)


def candidate_event_id(*, venue: str, symbol: str, timeframe: str, event_timestamp: datetime,
                       strategy_arm: StrategyArm, setup_subtype: str, direction: Direction,
                       feature_schema_version: str = FEATURE_SCHEMA_VERSION) -> str:
    return stable_hash({
        "venue": venue.lower().strip(), "symbol": symbol.upper().strip(),
        "timeframe": timeframe.lower().strip(), "event_timestamp": event_timestamp.isoformat(),
        "strategy_arm": strategy_arm.value, "setup_subtype": setup_subtype,
        "direction": direction.value, "feature_schema_version": feature_schema_version,
    })


def generate_candidates(frame: pd.DataFrame, *, venue: str, symbol: str, timeframe: str = "4h") -> list[CandidateEvent]:
    x = add_v58_continuous_features(frame)
    duration = _duration(timeframe)
    o, h, l, c, v = (x[name].astype(float) for name in ("open", "high", "low", "close", "volume"))
    previous_close = c.shift(1)
    tr = pd.concat([h-l, (h-previous_close).abs(), (l-previous_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    tenkan = (h.rolling(9).max()+l.rolling(9).min())/2
    kijun = (h.rolling(26).max()+l.rolling(26).min())/2
    span_a = (tenkan+kijun)/2
    span_b = (h.rolling(52).max()+l.rolling(52).min())/2
    top, bottom = pd.concat([span_a, span_b], axis=1).max(axis=1), pd.concat([span_a, span_b], axis=1).min(axis=1)
    ema200 = c.ewm(span=200, adjust=False, min_periods=200).mean()
    prior20h, prior20l = h.shift(1).rolling(20).max(), l.shift(1).rolling(20).min()
    clv01 = (c-l)/(h-l).replace(0, np.nan)

    rows: list[tuple[int, StrategyArm, str, tuple[str, ...], tuple[str, ...]]] = []
    by_row: dict[int, set[StrategyArm]] = {}
    for i in range(len(x)):
        if i < 200 or not np.isfinite(atr.iat[i]):
            continue
        a = bool(c.iat[i] > prior20h.iat[i] and ema200.iat[i] > ema200.shift(6).iat[i])
        b1 = bool(c.iat[i] > top.iat[i] and c.iat[i-1] <= top.iat[i-1])
        b2 = bool(c.iat[i] > top.iat[i] and tenkan.iat[i] > kijun.iat[i] and x["tenkan_slope"].iat[i] > 0 and x["kijun_slope"].iat[i] >= 0)
        recent_reject = any(
            np.isfinite(kijun.iat[k]) and abs(l.iat[k]-kijun.iat[k]) <= 0.25*atr.iat[k]
            for k in range(max(0, i-2), i+1)
        )
        b3 = bool(c.iat[i] > top.iat[i] and c.iat[i] > o.iat[i] and clv01.iat[i] >= 0.75 and recent_reject)
        c_arm = bool(
            1 <= x["bars_since_sweep"].iat[i] <= 6 and x["sweep_depth_atr"].iloc[max(0, i-6):i+1].max() > 0
            and x["displacement_body_ratio"].iat[i] >= 0.60 and x["displacement_range_atr"].iat[i] >= 1.0
            and x["relative_volume"].iat[i] >= 1.0 and 0 <= x["bars_since_mss"].iat[i] <= 3
            and x["mss_break_distance_atr"].iat[i] > 0
        )
        d1 = bool(x["trend_strength"].iat[i] >= 0.5 and x["signal_bar_quality"].iat[i] >= 0.5 and x["follow_through_strength"].iat[i] > 0)
        d2 = bool(x["breakout_strength"].iat[i] >= 0.25 and x["signal_bar_quality"].iat[i] >= 0.5)
        d3 = bool(x["failed_breakout_score"].iat[i] >= 0.25 and c.iat[i] > o.iat[i])
        d4 = bool(abs(x["trend_strength"].iat[i]) <= 0.25 and x["trading_range_position"].iat[i] <= 0.2 and c.iat[i] > o.iat[i])
        d5 = bool(x["trend_strength"].iat[i] >= 0.5 and 0.5 <= x["pullback_depth_atr"].iat[i] <= 2.0 and c.iat[i] > o.iat[i])
        defs = []
        if a: defs.append((StrategyArm.ARM_A, "S6_BREAKOUT", ("BASE",), ("PRIOR20_BREAK", "EMA200_SLOPE_POSITIVE")))
        for hit, subtype in ((b1,"B1_CLOUD_BREAKOUT"),(b2,"B2_TK_TREND_CONTINUATION"),(b3,"B3_PULLBACK_REJECTION")):
            if hit: defs.append((StrategyArm.ARM_B, subtype, ("ICHIMOKU",), (subtype, "CHIKOU_EXCLUDED")))
        if c_arm:
            states=("SWEEP_RECLAIM","DISPLACEMENT","MSS", "FVG_PRESENT" if x["fvg_age"].iat[i] <= 6 else "FVG_ABSENT")
            defs.append((StrategyArm.ARM_C,"ICT_SMC_CHAIN",("ICT_SMC",),states))
        for hit, subtype in ((d1,"D1_TREND_CONTINUATION"),(d2,"D2_BREAKOUT"),(d3,"D3_FAILED_BREAKOUT"),(d4,"D4_RANGE_REVERSAL"),(d5,"D5_PULLBACK_CONTINUATION")):
            if hit: defs.append((StrategyArm.ARM_D,subtype,("BROOKS_PROXY",),(subtype,)))
        for arm, subtype, families, states in defs:
            rows.append((i,arm,subtype,families,states)); by_row.setdefault(i,set()).add(arm)
        if {StrategyArm.ARM_A,StrategyArm.ARM_B,StrategyArm.ARM_C,StrategyArm.ARM_D}.issubset(by_row.get(i,set())):
            rows.append((i,StrategyArm.ARM_E,"E1_FOUR_FRAMEWORK_CONFLUENCE",("S6","ICHIMOKU","ICT_SMC","BROOKS_PROXY"),("ALL_FOUR_PRESENT","CORRELATED_COMPONENTS")))

    out: list[CandidateEvent] = []
    seen: set[str] = set()
    for i, arm, subtype, families, states in rows:
        event_ts = x["timestamp"].iat[i].to_pydatetime() + duration
        eid = candidate_event_id(venue=venue,symbol=symbol,timeframe=timeframe,event_timestamp=event_ts,
                                 strategy_arm=arm,setup_subtype=subtype,direction=Direction.LONG)
        if eid in seen:
            raise RuntimeError(f"duplicate candidate event: {eid}")
        seen.add(eid)
        out.append(CandidateEvent(eid,venue,symbol,timeframe,event_ts,arm,subtype,Direction.LONG,
                                  FEATURE_SCHEMA_VERSION,families,states,i))
    return out
