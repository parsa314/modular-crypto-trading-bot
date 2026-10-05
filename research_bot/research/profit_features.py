"""Causal features for the separately preregistered BTC spot baseline study.

OHLCV timestamps denote bar opens. Features at t are available only at t+1h;
the earliest permitted fill is the following bar's open. Existing feature
definitions are reused rather than changing historical experiment code.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research_bot.ensemble_features import validate_ohlcv
from research_bot.features import FEATURE_COLUMNS as LEGACY_FEATURE_COLUMNS
from research_bot.features import add_features


FEATURE_COLUMNS = tuple(LEGACY_FEATURE_COLUMNS)
FEATURE_WARMUP_BARS = 96
BAR_DURATION = pd.Timedelta(hours=1)


def build_profit_features(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """Preserve rows, leave warm-up missing, and mark finite closed-bar features.

Flat-price RSI is 0.5; a one-sided rise/fall is 1/0. Constant-volume z-score
is zero. These explicit conventions apply only to this new experiment.
Zero-volume Amihud observations remain unavailable, rather than inventing
liquidity. No feature claims to observe order-book OFI from OHLCV candles.
"""
    source = validate_ohlcv(ohlcv)
    if not source["timestamp"].diff().iloc[1:].eq(BAR_DURATION).all():
        raise ValueError("profit baseline requires contiguous 1h OHLCV")
    x = add_features(source)
    delta = source["close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    x["rsi_14"] = gain.div(gain + loss).mask(gain.eq(0) & loss.eq(0), 0.5)
    volume_std = source["volume"].rolling(24).std()
    x["volume_z_24"] = x["volume_z_24"].mask(volume_std.eq(0), 0.0)
    x["feature_available_at"] = source["timestamp"] + BAR_DURATION
    x["feature_ready"] = np.isfinite(x[list(FEATURE_COLUMNS)].to_numpy()).all(axis=1)
    return x


def add_profit_labels(
    featured: pd.DataFrame,
    *,
    horizon_bars: int = 24,
    fee_bps: float = 10.0,
    slippage_bps: float = 5.0,
) -> pd.DataFrame:
    """Label whether next-open to t+horizon close beats both execution costs.

The target is an outcome, never a model input. End-of-dataset rows have missing
targets. Label availability includes the future exit candle's closing time.
"""
    if isinstance(horizon_bars, bool) or not isinstance(horizon_bars, int) or horizon_bars < 1:
        raise ValueError("horizon_bars must be a positive integer")
    for name, value in (("fee_bps", fee_bps), ("slippage_bps", slippage_bps)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or not 0 <= value < 1000:
            raise ValueError(f"{name} must be finite and in [0, 1000)")
    x = featured.copy()
    fee, slip = fee_bps / 10_000, slippage_bps / 10_000
    entry = x["open"].shift(-1) * (1 + slip) * (1 + fee)
    exit_proceeds = x["close"].shift(-horizon_bars) * (1 - slip) * (1 - fee)
    x["target_net_return"] = exit_proceeds / entry - 1
    x["target"] = x["target_net_return"].gt(0).astype(float).where(x["target_net_return"].notna())
    x["label_end"] = x["timestamp"].shift(-horizon_bars) + BAR_DURATION
    return x
