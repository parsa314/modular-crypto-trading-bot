from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from urllib.request import Request, urlopen
import zipfile

import pandas as pd

from research_bot.historical_proxy_v21 import (
    V21HistoricalProxyConfig,
    build_historical_proxy_panel,
    normalize_binance_5m_frame,
    run_historical_proxy_stress_test,
)
from research_bot.integrity_v19 import finalize_payload_hash


BINANCE_VISION_BASE = "https://data.binance.vision/data/spot/monthly/klines"
COLS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trade_count", "taker_buy_base", "taker_buy_quote", "ignore",
]


def _download(url: str, timeout: int = 90) -> bytes:
    req = Request(url, headers={"User-Agent": "modular-crypto-research-bot/v0.21"})
    with urlopen(req, timeout=timeout) as response:
        return response.read()


def _months(start: str, end: str) -> list[str]:
    left = pd.Timestamp(start, tz="UTC") if pd.Timestamp(start).tzinfo is None else pd.Timestamp(start).tz_convert("UTC")
    right = pd.Timestamp(end, tz="UTC") if pd.Timestamp(end).tzinfo is None else pd.Timestamp(end).tz_convert("UTC")
    cursor = left.to_period("M")
    final = (right - pd.Timedelta(microseconds=1)).to_period("M")
    out = []
    while cursor <= final:
        out.append(str(cursor))
        cursor += 1
    return out


def _parse_checksum(text: str) -> str:
    token = text.strip().split()[0].lower()
    if len(token) != 64 or any(ch not in "0123456789abcdef" for ch in token):
        raise ValueError(f"invalid checksum payload: {text[:80]!r}")
    return token


def _load_month(symbol: str, month: str) -> tuple[pd.DataFrame, dict]:
    year, mon = month.split("-")
    filename = f"{symbol}-5m-{year}-{mon}.zip"
    url = f"{BINANCE_VISION_BASE}/{symbol}/5m/{filename}"
    zip_bytes = _download(url)
    expected_sha = _parse_checksum(_download(url + ".CHECKSUM").decode("utf-8"))
    actual_sha = hashlib.sha256(zip_bytes).hexdigest()
    if actual_sha != expected_sha:
        raise RuntimeError(f"checksum mismatch for {filename}: {actual_sha} != {expected_sha}")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        csv_names = [name for name in zf.namelist() if name.lower().endswith(".csv")]
        if len(csv_names) != 1:
            raise RuntimeError(f"expected one CSV in {filename}, found {csv_names}")
        with zf.open(csv_names[0]) as fh:
            raw = pd.read_csv(fh, header=None, low_memory=False)
    if raw.shape[1] < 12:
        raise RuntimeError(f"unexpected Binance schema in {filename}: {raw.shape}")
    raw = raw.iloc[:, :12]
    raw.columns = COLS
    # Some historical files may contain a header row; normalize function drops it
    # naturally because open_time cannot be parsed as numeric.
    frame = normalize_binance_5m_frame(raw, symbol)
    return frame, {
        "symbol": symbol,
        "month": month,
        "filename": filename,
        "url": url,
        "sha256": actual_sha,
        "rows": int(len(frame)),
        "start": frame["timestamp"].min().isoformat() if len(frame) else None,
        "end": frame["timestamp"].max().isoformat() if len(frame) else None,
    }


def _panel_digest(panel: pd.DataFrame) -> str:
    if panel.empty:
        return hashlib.sha256(b"").hexdigest()
    cols = sorted(panel.columns)
    x = panel[cols].copy()
    for col in x.columns:
        if pd.api.types.is_datetime64_any_dtype(x[col]):
            x[col] = pd.to_datetime(x[col], utc=True).astype(str)
    csv = x.sort_values(["symbol", "decision_at"]).to_csv(index=False, float_format="%.17g")
    return hashlib.sha256(csv.encode("utf-8")).hexdigest()


def _markdown(result: dict) -> str:
    lines = [
        "# v0.21-H Historical Flow-Proxy Stress Test",
        "",
        f"Generated: {result.get('generated_at')}",
        "",
        f"Decision: **{result.get('interpretation')}**",
        "",
        "> This is a historical single-venue proxy stress test. It is not prospective common-anchor evidence and cannot authorize PAPER replacement, testnet, or LIVE execution.",
        "",
        "## Evidence scope",
        "",
        f"- Panel rows: {result.get('panel_rows')}",
        f"- Panel range: {result.get('panel_start')} → {result.get('panel_end')}",
        f"- Symbols: {', '.join(result.get('panel_symbols') or [])}",
        f"- Dataset SHA-256: `{result.get('dataset_sha256')}`",
        f"- Archive files verified: {len(result.get('archive_files') or [])}",
        "",
        "## Planned comparisons",
        "",
        "| Model | Cost | Price-only return | +Flow return | Δ return (pp) | Mean Δ/4h | 95% block CI | p | FDR q | max-T p |",
        "|---|---:|---:|---:|---:|---:|---|---:|---:|---:|",
    ]
    strategies = result.get("strategies") or {}
    for comp_id, comp in (result.get("comparisons") or {}).items():
        model = comp["model"]
        cost = comp["cost_bps"]
        left = strategies[f"{model}|price_only|{cost:g}bps"]["metrics"]
        right = strategies[f"{model}|price_plus_flow_proxy|{cost:g}bps"]["metrics"]
        inf = comp["inference"]
        ci = inf.get("ci95") or [None, None]
        lines.append(
            f"| {model} | {cost:g} bps | {left.get('total_return', 0):.2%} | {right.get('total_return', 0):.2%} | "
            f"{comp.get('incremental_total_return_pp', 0):+.2f} | {inf.get('mean_difference', 0):+.6g} | "
            f"[{ci[0]:+.6g}, {ci[1]:+.6g}] | {inf.get('one_sided_p'):.4f} | {comp.get('fdr_q'):.4f} | {comp.get('max_t_fwer_p'):.4f} |"
        )
    lines.extend([
        "",
        "## Safety verdict",
        "",
        "- signal_authorized = false",
        "- paper_strategy_replacement_authorized = false",
        "- testnet_promotion_authorized = false",
        "- live_execution_authorized = false",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/v21/historical_proxy.json")
    parser.add_argument("--markdown", default="artifacts/v21/V21_HISTORICAL_PROXY_RESULTS.md")
    args = parser.parse_args()

    cfg = V21HistoricalProxyConfig()
    frames = []
    archive_files = []
    for symbol in cfg.symbols:
        for month in _months(cfg.source_start, cfg.source_end):
            frame, meta = _load_month(symbol, month)
            frames.append(frame)
            archive_files.append(meta)
    raw = pd.concat(frames, ignore_index=True).sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    panel = build_historical_proxy_panel(raw, cfg)
    if panel.empty:
        raise RuntimeError("historical proxy panel is empty")
    result = run_historical_proxy_stress_test(panel, cfg)
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    result["source"] = "Binance Public Data spot monthly 5m klines"
    result["archive_files"] = archive_files
    result["archive_file_count"] = len(archive_files)
    result["raw_5m_rows"] = int(len(raw))
    result["dataset_sha256"] = _panel_digest(panel)
    result["pre_registration"] = "docs/V21_COMMON_ANCHOR_AND_HISTORICAL_PROXY_PROTOCOL.md"
    finalize_payload_hash(result, "artifact_sha256")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    md = Path(args.markdown)
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(_markdown(result), encoding="utf-8")
    print(json.dumps({
        "output": str(output),
        "markdown": str(md),
        "panel_rows": result["panel_rows"],
        "dataset_sha256": result["dataset_sha256"],
        "artifact_sha256": result["artifact_sha256"],
        "interpretation": result["interpretation"],
    }, indent=2))


if __name__ == "__main__":
    main()
