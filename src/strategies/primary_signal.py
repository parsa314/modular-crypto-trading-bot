"""Closed-bar EMA20/50 crossover; emitted entries require ML admission."""
from __future__ import annotations

import numpy as np
import pandas as pd


class PrimarySignal:
    def __init__(self, ml_model=None):
        self.ml_model = ml_model

    @staticmethod
    def raw_entries(bars: pd.DataFrame) -> pd.Series:
        fast = bars.close.ewm(span=20, adjust=False, min_periods=20).mean()
        slow = bars.close.ewm(span=50, adjust=False, min_periods=50).mean()
        return (fast > slow) & (fast.shift() <= slow.shift())

    def filter_candidates(self, features: pd.DataFrame) -> pd.DataFrame:
        if self.ml_model is None:
            raise ValueError("PrimarySignal needs an explicitly supplied fitted meta-labeler")
        probability = np.asarray(self.ml_model.predict_proba(features), dtype=float)
        if probability.ndim != 1 or len(probability) != len(features) or not np.isfinite(probability).all():
            raise ValueError("Invalid meta-labeler probabilities")
        result = features.copy()
        result["ml_probability"] = probability
        return result[result.ml_probability >= self.ml_model.threshold].copy()

    def generate(self, bars: pd.DataFrame, current_features: pd.DataFrame):
        if not len(bars) or not self.raw_entries(bars).iloc[-1]:
            return None
        if len(current_features) != 1 or pd.Timestamp(current_features.available_at.iloc[0]) != pd.Timestamp(bars.available_at.iloc[-1]):
            raise ValueError("Inference features must match this exact closed-bar decision")
        approved = self.filter_candidates(current_features)
        if not len(approved):
            return None
        row = approved.iloc[0]
        return {"strategy": "primary_ema20_50", "direction": "BUY", "available_at": row.available_at,
                "confidence": float(row.signal_confidence), "ml_probability": float(row.ml_probability)}
