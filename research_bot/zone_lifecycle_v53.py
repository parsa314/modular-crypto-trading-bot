from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd


class ZoneKind(str, Enum):
    FVG_BULL = "FVG_BULL"
    FVG_BEAR = "FVG_BEAR"
    OB_BULL = "OB_BULL"
    OB_BEAR = "OB_BEAR"
    BREAKER_BULL = "BREAKER_BULL"
    BREAKER_BEAR = "BREAKER_BEAR"


class ZoneStatus(str, Enum):
    ACTIVE = "ACTIVE"
    MITIGATED = "MITIGATED"
    FILLED = "FILLED"
    INVALIDATED = "INVALIDATED"


@dataclass
class ZoneStateV53:
    zone_id: str
    kind: ZoneKind
    lower: float
    upper: float
    formed_index: int
    status: ZoneStatus = ZoneStatus.ACTIVE
    touches: int = 0
    invalidated_index: int | None = None

    def overlaps(self, low: float, high: float) -> bool:
        return low <= self.upper and high >= self.lower


def _finite_zone(lower: float, upper: float) -> bool:
    return np.isfinite(lower) and np.isfinite(upper) and 0 < lower < upper


def add_zone_lifecycle_v53(frame: pd.DataFrame) -> pd.DataFrame:
    """Track multiple concurrent FVG/OB/breaker zones without look-ahead.

    Expected formation columns are produced by ``multiframework_features_v53``.
    Lifecycle outputs are event/state variables calculated using only the current
    and prior bars. Breakers activate one bar after invalidation, preventing the
    invalidating bar from also counting as a retest.
    """

    required = {
        "high", "low", "close",
        "ict_fvg_bull", "ict_fvg_bear",
        "ict_bull_ob_candidate", "ict_bear_ob_candidate",
        "ict_bull_ob_lower", "ict_bull_ob_upper",
        "ict_bear_ob_lower", "ict_bear_ob_upper",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing lifecycle inputs: {sorted(missing)}")

    x = frame.copy()
    n = len(x)
    active: list[ZoneStateV53] = []
    pending_breakers: list[tuple[int, ZoneStateV53]] = []

    active_fvg_bull = np.zeros(n, dtype=int)
    active_fvg_bear = np.zeros(n, dtype=int)
    active_ob_bull = np.zeros(n, dtype=int)
    active_ob_bear = np.zeros(n, dtype=int)
    fvg_fill_bull = np.zeros(n, dtype=float)
    fvg_fill_bear = np.zeros(n, dtype=float)
    ob_mit_bull = np.zeros(n, dtype=float)
    ob_mit_bear = np.zeros(n, dtype=float)
    ob_inv_bull = np.zeros(n, dtype=float)
    ob_inv_bear = np.zeros(n, dtype=float)
    breaker_retest_bull = np.zeros(n, dtype=float)
    breaker_retest_bear = np.zeros(n, dtype=float)
    nearest_bull_fvg_dist = np.full(n, np.nan)
    nearest_bear_fvg_dist = np.full(n, np.nan)
    nearest_bull_ob_dist = np.full(n, np.nan)
    nearest_bear_ob_dist = np.full(n, np.nan)

    for i in range(n):
        high = float(x["high"].iloc[i])
        low = float(x["low"].iloc[i])
        close = float(x["close"].iloc[i])

        # Breakers become active only after the invalidation bar has completed.
        if pending_breakers:
            still_pending: list[tuple[int, ZoneStateV53]] = []
            for activate_at, zone in pending_breakers:
                if activate_at <= i:
                    active.append(zone)
                else:
                    still_pending.append((activate_at, zone))
            pending_breakers = still_pending

        # Formation: FVG uses the canonical 3-candle geometry.
        if i >= 2 and float(x["ict_fvg_bull"].iloc[i]) == 1.0:
            lower = float(x["high"].iloc[i - 2])
            upper = float(x["low"].iloc[i])
            if _finite_zone(lower, upper):
                active.append(ZoneStateV53(f"fvg-bull-{i}", ZoneKind.FVG_BULL, lower, upper, i))
        if i >= 2 and float(x["ict_fvg_bear"].iloc[i]) == 1.0:
            lower = float(x["high"].iloc[i])
            upper = float(x["low"].iloc[i - 2])
            if _finite_zone(lower, upper):
                active.append(ZoneStateV53(f"fvg-bear-{i}", ZoneKind.FVG_BEAR, lower, upper, i))

        if float(x["ict_bull_ob_candidate"].iloc[i]) == 1.0:
            lower = float(x["ict_bull_ob_lower"].iloc[i]); upper = float(x["ict_bull_ob_upper"].iloc[i])
            if _finite_zone(lower, upper):
                active.append(ZoneStateV53(f"ob-bull-{i}", ZoneKind.OB_BULL, lower, upper, i))
        if float(x["ict_bear_ob_candidate"].iloc[i]) == 1.0:
            lower = float(x["ict_bear_ob_lower"].iloc[i]); upper = float(x["ict_bear_ob_upper"].iloc[i])
            if _finite_zone(lower, upper):
                active.append(ZoneStateV53(f"ob-bear-{i}", ZoneKind.OB_BEAR, lower, upper, i))

        survivors: list[ZoneStateV53] = []
        for zone in active:
            if zone.formed_index == i:
                survivors.append(zone)
                continue

            overlap = zone.overlaps(low, high)
            if overlap:
                zone.touches += 1

            if zone.kind == ZoneKind.FVG_BULL:
                if low <= zone.lower:
                    zone.status = ZoneStatus.FILLED; fvg_fill_bull[i] = 1.0; continue
                if overlap:
                    zone.status = ZoneStatus.MITIGATED
            elif zone.kind == ZoneKind.FVG_BEAR:
                if high >= zone.upper:
                    zone.status = ZoneStatus.FILLED; fvg_fill_bear[i] = 1.0; continue
                if overlap:
                    zone.status = ZoneStatus.MITIGATED
            elif zone.kind == ZoneKind.OB_BULL:
                if close < zone.lower:
                    zone.status = ZoneStatus.INVALIDATED; zone.invalidated_index = i; ob_inv_bull[i] = 1.0
                    pending_breakers.append((i + 1, ZoneStateV53(f"breaker-bear-{zone.zone_id}", ZoneKind.BREAKER_BEAR, zone.lower, zone.upper, i)))
                    continue
                if overlap:
                    zone.status = ZoneStatus.MITIGATED; ob_mit_bull[i] = 1.0
            elif zone.kind == ZoneKind.OB_BEAR:
                if close > zone.upper:
                    zone.status = ZoneStatus.INVALIDATED; zone.invalidated_index = i; ob_inv_bear[i] = 1.0
                    pending_breakers.append((i + 1, ZoneStateV53(f"breaker-bull-{zone.zone_id}", ZoneKind.BREAKER_BULL, zone.lower, zone.upper, i)))
                    continue
                if overlap:
                    zone.status = ZoneStatus.MITIGATED; ob_mit_bear[i] = 1.0
            elif zone.kind == ZoneKind.BREAKER_BULL:
                if close < zone.lower:
                    zone.status = ZoneStatus.INVALIDATED; continue
                if overlap and close > zone.upper:
                    breaker_retest_bull[i] = 1.0
            elif zone.kind == ZoneKind.BREAKER_BEAR:
                if close > zone.upper:
                    zone.status = ZoneStatus.INVALIDATED; continue
                if overlap and close < zone.lower:
                    breaker_retest_bear[i] = 1.0
            survivors.append(zone)
        active = survivors

        def zones(kind: ZoneKind) -> list[ZoneStateV53]:
            return [z for z in active if z.kind == kind and z.status != ZoneStatus.INVALIDATED]

        fb, fs = zones(ZoneKind.FVG_BULL), zones(ZoneKind.FVG_BEAR)
        obb, obs = zones(ZoneKind.OB_BULL), zones(ZoneKind.OB_BEAR)
        active_fvg_bull[i], active_fvg_bear[i] = len(fb), len(fs)
        active_ob_bull[i], active_ob_bear[i] = len(obb), len(obs)

        if fb:
            nearest_bull_fvg_dist[i] = min(abs(close - z.upper) / close for z in fb)
        if fs:
            nearest_bear_fvg_dist[i] = min(abs(close - z.lower) / close for z in fs)
        if obb:
            nearest_bull_ob_dist[i] = min(abs(close - z.upper) / close for z in obb)
        if obs:
            nearest_bear_ob_dist[i] = min(abs(close - z.lower) / close for z in obs)

    x["ict_active_fvg_bull_count"] = active_fvg_bull
    x["ict_active_fvg_bear_count"] = active_fvg_bear
    x["ict_fvg_bull_fill"] = fvg_fill_bull
    x["ict_fvg_bear_fill"] = fvg_fill_bear
    x["ict_nearest_bull_fvg_distance_pct"] = nearest_bull_fvg_dist
    x["ict_nearest_bear_fvg_distance_pct"] = nearest_bear_fvg_dist
    x["ict_active_bull_ob_count"] = active_ob_bull
    x["ict_active_bear_ob_count"] = active_ob_bear
    x["ict_bull_ob_mitigation"] = ob_mit_bull
    x["ict_bear_ob_mitigation"] = ob_mit_bear
    x["ict_bull_ob_invalidation"] = ob_inv_bull
    x["ict_bear_ob_invalidation"] = ob_inv_bear
    x["ict_bull_breaker_retest"] = breaker_retest_bull
    x["ict_bear_breaker_retest"] = breaker_retest_bear
    x["ict_nearest_bull_ob_distance_pct"] = nearest_bull_ob_dist
    x["ict_nearest_bear_ob_distance_pct"] = nearest_bear_ob_dist
    return x
