"""Download auditable, closed CoinEx spot candles without filling missing bars."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from research_bot.coinex_public import PERIOD_MS, fetch_coinex_klines

SYMBOLS = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT")
SOURCE = "https://docs.coinex.com/api/v2/spot/market/http/list-market-kline"


def collect_one(symbol: str, start: datetime, cutoff: datetime, output: Path,
                fetcher=fetch_coinex_klines) -> dict:
    if symbol not in SYMBOLS:
        raise ValueError("symbol outside frozen universe")
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(cutoff)
    if start_ts.tzinfo is None or end_ts.tzinfo is None or end_ts <= start_ts:
        raise ValueError("start and cutoff must be ordered timezone-aware timestamps")
    step = pd.Timedelta(milliseconds=PERIOD_MS["4hour"])
    bars_requested = math.ceil((end_ts - start_ts) / step) + 100
    frame = fetcher(symbol, period="4hour", market_type="spot",
                    start_ms=int(start_ts.timestamp() * 1000),
                    end_ms=int(end_ts.timestamp() * 1000), bars=bars_requested)
    if frame.empty:
        raise ValueError("exchange returned no history")
    frame = frame.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="raise")
    frame = frame[(frame.timestamp >= start_ts) & (frame.timestamp + step <= end_ts)]
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    if frame.empty or frame.timestamp.duplicated().any():
        raise ValueError("closed history empty or duplicate timestamps")
    cols = ("open", "high", "low", "close", "volume")
    for col in cols:
        frame[col] = pd.to_numeric(frame[col], errors="raise")
    if (not np.isfinite(frame[list(cols)].to_numpy(dtype=float)).all()
            or (frame[["open", "high", "low", "close"]] <= 0).any().any()
            or (frame.volume < 0).any()
            or (frame.high < frame[["open", "close", "low"]].max(axis=1)).any()
            or (frame.low > frame[["open", "close"]].min(axis=1)).any()):
        raise ValueError("invalid OHLCV returned from exchange")
    deltas = frame.timestamp.diff().dropna()
    gap_intervals = int((deltas != step).sum())
    missing_bars = int(sum(int(d / step) - 1 for d in deltas if d > step))
    if (deltas < step).any():
        raise ValueError("overlapping or off-grid timestamps")
    selected = frame[["timestamp", *cols]].copy()
    csv_file = output / (symbol.replace("/", "_") + "_4h.csv.gz")
    selected.to_csv(csv_file, index=False, compression={"method": "gzip", "mtime": 0}, float_format="%.17g")
    return {
        "symbol": symbol, "status": "GAPLESS" if gap_intervals == 0 else "GAPS_PRESENT",
        "source": SOURCE, "endpoint": "/spot/kline", "market_type": "spot",
        "period": "4hour", "requested_start": start_ts.isoformat(),
        "requested_cutoff": end_ts.isoformat(), "first_bar": frame.timestamp.iloc[0].isoformat(),
        "last_bar": frame.timestamp.iloc[-1].isoformat(), "rows": len(frame),
        "gap_intervals": gap_intervals, "missing_bars": missing_bars,
        "file": csv_file.name, "sha256": hashlib.sha256(csv_file.read_bytes()).hexdigest(),
        "note": "Real public exchange candles; no gap filling; last unclosed candle excluded",
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--start", default="2022-01-01T00:00:00+00:00")
    args = p.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    manifest = {"collected_at": now.isoformat(), "source": SOURCE,
                "research_only": True, "symbols": {}, "blocked": {}}
    for symbol in SYMBOLS:
        try:
            manifest["symbols"][symbol] = collect_one(
                symbol, pd.Timestamp(args.start).to_pydatetime(), now, args.output_dir)
        except Exception as exc:
            manifest["blocked"][symbol] = f"{type(exc).__name__}: {exc}"
        print(symbol, manifest["symbols"].get(symbol, manifest["blocked"].get(symbol)), flush=True)
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0 if len(manifest["symbols"]) == len(SYMBOLS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
