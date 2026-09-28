from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
import re

import numpy as np
import pandas as pd

from .contracts import Direction, StrategyArm
from .events import stable_hash, make_feature_snapshot_id
from .features import add_v58_continuous_features


FEATURE_SCHEMA_VERSION = "58.2"


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
    atr_at_event: float = math.nan
    feature_snapshot_id: str = ""
    state_timestamps: tuple[tuple[str, str], ...] = ()
    feature_values: tuple[tuple[str, float | None], ...] = ()


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
    duration = _duration(timeframe)
    # Only the declared raw bar schema may feed a snapshot. Caller-supplied
    # derived columns must neither override features nor inject future data.
    x = add_v58_continuous_features(frame.loc[:, ["timestamp", "open", "high", "low", "close", "volume"]], timeframe=timeframe)
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
    chain_traces: dict[int, tuple[int, int, int]] = {}
    sweep_index: int | None = None
    displacement_index: int | None = None
    mss_level = math.nan
    for i in range(len(x)):
        if i < 200 or not np.isfinite(atr.iat[i]):
            continue
        bullish = c.iat[i] > o.iat[i]
        a = bool(c.iat[i] > prior20h.iat[i] and ema200.iat[i] > ema200.shift(6).iat[i])
        b1 = bool(bullish and c.iat[i] > top.iat[i] and c.iat[i-1] <= top.iat[i-1])
        b2 = bool(bullish and c.iat[i] > top.iat[i] and tenkan.iat[i] > kijun.iat[i] and x["tenkan_slope"].iat[i] > 0 and x["kijun_slope"].iat[i] >= 0)
        recent_reject = any(
            np.isfinite(kijun.iat[k]) and abs(l.iat[k]-kijun.iat[k]) <= 0.25*atr.iat[k]
            for k in range(max(0, i-2), i+1)
        )
        b3 = bool(c.iat[i] > top.iat[i] and c.iat[i] > o.iat[i] and clv01.iat[i] >= 0.75 and recent_reject)
        # One ordered, consumable long chain. Every reference is frozen when
        # observed; three different closed bars must establish its states.
        c_arm = False
        if sweep_index is not None and i - sweep_index > 6:
            sweep_index = displacement_index = None
        if x["bull_sweep"].iat[i] == 1.0:
            sweep_index, displacement_index = i, None
            mss_level = float(prior20h.iat[i])
        elif sweep_index is not None:
            broke_level = c.iat[i] > mss_level
            if broke_level:
                if displacement_index is not None and bullish and c.iat[i-1] <= mss_level:
                    c_arm = True
                    chain_traces[i] = (sweep_index, displacement_index, i)
                # An early break or a completed chain cannot be reused.
                sweep_index = displacement_index = None
            elif displacement_index is None and bullish and (
                x["displacement_body_ratio"].iat[i] >= 0.60
                and x["displacement_range_atr"].iat[i] >= 1.0
                and x["relative_volume"].iat[i] >= 1.0
            ):
                displacement_index = i
        d1 = bool(bullish and x["trend_strength"].iat[i] >= 0.5 and x["signal_bar_quality"].iat[i] >= 0.5 and x["follow_through_strength"].iat[i] > 0)
        d2 = bool(bullish and x["breakout_strength"].iat[i] >= 0.25 and x["signal_bar_quality"].iat[i] >= 0.5)
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
    feature_columns = sorted(set(x.columns) - {"timestamp"})
    for i, arm, subtype, families, states in rows:
        event_ts = x["timestamp"].iat[i].to_pydatetime() + duration
        eid = candidate_event_id(venue=venue,symbol=symbol,timeframe=timeframe,event_timestamp=event_ts,
                                 strategy_arm=arm,setup_subtype=subtype,direction=Direction.LONG)
        if eid in seen:
            raise RuntimeError(f"duplicate candidate event: {eid}")
        seen.add(eid)
        values = {name: float(x[name].iat[i]) if np.isfinite(x[name].iat[i]) else None for name in feature_columns}
        values["atr_at_event"] = float(atr.iat[i])
        state_times = tuple((state, event_ts.isoformat()) for state in states)
        if arm in (StrategyArm.ARM_C, StrategyArm.ARM_E):
            trace = chain_traces[i]
            state_times = tuple((state, (x["timestamp"].iat[index].to_pydatetime() + duration).isoformat())
                                for state, index in zip(("SWEEP_RECLAIM", "DISPLACEMENT", "MSS"), trace))
            values.update({"sweep_row_index": float(trace[0]), "displacement_row_index": float(trace[1]),
                           "mss_row_index": float(trace[2]),
                           "chain_reclaim_strength_atr": float(x["sweep_reclaim_strength_atr"].iat[trace[0]]),
                           "chain_mss_level": float(prior20h.iat[trace[0]])})
        snapshot = make_feature_snapshot_id(event_timestamp=event_ts, feature_version=FEATURE_SCHEMA_VERSION, features=values)
        out.append(CandidateEvent(eid,venue,symbol,timeframe,event_ts,arm,subtype,Direction.LONG,
                                  FEATURE_SCHEMA_VERSION,families,states,i,float(atr.iat[i]),snapshot,state_times,tuple(sorted(values.items()))))
    return out
