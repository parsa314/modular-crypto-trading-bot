from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd

from .hashing import stable_hash
from .market_data import OHLCV_COLUMNS, resample_causal_complete, timeframe_delta


@dataclass(frozen=True)
class TimeframeView:
    timeframe: str
    rows: int
    coverage_start: str | None
    coverage_end: str | None
    dataset_hash: str


def build_timeframe_views(
    base: pd.DataFrame,
    *,
    source_timeframe: str,
    targets: Iterable[str],
) -> tuple[dict[str, pd.DataFrame], tuple[TimeframeView, ...]]:
    if base.empty:
        raise ValueError("base dataset must be nonempty")
    target_list = tuple(targets)
    if not target_list:
        raise ValueError("at least one target timeframe is required")
    if len(target_list) != len(set(target_list)):
        raise ValueError("target timeframes must be unique")

    views: dict[str, pd.DataFrame] = {}
    meta: list[TimeframeView] = []
    for target in target_list:
        if timeframe_delta(target) <= timeframe_delta(source_timeframe):
            raise ValueError("every target timeframe must exceed source timeframe")
        frame = resample_causal_complete(
            base,
            source_timeframe=source_timeframe,
            target_timeframe=target,
        )
        rows = [
            {
                **row,
                "timestamp": pd.Timestamp(row["timestamp"]).isoformat(),
            }
            for row in frame.loc[:, list(OHLCV_COLUMNS)].to_dict(orient="records")
        ]
        dataset_hash = stable_hash(
            {
                "source_timeframe": source_timeframe,
                "target_timeframe": target,
                "rows": rows,
            }
        )
        views[target] = frame
        meta.append(
            TimeframeView(
                timeframe=target,
                rows=len(frame),
                coverage_start=None if frame.empty else frame["timestamp"].iloc[0].isoformat(),
                coverage_end=None if frame.empty else frame["timestamp"].iloc[-1].isoformat(),
                dataset_hash=dataset_hash,
            )
        )
    return views, tuple(meta)
