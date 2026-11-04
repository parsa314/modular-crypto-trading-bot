from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Mapping

import numpy as np
import pandas as pd

from .features import add_features
from .ichimoku_advanced import detect_kumo_triangle_breakout
from .regime import add_regime_features


@dataclass(frozen=True)
class FastScanConfig:
    min_bars: int = 240
    max_missing_fraction: float = 0.01
    max_abnormal_fraction: float = 0.01
    min_recent_quote_volume: float | None = None
    candidate_quantile: float = 0.80


@dataclass(frozen=True)
class FastScanRow:
    symbol: str
    timestamp: str | None
    status: str
    rejection_reasons: tuple[str, ...]
    raw_score: float | None
    score_percentile: float | None
    triangle_candidate: bool
    trend_state: str
    high_vol: bool
    metrics: dict


def _abnormal_fraction(df: pd.DataFrame) -> float:
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(df.columns) or df.empty:
        return 1.0
    x = df[list(required)].apply(pd.to_numeric, errors="coerce")
    bad = (
        (~np.isfinite(x).all(axis=1))
        | (x["high"] < x[["open", "close", "low"]].max(axis=1))
        | (x["low"] > x[["open", "close", "high"]].min(axis=1))
        | (x[["open", "high", "low", "close"]] <= 0).any(axis=1)
        | (x["volume"] < 0)
    )
    return float(bad.mean())


def _trend_state(row: pd.Series) -> str:
    if float(row.get("regime_trend_up", 0.0)) == 1.0:
        return "BULL"
    if float(row.get("regime_trend_down", 0.0)) == 1.0:
        return "BEAR"
    return "SIDEWAYS"


def _raw_opportunity_score(row: pd.Series) -> float:
    """Transparent fast-screen score, not a learned alpha model.

    The score intentionally uses a small, interpretable feature set.  It ranks
    candidates for deeper analysis and must not be labelled a confirmed entry.
    """

    mom = np.nanmean([float(row.get("mom_6", np.nan)), float(row.get("mom_24", np.nan))])
    trend = float(row.get("price_to_ma_24", np.nan))
    tk = float(row.get("ichi_tenkan_kijun", np.nan))
    cloud = -abs(float(row.get("ichi_price_kijun", np.nan)))
    vol_z = float(row.get("volume_z_24", np.nan))
    range_penalty = abs(float(row.get("range_pct", np.nan)))
    high_vol_penalty = 0.01 if float(row.get("regime_high_vol", 0.0)) == 1.0 else 0.0

    vals = [mom, trend, tk, cloud, 0.002 * vol_z, -range_penalty, -high_vol_penalty]
    vals = [v for v in vals if np.isfinite(v)]
    return float(sum(vals)) if vals else np.nan


def scan_symbol(symbol: str, bars: pd.DataFrame, config: FastScanConfig | None = None) -> FastScanRow:
    cfg = config or FastScanConfig()
    reasons: list[str] = []
    if len(bars) < cfg.min_bars:
        reasons.append("INSUFFICIENT_HISTORY")
    missing_fraction = float(bars.isna().any(axis=1).mean()) if len(bars) else 1.0
    if missing_fraction > cfg.max_missing_fraction:
        reasons.append("TOO_MANY_MISSING_ROWS")
    abnormal_fraction = _abnormal_fraction(bars)
    if abnormal_fraction > cfg.max_abnormal_fraction:
        reasons.append("ABNORMAL_CANDLES")
    # Do not silently fall back to an older bar when the newest candle is bad.
    if not bars.empty and _abnormal_fraction(bars.tail(1)) > 0:
        reasons.append("LATEST_CANDLE_INVALID")

    if reasons:
        return FastScanRow(
            symbol=symbol,
            timestamp=None,
            status="REJECT",
            rejection_reasons=tuple(reasons),
            raw_score=None,
            score_percentile=None,
            triangle_candidate=False,
            trend_state="UNKNOWN",
            high_vol=False,
            metrics={"rows": len(bars), "missing_fraction": missing_fraction, "abnormal_fraction": abnormal_fraction},
        )

    x = add_features(bars)
    x = add_regime_features(x)
    x = detect_kumo_triangle_breakout(x)
    usable = x.dropna(subset=["close", "mom_24", "price_to_ma_24", "ichi_tenkan_kijun"])
    if usable.empty:
        return FastScanRow(
            symbol=symbol,
            timestamp=None,
            status="REJECT",
            rejection_reasons=("NO_USABLE_FEATURE_ROW",),
            raw_score=None,
            score_percentile=None,
            triangle_candidate=False,
            trend_state="UNKNOWN",
            high_vol=False,
            metrics={"rows": len(bars), "missing_fraction": missing_fraction, "abnormal_fraction": abnormal_fraction},
        )

    row = usable.iloc[-1]
    score = _raw_opportunity_score(row)
    return FastScanRow(
        symbol=symbol,
        timestamp=pd.Timestamp(row["timestamp"]).isoformat(),
        status="SCANNED",
        rejection_reasons=(),
        raw_score=score if np.isfinite(score) else None,
        score_percentile=None,
        triangle_candidate=bool(float(row.get("triangle_candidate", 0.0)) == 1.0),
        trend_state=_trend_state(row),
        high_vol=bool(float(row.get("regime_high_vol", 0.0)) == 1.0),
        metrics={
            "close": float(row["close"]),
            "mom_6": float(row.get("mom_6", np.nan)),
            "mom_24": float(row.get("mom_24", np.nan)),
            "vol_24": float(row.get("vol_24", np.nan)),
            "volume_z_24": float(row.get("volume_z_24", np.nan)),
            "atr_14_pct": float(row.get("atr_14_pct", np.nan)),
            "ichi_tk_distance_pct": float(row.get("ichi_tk_distance_pct", np.nan)),
            "ichi_cloud_width_pct": float(row.get("ichi_cloud_width_pct", np.nan)),
            "rows": len(bars),
            "missing_fraction": missing_fraction,
            "abnormal_fraction": abnormal_fraction,
        },
    )


def scan_universe(
    symbol_bars: Mapping[str, pd.DataFrame],
    config: FastScanConfig | None = None,
) -> pd.DataFrame:
    """Scan an arbitrary dynamic symbol universe and return rankable candidates."""

    cfg = config or FastScanConfig()
    rows = [scan_symbol(symbol, bars, cfg) for symbol, bars in symbol_bars.items()]
    frame = pd.DataFrame([asdict(r) for r in rows])
    if frame.empty:
        return frame

    scanned = frame["status"].eq("SCANNED") & frame["raw_score"].notna()
    if scanned.any():
        frame.loc[scanned, "score_percentile"] = frame.loc[scanned, "raw_score"].rank(pct=True, method="average")
        candidate = scanned & (frame["score_percentile"] >= cfg.candidate_quantile)
        frame.loc[candidate, "status"] = "DEEP_ANALYSIS_CANDIDATE"
    return frame.sort_values(["status", "score_percentile"], ascending=[True, False], na_position="last").reset_index(drop=True)
