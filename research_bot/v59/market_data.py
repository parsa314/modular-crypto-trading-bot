from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
from typing import Any, Mapping, Protocol

import numpy as np
import pandas as pd

from .hashing import stable_hash


TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1_800,
    "1h": 3_600,
    "2h": 7_200,
    "4h": 14_400,
    "6h": 21_600,
    "12h": 43_200,
    "1d": 86_400,
    "1w": 604_800,
}
OHLCV_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")


def _canonical_text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be a canonical nonempty string")
    return value


def utc_timestamp(value: Any, name: str) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError(f"{name} must be timezone-aware UTC")
    return stamp.tz_convert("UTC")


def timeframe_delta(timeframe: str) -> pd.Timedelta:
    if timeframe not in TIMEFRAME_SECONDS:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    return pd.Timedelta(seconds=TIMEFRAME_SECONDS[timeframe])


@dataclass(frozen=True)
class MarketDataRequest:
    exchange: str
    symbol: str
    timeframe: str
    market_type: str
    since: datetime
    until: datetime
    as_of: datetime
    limit: int = 10_000

    def __post_init__(self) -> None:
        _canonical_text(self.exchange, "exchange")
        _canonical_text(self.symbol, "symbol")
        if self.timeframe not in TIMEFRAME_SECONDS:
            raise ValueError("unsupported timeframe")
        if self.market_type not in {"spot", "swap", "future"}:
            raise ValueError("market_type must be spot/swap/future")
        since = utc_timestamp(self.since, "since")
        until = utc_timestamp(self.until, "until")
        as_of = utc_timestamp(self.as_of, "as_of")
        if not since < until <= as_of:
            raise ValueError("request requires since < until <= as_of")
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or self.limit <= 0:
            raise ValueError("limit must be a positive integer")


@dataclass(frozen=True)
class ProviderOHLCVResult:
    provider_id: str
    exchange: str
    symbol: str
    timeframe: str
    market_type: str
    received_at: datetime
    rows: tuple[tuple[Any, Any, Any, Any, Any, Any], ...]
    raw_payload: bytes
    source_descriptor: str
    provider_metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        for name in ("provider_id", "exchange", "symbol", "timeframe", "market_type", "source_descriptor"):
            _canonical_text(getattr(self, name), name)
        utc_timestamp(self.received_at, "received_at")
        if self.timeframe not in TIMEFRAME_SECONDS:
            raise ValueError("unsupported provider timeframe")
        if self.market_type not in {"spot", "swap", "future"}:
            raise ValueError("invalid provider market_type")
        if not isinstance(self.raw_payload, bytes) or not self.raw_payload:
            raise ValueError("raw_payload must contain provider evidence bytes")


class ReadOnlyOHLCVProvider(Protocol):
    provider_id: str

    def fetch_ohlcv(self, request: MarketDataRequest) -> ProviderOHLCVResult:
        ...


@dataclass(frozen=True)
class DataQualityReport:
    rows_received: int
    rows_closed: int
    duplicates: int
    gap_count: int
    missing_bar_count: int
    off_grid_count: int
    null_count: int
    invalid_candle_count: int
    coverage_start: str | None
    coverage_end: str | None
    expected_spacing_seconds: int
    quality_pass: bool
    reasons: tuple[str, ...]

    @property
    def report_hash(self) -> str:
        return stable_hash(asdict(self))


def _coerce_rows(rows: tuple[tuple[Any, Any, Any, Any, Any, Any], ...]) -> pd.DataFrame:
    frame = pd.DataFrame(list(rows), columns=list(OHLCV_COLUMNS))
    if frame.empty:
        return frame
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="raise")
    for column in OHLCV_COLUMNS[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame


def normalize_and_audit(
    result: ProviderOHLCVResult,
    request: MarketDataRequest,
) -> tuple[pd.DataFrame, DataQualityReport]:
    if result.exchange != request.exchange:
        raise ValueError("provider exchange identity mismatch")
    if result.symbol != request.symbol:
        raise ValueError("provider symbol identity mismatch")
    if result.timeframe != request.timeframe:
        raise ValueError("provider timeframe identity mismatch")
    if result.market_type != request.market_type:
        raise ValueError("provider market_type identity mismatch")

    frame = _coerce_rows(result.rows)
    rows_received = len(frame)
    reasons: list[str] = []
    if frame.empty:
        report = DataQualityReport(
            rows_received=0, rows_closed=0, duplicates=0, gap_count=0,
            missing_bar_count=0, off_grid_count=0, null_count=0,
            invalid_candle_count=0, coverage_start=None, coverage_end=None,
            expected_spacing_seconds=TIMEFRAME_SECONDS[request.timeframe],
            quality_pass=False, reasons=("EMPTY_DATASET",),
        )
        return frame, report

    duplicates = int(frame["timestamp"].duplicated(keep=False).sum())
    if duplicates:
        reasons.append("DUPLICATE_TIMESTAMPS")

    frame = frame.sort_values("timestamp", kind="mergesort").reset_index(drop=True)
    delta = timeframe_delta(request.timeframe)
    since = utc_timestamp(request.since, "since")
    until = utc_timestamp(request.until, "until")
    as_of = utc_timestamp(request.as_of, "as_of")

    in_requested_window = (frame["timestamp"] >= since) & (frame["timestamp"] < until)
    frame = frame.loc[in_requested_window].copy()

    # Timestamps are bar-open clocks. A bar is admissible only after its close
    # is known at as_of.
    closed = frame["timestamp"] + delta <= as_of
    frame = frame.loc[closed].copy().reset_index(drop=True)

    numeric = frame[list(OHLCV_COLUMNS[1:])]
    null_count = int(frame[list(OHLCV_COLUMNS)].isna().sum().sum())
    invalid_candle = (
        (frame["open"] <= 0)
        | (frame["high"] <= 0)
        | (frame["low"] <= 0)
        | (frame["close"] <= 0)
        | (frame["volume"] < 0)
        | (frame["high"] < frame[["open", "close", "low"]].max(axis=1))
        | (frame["low"] > frame[["open", "close", "high"]].min(axis=1))
    ) if not frame.empty else pd.Series(dtype=bool)
    invalid_candle_count = int(invalid_candle.sum()) if len(invalid_candle) else 0
    if null_count:
        reasons.append("NULL_VALUES")
    if invalid_candle_count:
        reasons.append("INVALID_CANDLES")
    if not frame.empty and not np.isfinite(numeric.to_numpy(dtype=float)).all():
        reasons.append("NON_FINITE_VALUES")

    spacing_ns = int(delta.value)
    off_grid_count = 0
    if not frame.empty:
        # pandas 3 may store datetime64 columns at microsecond resolution;
        # Timestamp.value is always nanoseconds and keeps this grid test stable.
        off_grid_count = sum(
            1 for stamp in frame["timestamp"]
            if pd.Timestamp(stamp).value % spacing_ns != 0
        )
        if off_grid_count:
            reasons.append("OFF_GRID_TIMESTAMPS")

    gap_count = 0
    missing_bar_count = 0
    if len(frame) >= 2:
        gaps = frame["timestamp"].diff().iloc[1:]
        gap_mask = gaps > delta
        gap_count = int(gap_mask.sum())
        missing_bar_count = int(sum(max(0, int(gap / delta) - 1) for gap in gaps[gap_mask]))
        if gap_count:
            reasons.append("CLOCK_GAPS")

    if duplicates:
        # Duplicate evidence is fatal even if a sorted frame could be deduped.
        pass
    if len(frame) > request.limit:
        reasons.append("LIMIT_EXCEEDED")
    if frame.empty:
        reasons.append("NO_CLOSED_BARS")

    report = DataQualityReport(
        rows_received=rows_received,
        rows_closed=len(frame),
        duplicates=duplicates,
        gap_count=gap_count,
        missing_bar_count=missing_bar_count,
        off_grid_count=off_grid_count,
        null_count=null_count,
        invalid_candle_count=invalid_candle_count,
        coverage_start=None if frame.empty else frame["timestamp"].iloc[0].isoformat(),
        coverage_end=None if frame.empty else frame["timestamp"].iloc[-1].isoformat(),
        expected_spacing_seconds=TIMEFRAME_SECONDS[request.timeframe],
        quality_pass=not reasons,
        reasons=tuple(reasons),
    )
    return frame, report


def resample_causal_complete(
    frame: pd.DataFrame,
    *,
    source_timeframe: str,
    target_timeframe: str,
) -> pd.DataFrame:
    """Aggregate bar-open OHLCV only when every source bar exists in a bucket."""
    source_delta = timeframe_delta(source_timeframe)
    target_delta = timeframe_delta(target_timeframe)
    if target_delta <= source_delta:
        raise ValueError("target_timeframe must be higher than source_timeframe")
    ratio = target_delta / source_delta
    if int(ratio) != ratio:
        raise ValueError("target timeframe must be an integer multiple of source timeframe")
    expected_count = int(ratio)

    if list(frame.columns)[:6] != list(OHLCV_COLUMNS):
        missing = set(OHLCV_COLUMNS) - set(frame.columns)
        if missing:
            raise ValueError(f"missing OHLCV columns: {sorted(missing)}")
        frame = frame.loc[:, list(OHLCV_COLUMNS)].copy()
    if frame.empty:
        return frame.copy()

    x = frame.copy()
    x["timestamp"] = pd.to_datetime(x["timestamp"], utc=True, errors="raise")
    x = x.sort_values("timestamp", kind="mergesort")
    if x["timestamp"].duplicated().any():
        raise ValueError("cannot resample duplicate timestamps")
    x = x.set_index("timestamp")
    rule = f"{int(target_delta.total_seconds())}s"
    grouped = x.resample(rule, label="left", closed="left", origin="epoch")
    out = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    counts = grouped["close"].count()
    out = out.loc[counts == expected_count].dropna().reset_index()
    return out.loc[:, list(OHLCV_COLUMNS)]
