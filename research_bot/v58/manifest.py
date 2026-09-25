from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

import numpy as np
import pandas as pd


def _json_default(value: Any):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        v = float(value)
        return v if np.isfinite(v) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def stable_frame_hash(frame: pd.DataFrame) -> str:
    x = frame.copy()
    for col in x.columns:
        if pd.api.types.is_datetime64_any_dtype(x[col]):
            x[col] = pd.to_datetime(x[col], utc=True, errors="raise").astype(str)
    raw = x.to_csv(index=False, float_format="%.12g", lineterminator="\n").encode("utf-8")
    return sha256(raw).hexdigest()


def stable_schema_hash(frame: pd.DataFrame) -> str:
    schema = [(str(c), str(frame[c].dtype)) for c in frame.columns]
    raw = json.dumps(schema, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return sha256(raw).hexdigest()


def build_dataset_manifest(
    frame: pd.DataFrame,
    *,
    source: str,
    symbol: str,
    venue: str,
    timeframe: str,
    data_version: str,
) -> dict:
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")
    x = frame.copy()
    x["timestamp"] = pd.to_datetime(x["timestamp"], utc=True, errors="raise")
    x = x.sort_values("timestamp", kind="mergesort").reset_index(drop=True)
    if x["timestamp"].duplicated().any():
        raise ValueError("duplicate timestamps in dataset")
    if x.empty:
        raise ValueError("empty dataset")
    deltas = x["timestamp"].diff().dropna()
    expected = pd.Timedelta(timeframe)
    missing_interval_count = int((deltas > expected).sum()) if len(deltas) else 0
    return {
        "source": source,
        "symbol": symbol,
        "venue": venue,
        "timeframe": timeframe,
        "data_version": data_version,
        "rows": int(len(x)),
        "start": x["timestamp"].iloc[0].isoformat(),
        "end": x["timestamp"].iloc[-1].isoformat(),
        "duplicate_timestamps": 0,
        "gap_count": missing_interval_count,
        "frame_sha256": stable_frame_hash(x),
        "schema_sha256": stable_schema_hash(x),
        "pit_status": "PRICE_OHLCV_TIMESTAMPED",
    }


def manifest_hash(manifest: dict) -> str:
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":"), default=_json_default).encode("utf-8")
    return sha256(raw).hexdigest()
