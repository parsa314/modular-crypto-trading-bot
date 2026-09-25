from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd

from .contracts import RegimeLabel
from .features import add_v58_continuous_features


@dataclass(frozen=True)
class RegimeConfig:
    trend_threshold_atr: float = 0.35
    transition_delta: float = 0.75
    vol_window: int = 72
    min_periods: int = 24


def add_causal_regime(frame: pd.DataFrame, config: RegimeConfig | None = None) -> pd.DataFrame:
    cfg = config or RegimeConfig()
    x = add_v58_continuous_features(frame)
    rv = x["realized_volatility"]
    prior_rv = rv.shift(1)
    vol_med = prior_rv.rolling(cfg.vol_window, min_periods=cfg.min_periods).median()
    vol_q25 = prior_rv.rolling(cfg.vol_window, min_periods=cfg.min_periods).quantile(0.25)
    vol_q75 = prior_rv.rolling(cfg.vol_window, min_periods=cfg.min_periods).quantile(0.75)
    trend = x["trend_strength"]
    trend_delta = (trend - trend.shift(1)).abs()

    labels: list[str] = []
    confidence: list[float] = []
    for i in range(len(x)):
        t = trend.iloc[i]
        d = trend_delta.iloc[i]
        r = rv.iloc[i]
        med = vol_med.iloc[i]
        q25 = vol_q25.iloc[i]
        q75 = vol_q75.iloc[i]
        if any(pd.isna(v) for v in (t, r, med, q25, q75)):
            labels.append(RegimeLabel.UNKNOWN.value)
            confidence.append(0.0)
            continue
        if not pd.isna(d) and d >= cfg.transition_delta:
            label = RegimeLabel.TRANSITION
            conf = min(1.0, float(d / max(cfg.transition_delta, 1e-12) - 1.0 + 0.5))
        elif r >= q75:
            label = RegimeLabel.HIGH_VOL
            conf = min(1.0, 0.5 + float((r - q75) / max(abs(q75), 1e-12)))
        elif r <= q25:
            label = RegimeLabel.LOW_VOL
            conf = min(1.0, 0.5 + float((q25 - r) / max(abs(q25), 1e-12)))
        elif t >= cfg.trend_threshold_atr:
            label = RegimeLabel.TREND_UP
            conf = min(1.0, abs(float(t)) / max(cfg.trend_threshold_atr * 2.0, 1e-12))
        elif t <= -cfg.trend_threshold_atr:
            label = RegimeLabel.TREND_DOWN
            conf = min(1.0, abs(float(t)) / max(cfg.trend_threshold_atr * 2.0, 1e-12))
        else:
            label = RegimeLabel.RANGE
            conf = max(0.0, 1.0 - abs(float(t)) / max(cfg.trend_threshold_atr, 1e-12))
        labels.append(label.value)
        confidence.append(float(np.clip(conf, 0.0, 1.0)))

    x["regime"] = labels
    x["regime_confidence"] = confidence
    x["regime_available_at"] = x["timestamp"]
    return x
