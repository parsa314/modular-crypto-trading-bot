"""Byte-preserving historical data intake. Never generates trading outcomes."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ASSETS = {"BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT"}
REQUIRED = ["timestamp", "open", "high", "low", "close", "volume"]
STEP = pd.Timedelta(hours=4)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def inspect_csv(raw: bytes) -> tuple[pd.DataFrame, dict]:
    frame = pd.read_csv(io.BytesIO(raw))
    if list(frame.columns) != REQUIRED or frame.empty:
        raise ValueError("expected nonempty timestamp/open/high/low/close/volume CSV")
    if not frame["timestamp"].astype(str).str.contains(r"(?:Z|[+-]\d{2}:\d{2})$", regex=True).all():
        raise ValueError("every timestamp must have an explicit UTC offset")
    times = pd.to_datetime(frame.timestamp, utc=True, errors="raise")
    if times.isna().any():
        raise ValueError("missing timestamp")
    numbers = frame[REQUIRED[1:]].to_numpy(dtype=float)
    if not np.isfinite(numbers).all():
        raise ValueError("nonfinite OHLCV")
    o, h, l, c, v = numbers.T
    if (numbers[:, :4] <= 0).any() or (v < 0).any():
        raise ValueError("nonpositive price or negative volume")
    if ((l > np.minimum(o, c)) | (h < np.maximum(o, c)) | (h < l)).any():
        raise ValueError("invalid OHLC geometry")
    duplicates = int(times.duplicated().sum())
    if duplicates or not times.is_monotonic_increasing:
        raise ValueError("duplicate or unordered timestamps; no silent repair")
    if any(t != t.floor("4h") for t in times):
        raise ValueError("off-grid H4 timestamp")
    diffs = times.diff().dropna()
    missing = int(sum(int(d / STEP) - 1 for d in diffs))
    if missing:
        raise ValueError(f"missing H4 bars: {missing}; no gap filling")
    frame["timestamp"] = times
    schema = {"columns": REQUIRED, "timestamp": "UTC_BAR_OPEN", "timeframe": "4h", "numeric": "float64"}
    return frame, {
        "row_count": len(frame), "start_timestamp": times.iloc[0].isoformat(),
        "end_timestamp": times.iloc[-1].isoformat(), "last_bar_close": (times.iloc[-1] + STEP).isoformat(),
        "missing_bar_count": missing, "duplicate_timestamp_count": duplicates,
        "schema_hash": digest(canonical_json(schema)), "csv_sha256": digest(raw), "timezone": "UTC",
    }


def audit_archive(path: Path, *, venue: str, expected_sha256: str, source_run: int,
                  source_artifact: int) -> tuple[dict, dict[str, bytes]]:
    """Validate the exact received ZIP and per-file hashes from its source manifest.

    Previously inspected archives are ALWAYS development-only; no API parameter
    can silently upgrade this intake to an untouched holdout.
    """
    raw_zip = path.read_bytes()
    archive_hash = digest(raw_zip)
    if archive_hash != expected_sha256:
        raise ValueError("archive SHA-256 mismatch")
    rows, members = [], {}
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
        names = zf.namelist()
        if len(names) != len(set(names)):
            raise ValueError("duplicate ZIP members")
        manifest_raw = zf.read("manifest.json")
        manifest = json.loads(manifest_raw)
        declared = manifest["symbols"]
        for name, metadata in sorted(declared.items()):
            symbol = name if "/" in name else name[:-4] + "/USDT"
            if symbol not in ASSETS:
                raise ValueError(f"unexpected symbol: {symbol}")
            filename = metadata["file"]
            if Path(filename).name != filename or not filename.endswith(".csv.gz"):
                raise ValueError("unsafe or unsupported archive filename")
            member = zf.read(filename)
            if digest(member) != metadata["sha256"]:
                raise ValueError(f"source file hash mismatch: {filename}")
            frame, quality = inspect_csv(gzip.decompress(member))
            if quality["row_count"] != metadata["rows"]:
                raise ValueError("row count differs from source manifest")
            for key, audit_key in [("first_bar", "start_timestamp"), ("last_bar", "end_timestamp")]:
                if pd.Timestamp(metadata[key]) != pd.Timestamp(quality[audit_key]):
                    raise ValueError("timestamp bounds differ from source manifest")
            collected = manifest.get("collected_at")
            if collected is not None and pd.Timestamp(quality["last_bar_close"]) > pd.Timestamp(collected):
                raise ValueError("unclosed source candle")
            limitations = ["PREVIOUSLY_INSPECTED_DEVELOPMENT_ONLY", "NOT_PROSPECTIVE_FIRST_SEEN_DATA",
                           "SOURCE_CLAIMS_NOT_INDEPENDENT_EXCHANGE_REDOWNLOAD"]
            if collected is None:
                limitations.append("EXACT_DOWNLOAD_TIMESTAMP_NOT_IN_SOURCE_MANIFEST")
            if metadata.get("leading_missing_bars", 0):
                limitations.append("PARTIAL_REQUESTED_HISTORY")
            rows.append({
                "dataset_id": f"{venue}_{symbol.replace('/', '_')}_4h_{quality['csv_sha256'][:16]}",
                "venue": venue, "symbol": symbol, "timeframe": "4h", **quality,
                "file": filename, "file_sha256": digest(member), "archive_sha256": archive_hash,
                "download_source": metadata.get("source", manifest.get("source")),
                "download_timestamp": collected, "source_run": source_run, "source_artifact": source_artifact,
                "source_manifest_sha256": digest(manifest_raw), "partition_eligibility": "DEVELOPMENT_ONLY",
                "known_limitations": limitations,
            })
            members[filename] = member
        if {row["symbol"] for row in rows} != ASSETS:
            raise ValueError("all five assets required")
    return {"venue": venue, "archive_sha256": archive_hash, "datasets": rows}, members


def write_new(path: Path, content: bytes) -> None:
    """Create new evidence, or verify identical bytes; never replace evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as fh:
            fh.write(content)
    except FileExistsError:
        if path.read_bytes() != content:
            raise ValueError(f"immutable output differs: {path}")


def freeze_archives(specs: list[dict], output: Path) -> dict:
    audited = []
    seen = set()
    for spec in specs:
        report, members = audit_archive(**spec)
        if report["archive_sha256"] in seen:
            raise ValueError("duplicate archive: not independent evidence")
        seen.add(report["archive_sha256"])
        audited.append((spec, report, members))
    # No output is emitted until every archive has passed validation.
    datasets = []
    for spec, report, members in audited:
        root = output / "raw" / report["archive_sha256"]
        write_new(root / "source.zip", spec["path"].read_bytes())
        for name, raw in members.items():
            write_new(root / name, raw)
        datasets.extend(report["datasets"])
    manifest = {
        "status": "HISTORICAL_BYTES_VERIFIED_DEVELOPMENT_ONLY", "datasets": datasets,
        "event_dataset_authorized": False, "model_training_authorized": False,
        "paper_execution": False, "live_execution": False,
        "blockers": ["FINAL_HOLDOUT_NOT_SEALED", "CRITICAL_INTEGRATION_GATES_REQUIRE_REVIEW"],
    }
    write_new(output / "V58_RECOVERED_DATA_MANIFEST.json", canonical_json(manifest))
    return manifest
