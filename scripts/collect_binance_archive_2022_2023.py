"""Collect a separate, checksummed Binance spot 4h history (never merge venues)."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from urllib.request import Request, urlopen
import zipfile

import pandas as pd

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT")
BASE = "https://data.binance.vision/data/spot/monthly/klines"
MONTHS = tuple(f"{year}-{month:02d}" for year in (2022, 2023)
               for month in range(1, 13) if year == 2022 or month <= 8)
HEADER = ["timestamp", "open", "high", "low", "close", "volume", "close_time",
          "quote_volume", "trade_count", "taker_base", "taker_quote", "ignore"]


def download(url: str) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": "thesis-public-research/1.0"}), timeout=45) as r:
        return r.read()


def collect_month(symbol: str, month: str, getter=download) -> tuple[pd.DataFrame, dict]:
    if symbol not in SYMBOLS or month not in MONTHS:
        raise ValueError("unregistered market or month")
    name = f"{symbol}-4h-{month}.zip"
    url = f"{BASE}/{symbol}/4h/{name}"
    archive = getter(url)
    checksum = getter(url + ".CHECKSUM").decode("ascii").split()[0].lower()
    digest = hashlib.sha256(archive).hexdigest()
    if checksum != digest:
        raise ValueError(f"archive checksum mismatch for {symbol} {month}")
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        csvs = [p for p in z.namelist() if p.endswith(".csv")]
        if len(csvs) != 1:
            raise ValueError("unexpected archive structure")
        frame = pd.read_csv(z.open(csvs[0]), header=None, names=HEADER)
    if frame.empty:
        raise ValueError("empty exchange archive")
    frame = frame[["timestamp", "open", "high", "low", "close", "volume"]]
    frame["timestamp"] = pd.to_datetime(frame.timestamp, unit="ms", utc=True, errors="raise")
    if (frame.timestamp.dt.strftime("%Y-%m") != month).any():
        raise ValueError("monthly archive has bars outside month")
    return frame, {"month": month, "archive_url": url, "archive_sha256": digest,
                   "checksum_url": url + ".CHECKSUM", "rows": len(frame)}


def main() -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--output-dir", type=Path, required=True)
    args = p.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"source": "binance-public-data monthly spot klines with provider CHECKSUM",
                "period": "4h", "venue": "Binance", "symbols": {}, "blocked": {}}
    for symbol in SYMBOLS:
        parts, months = [], []
        for month in MONTHS:
            try:
                frame, entry = collect_month(symbol, month)
                parts.append(frame)
                months.append(entry)
            except Exception as exc:
                manifest["blocked"][f"{symbol}:{month}"] = f"{type(exc).__name__}: {exc}"
            print(symbol, month, "downloaded" if len(months) and months[-1]["month"] == month else "blocked", flush=True)
        if not parts:
            continue
        x = pd.concat(parts, ignore_index=True).sort_values("timestamp").reset_index(drop=True)
        step = pd.Timedelta(hours=4)
        diffs = x.timestamp.diff().dropna()
        if x.timestamp.duplicated().any() or (diffs < step).any():
            raise ValueError(f"duplicate or overlapping bars: {symbol}")
        if (x[["open", "high", "low", "close"]] <= 0).any().any():
            raise ValueError(f"nonpositive price: {symbol}")
        file = args.output_dir / f"{symbol}_4h_2022_2023-08.csv.gz"
        x.to_csv(file, index=False, compression={"method": "gzip", "mtime": 0}, float_format="%.17g")
        manifest["symbols"][symbol] = {
            "status": "COMPLETE" if len(months) == len(MONTHS) and (diffs == step).all()
                      else "PARTIAL_OR_GAPPED", "months_received": len(months),
            "months_requested": len(MONTHS), "rows": len(x),
            "first_bar": x.timestamp.iloc[0].isoformat(),
            "last_bar": x.timestamp.iloc[-1].isoformat(),
            "gap_intervals": int((diffs != step).sum()), "file": file.name,
            "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
            "provider_months": months,
        }
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return 0 if not manifest["blocked"] and all(
        v["status"] == "COMPLETE" for v in manifest["symbols"].values()
    ) and len(manifest["symbols"]) == len(SYMBOLS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
