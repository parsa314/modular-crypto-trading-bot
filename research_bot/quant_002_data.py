"""Strict OHLCV resampling for offline research; no data download or brokerage."""
from __future__ import annotations
import pandas as pd
import numpy as np


def complete_bars(m15: pd.DataFrame, *, hours: int) -> tuple[pd.DataFrame, tuple[str, ...]]:
    """Aggregate only groups containing every expected M15 open timestamp.

    Returned availability assumes each constituent is known at its close;
    this is not evidence of historical first-seen or intrabar trade sequence.
    """
    if hours not in (1,4):
        raise ValueError("UNSUPPORTED_TIMEFRAME")
    needed={"timestamp","open","high","low","close","volume"}
    if not needed.issubset(m15.columns):
        raise ValueError("MISSING_OHLCV")
    x=m15[list(needed)].copy()
    x["timestamp"]=pd.to_datetime(x.timestamp,utc=True,errors="raise")
    if x.timestamp.isna().any() or x.timestamp.duplicated().any() or not x.timestamp.is_monotonic_increasing:
        raise ValueError("DUPLICATE_OR_UNSORTED_M15")
    if not x.timestamp.dt.floor("15min").equals(x.timestamp):
        raise ValueError("M15_MISALIGNED")
    fields=["open","high","low","close","volume"]
    for field in fields:
        x[field]=pd.to_numeric(x[field],errors="raise")
    if not np.isfinite(x[fields].to_numpy(dtype=float)).all():
        raise ValueError("NONFINITE_M15")
    if (x[["open","high","low","close"]] <= 0).any().any() or (x.volume < 0).any():
        raise ValueError("INVALID_M15_PRICE_OR_VOLUME")
    if ((x.low > x[["open","close"]].min(axis=1)).any()
        or (x.high < x[["open","close"]].max(axis=1)).any() or (x.low > x.high).any()):
        raise ValueError("INCONSISTENT_M15_OHLC")
    n=hours*4
    rows=[]; rejected=[]
    for stamp,g in x.groupby(x.timestamp.dt.floor(f"{hours}h"),sort=True):
        expected=pd.date_range(stamp,periods=n,freq="15min")
        if len(g) != n or not g.timestamp.reset_index(drop=True).equals(pd.Series(expected)):
            rejected.append(stamp.isoformat())
            continue
        rows.append({"timestamp":stamp,"available_at":stamp+pd.Timedelta(hours=hours),
            "open":g.open.iloc[0],"high":g.high.max(),"low":g.low.min(),
            "close":g.close.iloc[-1],"volume":g.volume.sum()})
    return pd.DataFrame(rows,columns=["timestamp","available_at","open","high","low","close","volume"]),tuple(rejected)
