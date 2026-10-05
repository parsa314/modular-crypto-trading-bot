"""Checksum-pinned, public Binance Spot history for offline research only.

Timestamps denote bar OPEN time in UTC; the requested end is EXCLUSIVE.
Incomplete or invalid data is retained for inspection but cannot be loaded by
``load_validated_history``. No gaps, prices, or outliers are imputed or clipped.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile

import numpy as np
import pandas as pd

from research_bot.binance_spot_archive import COLUMNS, _parse_timestamp

BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"
INTERVALS = {"1h": pd.Timedelta(hours=1), "4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(days=1)}
OHLCV = ["timestamp", "open", "high", "low", "close", "volume"]
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
MAX_CSV_BYTES = 128 * 1024 * 1024


class DataIntegrityError(ValueError):
    """The source or dataset does not satisfy the research data contract."""


def _utc(value: str | pd.Timestamp) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError("dates must be finite")
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _bounds(start: str, end: str, timeframe: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    if timeframe not in INTERVALS:
        raise ValueError("timeframe must be 1h, 4h, or 1d")
    first, last = _utc(start), _utc(end)
    width = INTERVALS[timeframe]
    if first >= last or first.value % width.value or last.value % width.value:
        raise ValueError("start/end must be interval-aligned, with start < end")
    if (last - first) > pd.Timedelta(days=365 * 30):
        raise ValueError("request exceeds the 30-year resource bound")
    return first, last


def _symbol(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z0-9]{5,24}", value):
        raise ValueError("symbol must be uppercase exchange notation, e.g. BTCUSDT")
    return value


def _download(url: str, *, attempts: int = 3, timeout: float = 30) -> bytes:
    """Use platform TLS verification and configured proxies without overrides."""
    for attempt in range(attempts):
        try:
            request = Request(url, headers={"User-Agent": "modular-crypto-research/spot-history"})
            with urlopen(request, timeout=timeout) as response:
                payload = response.read(MAX_DOWNLOAD_BYTES + 1)
            if len(payload) > MAX_DOWNLOAD_BYTES:
                raise DataIntegrityError("download exceeds resource bound")
            return payload
        except HTTPError as exc:
            if exc.code not in {408, 425, 429, 500, 502, 503, 504} or attempt == attempts - 1:
                raise
        except (URLError, TimeoutError, ConnectionError, OSError):
            if attempt == attempts - 1:
                raise
        time.sleep(2**attempt)
    raise RuntimeError("unreachable download state")


def verify_checksum(payload: bytes, checksum: bytes, filename: str) -> str:
    """Require an exact SHA256 manifest entry for this archive filename."""
    try:
        line = checksum.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise DataIntegrityError("checksum is not ASCII") from exc
    match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?([^\s/\\]+)", line)
    if match is None or match.group(2) != filename:
        raise DataIntegrityError("checksum must contain one matching archive entry")
    digest = sha256(payload).hexdigest()
    if digest != match.group(1).lower():
        raise DataIntegrityError(f"checksum mismatch: {filename}")
    return digest


def _write_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _write_json(path: Path, payload: dict) -> None:
    _write_bytes(path, (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode())


def _archive(cache: Path, symbol: str, timeframe: str, month: str) -> tuple[bytes, bytes, dict]:
    filename = f"{symbol}-{timeframe}-{month}.zip"
    url = f"{BASE_URL}/{symbol}/{timeframe}/{filename}"
    archive_path, checksum_path = cache / filename, cache / (filename + ".CHECKSUM")
    if archive_path.exists() != checksum_path.exists():
        raise DataIntegrityError(f"incomplete cache pair: {filename}")
    if archive_path.exists():
        payload, checksum = archive_path.read_bytes(), checksum_path.read_bytes()
        source = "verified_cache"
    else:
        checksum = _download(url + ".CHECKSUM")
        payload = _download(url)
        source = "download"
    digest = verify_checksum(payload, checksum, filename)
    if source == "download":
        _write_bytes(archive_path, payload)
        _write_bytes(checksum_path, checksum)
    return payload, checksum, {
        "filename": filename, "url": url, "checksum_url": url + ".CHECKSUM",
        "sha256": digest, "checksum_sha256": sha256(checksum).hexdigest(),
        "bytes": len(payload), "retrieval": source,
    }


def parse_archive(payload: bytes, filename: str, timeframe: str) -> pd.DataFrame:
    """Parse exactly the official 12-field schema, including close timestamps."""
    if timeframe not in INTERVALS:
        raise ValueError("unsupported timeframe")
    try:
        with ZipFile(BytesIO(payload)) as archive:
            entries = archive.infolist()
            if len(entries) != 1 or entries[0].filename != filename.removesuffix(".zip") + ".csv":
                raise DataIntegrityError("archive must contain exactly its named CSV")
            if entries[0].file_size > MAX_CSV_BYTES:
                raise DataIntegrityError("CSV exceeds resource bound")
            data = archive.read(entries[0])
        raw = pd.read_csv(BytesIO(data), header=None, dtype=str)
    except (BadZipFile, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise DataIntegrityError("invalid or empty kline archive") from exc
    if raw.shape[1] != 12:
        raise DataIntegrityError(f"expected 12 kline fields; found {raw.shape[1]}")
    raw.columns = COLUMNS
    # Timestamp strings must be integral before reusing the existing ms/us parser.
    for column in ("open_time", "close_time"):
        if not raw[column].str.fullmatch(r"[0-9]+", na=False).all():
            raise DataIntegrityError(f"non-integral {column}")
    try:
        for column in COLUMNS:
            raw[column] = pd.to_numeric(raw[column], errors="raise")
        raw["timestamp"] = _parse_timestamp(raw["open_time"])
        close_time = _parse_timestamp(raw["close_time"])
    except (ValueError, OverflowError) as exc:
        raise DataIntegrityError("invalid numeric kline field or timestamp") from exc
    # Binance changed from ms to us in 2025; final micro/millisecond is inclusive.
    close_unit = np.where(raw["close_time"].abs() >= 10**14, 1_000, 1_000_000)
    expected_end = raw["timestamp"] + INTERVALS[timeframe]
    actual_end = close_time + pd.to_timedelta(close_unit, unit="ns")
    if (actual_end.gt(expected_end) | actual_end.le(raw["timestamp"])).any():
        raise DataIntegrityError("close timestamp does not match the interval")
    # Official maintenance records may end early (e.g. 2023-03-24 12h).
    # Keep the original row for diagnosis; incomplete bars BLOCK quality.
    raw["incomplete_bar"] = actual_end.ne(expected_end)
    month = re.search(r"(\d{4}-\d{2})\.zip$", filename)
    if month is None or not raw["timestamp"].dt.strftime("%Y-%m").eq(month.group(1)).all():
        raise DataIntegrityError("archive contains timestamps outside its month")
    return raw


def assemble_history(frames: list[pd.DataFrame], *, start: str, end: str, timeframe: str = "1h") -> tuple[pd.DataFrame, dict]:
    """Return normalized rows and an explicit quality verdict; never fill holes."""
    first, last = _bounds(start, end, timeframe)
    if not frames:
        raise DataIntegrityError("no archives supplied")
    all_rows = pd.concat(frames, ignore_index=True).sort_values("timestamp", kind="stable")
    selected = all_rows.loc[(all_rows["timestamp"] >= first) & (all_rows["timestamp"] < last)].copy()
    duplicates = selected[selected.duplicated("timestamp", keep=False)]
    for stamp, group in duplicates.groupby("timestamp"):
        if len(group.drop_duplicates()) != 1:
            raise DataIntegrityError(f"conflicting duplicate at {stamp.isoformat()}")
    duplicate_count = int(selected.duplicated("timestamp").sum())
    selected = selected.drop_duplicates("timestamp").reset_index(drop=True)
    expected = pd.date_range(first, last, freq=INTERVALS[timeframe], inclusive="left")
    actual = pd.DatetimeIndex(selected["timestamp"])
    missing = expected.difference(actual)
    unexpected = actual.difference(expected)
    numeric = selected[list(COLUMNS)].to_numpy(dtype=float)
    nonfinite = ~np.isfinite(numeric).all(axis=1)
    prices = selected[["open", "high", "low", "close"]]
    nonpositive = prices.le(0).any(axis=1)
    negative_volume = selected[["volume", "quote_volume", "taker_base", "taker_quote"]].lt(0).any(axis=1)
    ohlc_bad = ((selected["high"] < selected[["open", "close", "low"]].max(axis=1)) |
                (selected["low"] > selected[["open", "close", "high"]].min(axis=1)))
    invalid_trades = selected["trades"].lt(0) | selected["trades"].mod(1).ne(0)
    taker_bad = (selected["taker_base"] > selected["volume"]) | (selected["taker_quote"] > selected["quote_volume"])
    # Outliers are audit flags, never automatic deletion or synthetic replacement.
    outlier = selected["close"].pct_change(fill_method=None).abs().gt(0.20)
    trailing_volume = selected["volume"].shift(1).rolling(24, min_periods=24).median()
    outlier |= selected["volume"].gt(trailing_volume * 20) & trailing_volume.gt(0)
    failures = {
        "missing_bars": len(missing), "off_grid_bars": len(unexpected),
        "nonfinite_rows": int(nonfinite.sum()), "nonpositive_price_rows": int(nonpositive.sum()),
        "negative_volume_rows": int(negative_volume.sum()), "ohlc_violation_rows": int(ohlc_bad.sum()),
        "invalid_trade_count_rows": int(invalid_trades.sum()), "taker_volume_violation_rows": int(taker_bad.sum()),
        "incomplete_bar_rows": int(selected["incomplete_bar"].sum()),
    }
    quality = {
        "valid": not any(failures.values()), "status": "PASS" if not any(failures.values()) else "BLOCKED",
        "start_inclusive": first.isoformat(), "end_exclusive": last.isoformat(), "timeframe": timeframe,
        "timestamp_semantics": "bar_open_UTC", "expected_rows": len(expected), "rows": len(selected),
        "identical_duplicates_removed": duplicate_count, "rows_outside_request": len(all_rows) - len(selected) - duplicate_count,
        **failures, "missing_timestamps": [stamp.isoformat() for stamp in missing],
        "first_missing_timestamp": missing[0].isoformat() if len(missing) else None,
        "last_missing_timestamp": missing[-1].isoformat() if len(missing) else None,
        "off_grid_timestamps": [stamp.isoformat() for stamp in unexpected],
        "incomplete_bar_timestamps": [stamp.isoformat() for stamp in selected.loc[selected["incomplete_bar"], "timestamp"]],
        "outlier_flags": int(outlier.sum()),
        "outlier_timestamps": [stamp.isoformat() for stamp in selected.loc[outlier, "timestamp"]],
        "outlier_rule": "abs(close_return)>20% or volume>20x preceding 24-bar median; retained unchanged",
        "imputed_rows": 0, "clipped_values": 0,
        "cross_source_verified": False,
    }
    return selected[OHLCV].copy(), quality


def collect_history(*, start: str, end: str, output: str | Path, symbol: str = "BTCUSDT", timeframe: str = "1h", cache: str | Path | None = None) -> dict:
    """Fetch official monthly files and write Parquet, provenance, and quality."""
    first, last = _bounds(start, end, timeframe)
    _symbol(symbol)
    try:
        import pyarrow  # noqa: F401 -- check optional storage dependency before network
    except ImportError as exc:
        raise RuntimeError("Parquet requires the research-phase pyarrow extra") from exc
    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise FileExistsError("output directory must be empty; preserve prior evidence")
    raw_dir = destination / "raw"
    raw_dir.mkdir()
    cache_dir = Path(cache) if cache is not None else raw_dir
    cache_dir.mkdir(parents=True, exist_ok=True)
    months = pd.period_range(first.tz_localize(None).to_period("M"), (last - pd.Timedelta(nanoseconds=1)).tz_localize(None).to_period("M"), freq="M")
    manifest = {
        "schema_version": 1, "source": "Binance Vision official Spot monthly klines",
        "symbol": symbol, "market": "spot", "timeframe": timeframe,
        "start_inclusive": first.isoformat(), "end_exclusive": last.isoformat(),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "module_sha256": sha256(Path(__file__).read_bytes()).hexdigest(), "files": [],
        "status": "COLLECTING", "quality_file": "quality.json", "dataset_file": "ohlcv.parquet",
    }
    try:
        frames = []
        for month in months:
            payload, checksum, provenance = _archive(cache_dir, symbol, timeframe, month.strftime("%Y-%m"))
            if cache_dir.resolve() != raw_dir.resolve():
                _write_bytes(raw_dir / provenance["filename"], payload)
                _write_bytes(raw_dir / (provenance["filename"] + ".CHECKSUM"), checksum)
            provenance["path"] = "raw/" + provenance["filename"]
            manifest["files"].append(provenance)
            frames.append(parse_archive(payload, provenance["filename"], timeframe))
        frame, quality = assemble_history(frames, start=first.isoformat(), end=last.isoformat(), timeframe=timeframe)
        parquet = destination / "ohlcv.parquet"
        frame.to_parquet(parquet, engine="pyarrow", index=False)
        _write_json(destination / "quality.json", quality)
        manifest.update({"status": quality["status"], "rows": len(frame), "dataset_sha256": sha256(parquet.read_bytes()).hexdigest(), "quality_sha256": sha256((destination / "quality.json").read_bytes()).hexdigest()})
        _write_json(destination / "manifest.json", manifest)
        return manifest
    except Exception as exc:
        manifest.update({"status": "BLOCKED", "error_type": type(exc).__name__})
        _write_json(destination / "manifest.json", manifest)
        if not (destination / "quality.json").exists():
            _write_json(destination / "quality.json", {"valid": False, "status": "BLOCKED", "error_type": type(exc).__name__})
        raise


def load_validated_history(output: str | Path) -> tuple[pd.DataFrame, dict]:
    """Fail closed on failed quality, changed source files, or changed Parquet."""
    directory = Path(output)
    manifest = json.loads((directory / "manifest.json").read_text())
    quality_bytes = (directory / "quality.json").read_bytes()
    quality = json.loads(quality_bytes)
    if manifest.get("status") != "PASS" or quality.get("valid") is not True:
        raise DataIntegrityError("history quality gate failed; inspect quality.json")
    if sha256(quality_bytes).hexdigest() != manifest["quality_sha256"]:
        raise DataIntegrityError("quality report checksum mismatch")
    parquet = directory / "ohlcv.parquet"
    if sha256(parquet.read_bytes()).hexdigest() != manifest["dataset_sha256"]:
        raise DataIntegrityError("normalized dataset checksum mismatch")
    for entry in manifest["files"]:
        filename = entry["filename"]
        if Path(filename).name != filename:
            raise DataIntegrityError("unsafe manifest filename")
        raw = directory / "raw" / filename
        checksum = raw.with_name(filename + ".CHECKSUM").read_bytes()
        digest = verify_checksum(raw.read_bytes(), checksum, filename)
        if digest != entry["sha256"] or sha256(checksum).hexdigest() != entry["checksum_sha256"]:
            raise DataIntegrityError("raw source provenance mismatch")
    return pd.read_parquet(parquet, engine="pyarrow"), manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True, help="exclusive UTC end")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", choices=list(INTERVALS), default="1h")
    parser.add_argument("--output", required=True)
    parser.add_argument("--cache")
    args = parser.parse_args(argv)
    manifest = collect_history(**vars(args))
    print(json.dumps({"status": manifest["status"], "rows": manifest["rows"], "output": args.output}, allow_nan=False))
    return 0 if manifest["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
