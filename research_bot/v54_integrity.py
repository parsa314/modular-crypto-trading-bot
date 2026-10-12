from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from math import ceil
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class V54FeatureHealthConfig:
    max_missing_fraction: float = 0.95
    min_non_null: int = 60
    min_variance: float = 1e-14
    max_abs_pairwise_corr: float = 0.999999

    def __post_init__(self) -> None:
        if not 0 <= self.max_missing_fraction < 1:
            raise ValueError("max_missing_fraction must be in [0,1)")
        if self.min_non_null < 2:
            raise ValueError("min_non_null must be >=2")
        if self.min_variance < 0:
            raise ValueError("min_variance must be non-negative")
        if not 0 < self.max_abs_pairwise_corr <= 1:
            raise ValueError("max_abs_pairwise_corr must be in (0,1]")


def canonical_frame_sha256(frame: pd.DataFrame, columns: Iterable[str]) -> str:
    cols = [c for c in columns if c in frame.columns]
    if not cols:
        raise ValueError("no columns available for canonical fingerprint")
    x = frame[cols].copy()
    for col in x.columns:
        if pd.api.types.is_datetime64_any_dtype(x[col]) or col.endswith("_at") or col == "timestamp":
            x[col] = pd.to_datetime(x[col], utc=True, errors="raise").astype(str)
    payload = x.to_csv(index=False, float_format="%.12g", lineterminator="\n").encode("utf-8")
    return sha256(payload).hexdigest()


def schema_sha256(frame: pd.DataFrame) -> str:
    schema = [(str(c), str(frame[c].dtype)) for c in frame.columns]
    raw = json.dumps(schema, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return sha256(raw).hexdigest()


def dataset_manifest_v54(
    frame: pd.DataFrame,
    *,
    symbol: str,
    decision_time_col: str = "decision_at",
    source: str = "CoinEx public spot OHLCV",
) -> dict:
    required = {"timestamp", decision_time_col, "open", "high", "low", "close", "volume"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"manifest missing required columns: {sorted(missing)}")
    if len(frame) == 0:
        raise ValueError("cannot manifest empty frame")
    ts = pd.to_datetime(frame["timestamp"], utc=True, errors="raise")
    decision = pd.to_datetime(frame[decision_time_col], utc=True, errors="raise")
    if ts.duplicated().any() or decision.duplicated().any():
        raise ValueError("manifest frame contains duplicate timestamps")
    # Freeze the complete engineered frame, not only OHLCV. Any later mutation
    # of a derived feature, PIT availability column or market value must alter
    # this fingerprint and fail replay.
    all_columns = list(frame.columns)
    return {
        "protocol": "v0.54",
        "symbol": symbol,
        "source": source,
        "rows": int(len(frame)),
        "bar_open_start": ts.iloc[0].isoformat(),
        "bar_open_end": ts.iloc[-1].isoformat(),
        "decision_start": decision.iloc[0].isoformat(),
        "decision_end": decision.iloc[-1].isoformat(),
        "frame_sha256": canonical_frame_sha256(frame, all_columns),
        "schema_sha256": schema_sha256(frame),
        "columns": all_columns,
        "decision_time_col": decision_time_col,
        "paper_execution": False,
        "live_execution": False,
    }


def feature_health_v54(
    frame: pd.DataFrame,
    features: Iterable[str],
    config: V54FeatureHealthConfig | None = None,
) -> dict:
    cfg = config or V54FeatureHealthConfig()
    rows: dict[str, dict] = {}
    healthy: list[str] = []
    rejected: list[str] = []
    for feature in features:
        if feature not in frame.columns:
            rows[feature] = {"status": "MISSING_COLUMN"}
            rejected.append(feature)
            continue
        s = pd.to_numeric(frame[feature], errors="coerce").replace([np.inf, -np.inf], np.nan)
        non_null = int(s.notna().sum())
        missing_fraction = float(s.isna().mean())
        variance = float(s.dropna().var(ddof=0)) if non_null else float("nan")
        reasons: list[str] = []
        if missing_fraction > cfg.max_missing_fraction:
            reasons.append("TOO_MISSING")
        if non_null < cfg.min_non_null:
            reasons.append("TOO_FEW_OBSERVATIONS")
        if not np.isfinite(variance) or variance <= cfg.min_variance:
            reasons.append("CONSTANT_OR_NEAR_CONSTANT")
        status = "HEALTHY" if not reasons else "REJECTED"
        rows[feature] = {
            "status": status,
            "missing_fraction": missing_fraction,
            "non_null": non_null,
            "variance": variance if np.isfinite(variance) else None,
            "reasons": reasons,
        }
        (healthy if status == "HEALTHY" else rejected).append(feature)

    duplicate_like_pairs: list[dict] = []
    if len(healthy) >= 2:
        corr = frame[healthy].apply(pd.to_numeric, errors="coerce").corr().abs()
        for i, left in enumerate(healthy):
            for right in healthy[i + 1 :]:
                value = corr.loc[left, right]
                if pd.notna(value) and float(value) >= cfg.max_abs_pairwise_corr:
                    duplicate_like_pairs.append({"left": left, "right": right, "abs_corr": float(value)})

    return {
        "config": asdict(cfg),
        "features": rows,
        "healthy_features": healthy,
        "rejected_features": rejected,
        "duplicate_like_pairs": duplicate_like_pairs,
    }


def moving_block_mean_ci_v54(
    diff: pd.Series | np.ndarray,
    *,
    resamples: int = 2000,
    block: int = 24,
    seed: int = 54054,
) -> dict:
    x = pd.Series(diff, dtype=float).replace([np.inf, -np.inf], np.nan).dropna().to_numpy()
    n = len(x)
    if n == 0:
        return {"n": 0, "mean": None, "ci_low": None, "ci_high": None, "p_nonpositive": None}
    mean = float(np.mean(x))
    if n < max(30, 2 * int(block)):
        return {"n": n, "mean": mean, "ci_low": None, "ci_high": None, "p_nonpositive": None}
    if resamples < 100:
        raise ValueError("resamples must be >=100")
    b = max(1, min(int(block), n))
    rng = np.random.default_rng(int(seed))
    starts = np.arange(n)
    blocks_needed = int(ceil(n / b))
    means = np.empty(int(resamples), dtype=float)
    for i in range(int(resamples)):
        sample: list[float] = []
        for _ in range(blocks_needed):
            start = int(rng.choice(starts))
            sample.extend(x[(start + np.arange(b)) % n].tolist())
        means[i] = float(np.mean(sample[:n]))
    return {
        "n": n,
        "mean": mean,
        "ci_low": float(np.quantile(means, 0.025)),
        "ci_high": float(np.quantile(means, 0.975)),
        "p_nonpositive": float((1.0 + np.sum(means <= 0.0)) / (len(means) + 1.0)),
        "block": b,
        "resamples": int(resamples),
    }
