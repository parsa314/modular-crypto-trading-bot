"""Causal V59 spot hypotheses, separate from future-path outcome labeling.

Confluence definitions are selectively ported from V58, not re-tuned. Signals
use the *visible* cloud and closed bars. The reference price is the observed
signal close, never a future fill. With strictly-later execution required by
V59, the first admissible hourly/bar-open entry is one full bar after the
decision close; no fictional timestamp before availability is subtracted.
"""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import numpy as np
import pandas as pd

from .contracts import Direction, Regime, SignalCandidate
from .hashing import stable_hash
from .market_data import timeframe_delta, utc_timestamp
from .native_confluence import evaluate_confluence10
from .native_features import add_native_features


STRATEGY_VERSION = "V59_NATIVE_CAUSAL_1"
MODEL_FEATURES = ("ATR_percent", "returns_1", "returns_3", "returns_6", "returns_12",
                  "EMA_distance", "trend_slope", "tenkan_kijun_distance_atr",
                  "price_kijun_distance_atr", "price_cloud_distance_atr",
                  "cloud_width_atr", "trend_strength", "signal_bar_quality")


@dataclass(frozen=True)
class NativeStrategyConfig:
    timeframe: str = "4h"
    horizon_bars: int = 12
    stop_atr: float = 1.0
    reward_r: float = 2.0
    minimum_history: int = 96

    def __post_init__(self):
        timeframe_delta(self.timeframe)
        for name in ("horizon_bars", "minimum_history"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.minimum_history < 96:
            raise ValueError("Visible cloud and causal feature warmup require at least 96 bars")
        for name in ("stop_atr", "reward_r"):
            value = getattr(self, name)
            if isinstance(value, bool) or not np.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive finite data")


def code_hash() -> str:
    root = Path(__file__).parent
    return stable_hash({n: sha256((root / n).read_bytes()).hexdigest()
                        for n in ("native_strategy.py", "native_features.py", "native_confluence.py")})


def scan_native(frame: pd.DataFrame, *, venue: str, symbol: str, data_version: str,
                as_of, config: NativeStrategyConfig | None = None) -> tuple[list[SignalCandidate], dict]:
    cfg = config or NativeStrategyConfig()
    as_of = utc_timestamp(as_of, "as_of")
    delta = timeframe_delta(cfg.timeframe)
    frame = frame.copy()
    # Reject naive input rather than silently interpreting local wall clocks.
    frame["timestamp"] = [utc_timestamp(t, "timestamp") for t in frame.timestamp]
    if (venue.lower() == "coinex" and symbol in {"BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT"}
            and (frame.timestamp + delta > pd.Timestamp("2026-09-25T00:00:00Z")).any()):
        raise ValueError("SEALED_V58_FINAL_HOLDOUT: native development cannot consume this CoinEx spot period")
    frame = frame.loc[frame.timestamp + delta <= as_of].reset_index(drop=True)
    if frame.empty:
        return [], {}
    features = add_native_features(frame, timeframe=cfg.timeframe)
    signals, snapshots = [], {}
    chain = stable_hash({"venue": venue, "symbol": symbol, "timeframe": cfg.timeframe})
    implementation_hash = code_hash()
    for i, row in features.iterrows():
        observation = {"timestamp": row.timestamp.isoformat(),
                       **{n: float(row[n]) for n in ("open", "high", "low", "close", "volume")}}
        chain = stable_hash({"previous": chain, "bar": observation})
        if i + 1 < cfg.minimum_history or not np.isfinite(row[list(MODEL_FEATURES)].to_numpy(float)).all():
            continue
        values = {n: float(row[n]) for n in MODEL_FEATURES}
        decision = row.timestamp + delta
        snapshot = {"available_at": decision.isoformat(), "prefix_hash": chain,
                    "features": values, "code_hash": implementation_hash}
        snapshot_id = stable_hash(snapshot)
        hits = [(hit.strategy_id, hit.states) for hit in evaluate_confluence10(row.to_dict())]
        # Transparent non-ML reference strategy, included in the same cost path.
        if row.price_cloud_distance_atr > 0 and row.tenkan_kijun_distance_atr > 0:
            hits.append(("ICHIMOKU_VISIBLE_CLOUD", ("VISIBLE_CLOUD_BULL", "TENKAN_ABOVE_KIJUN")))
        reference = float(row.close)
        atr_value = float(row.ATR_percent * reference)
        stop, target = reference - cfg.stop_atr * atr_value, reference + cfg.stop_atr * atr_value * cfg.reward_r
        if stop <= 0 or not stop < reference < target:
            continue
        for strategy, confirmations in hits:
            event_id = stable_hash({"identity_version": "NATIVE_EVENT_ID_V2", "strategy": strategy,
                                   "symbol": symbol, "venue": venue, "direction": "LONG",
                                   "decision_at": decision.isoformat(), "entry_time": (decision+delta).isoformat(),
                                   "snapshot": snapshot_id, "data_version": data_version,
                                   "version": STRATEGY_VERSION, "config": cfg.__dict__})
            signals.append(SignalCandidate(
                event_id=event_id, strategy_id=strategy, strategy_family="NATIVE_CONFLUENCE" if strategy.startswith("C10_") else "ICHIMOKU",
                symbol=symbol, venue=venue, direction=Direction.LONG,
                decision_at=decision.to_pydatetime(), entry_time=(decision + delta).to_pydatetime(),
                entry_price=reference, stop_price=stop, target_price=target,
                horizon_bars=cfg.horizon_bars, regime=Regime.UNKNOWN, regime_confidence=0.0,
                feature_snapshot_id=snapshot_id, data_version=data_version,
                strategy_version=STRATEGY_VERSION, source_hash=chain,
                confirmations=tuple(confirmations)))
            snapshots[snapshot_id] = snapshot
    return signals, snapshots
