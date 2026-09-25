from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import numpy as np
import pandas as pd

from .manifest import stable_frame_hash, stable_schema_hash


@dataclass(frozen=True)
class FrozenFileSpec:
    symbol: str
    file_name: str
    compressed_sha256: str
    frame_sha256: str
    schema_sha256: str
    rows: int
    first_bar: str
    last_bar: str
    timeframe: str = "4h"


def verify_frozen_ohlcv(path: str | Path, spec: FrozenFileSpec) -> dict:
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(file_path)
    digest = sha256(file_path.read_bytes()).hexdigest()
    if digest != spec.compressed_sha256:
        raise ValueError(f"compressed SHA-256 mismatch for {spec.symbol}")

    frame = pd.read_csv(file_path, compression="gzip")
    required = ["timestamp", "open", "high", "low", "close", "volume"]
    if list(frame.columns) != required:
        raise ValueError(f"unexpected schema for {spec.symbol}: {list(frame.columns)}")
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="raise")
    for col in required[1:]:
        frame[col] = pd.to_numeric(frame[col], errors="raise")
    if len(frame) != spec.rows:
        raise ValueError(f"row-count mismatch for {spec.symbol}")
    if frame["timestamp"].duplicated().any():
        raise ValueError(f"duplicate timestamps for {spec.symbol}")
    if frame["timestamp"].iloc[0].isoformat() != spec.first_bar:
        raise ValueError(f"first-bar mismatch for {spec.symbol}")
    if frame["timestamp"].iloc[-1].isoformat() != spec.last_bar:
        raise ValueError(f"last-bar mismatch for {spec.symbol}")
    if not np.isfinite(frame[required[1:]].to_numpy(dtype=float)).all():
        raise ValueError(f"non-finite OHLCV for {spec.symbol}")
    if (frame[["open", "high", "low", "close"]] <= 0).any().any() or (frame["volume"] < 0).any():
        raise ValueError(f"invalid non-positive values for {spec.symbol}")
    if (frame["high"] < frame[["open", "close", "low"]].max(axis=1)).any():
        raise ValueError(f"invalid high geometry for {spec.symbol}")
    if (frame["low"] > frame[["open", "close"]].min(axis=1)).any():
        raise ValueError(f"invalid low geometry for {spec.symbol}")
    step = pd.Timedelta(hours=4)
    deltas = frame["timestamp"].diff().dropna()
    if not (deltas == step).all():
        raise ValueError(f"non-gapless 4h grid for {spec.symbol}")
    frame_digest = stable_frame_hash(frame)
    schema_digest = stable_schema_hash(frame)
    if frame_digest != spec.frame_sha256:
        raise ValueError(f"frame SHA-256 mismatch for {spec.symbol}")
    if schema_digest != spec.schema_sha256:
        raise ValueError(f"schema SHA-256 mismatch for {spec.symbol}")
    return {
        "symbol": spec.symbol,
        "rows": len(frame),
        "first_bar": frame["timestamp"].iloc[0].isoformat(),
        "last_bar": frame["timestamp"].iloc[-1].isoformat(),
        "compressed_sha256": digest,
        "frame_sha256": frame_digest,
        "schema_sha256": schema_digest,
        "gap_intervals": int((deltas != step).sum()),
    }


def frozen_60_20_20_split(frame: pd.DataFrame) -> dict:
    if frame.empty:
        raise ValueError("cannot split empty frame")
    x = frame.copy()
    x["timestamp"] = pd.to_datetime(x["timestamp"], utc=True, errors="raise")
    x = x.sort_values("timestamp", kind="mergesort").reset_index(drop=True)
    n = len(x)
    dev_n = int(n * 0.60)
    val_end_n = int(n * 0.80)
    if dev_n <= 0 or val_end_n <= dev_n or val_end_n >= n:
        raise ValueError("insufficient rows for frozen 60/20/20 split")
    return {
        "development": {
            "rows": dev_n,
            "start": x.iloc[0]["timestamp"].isoformat(),
            "end": x.iloc[dev_n - 1]["timestamp"].isoformat(),
        },
        "validation": {
            "rows": val_end_n - dev_n,
            "start": x.iloc[dev_n]["timestamp"].isoformat(),
            "end": x.iloc[val_end_n - 1]["timestamp"].isoformat(),
        },
        "internal_test_spent": {
            "rows": n - val_end_n,
            "start": x.iloc[val_end_n]["timestamp"].isoformat(),
            "end": x.iloc[-1]["timestamp"].isoformat(),
        },
    }
