from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from research_bot.candle_time_v53 import CandleTimeContractV53, closed_bar_snapshot_v53
from research_bot.coinex_public import fetch_coinex_klines
from research_bot.feature_audit_v54 import V54AuditConfig, audit_feature_families_v54
from research_bot.multitimeframe_v53 import build_multitimeframe_feature_frame_v53
from research_bot.v54_completion import assess_v54_completion
from research_bot.v54_integrity import dataset_manifest_v54
from research_bot.v54_promotion import V54PromotionPolicy, summarize_family_evidence_v54


DEFAULT_SYMBOLS = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT")


def _json_safe(obj):
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        value = float(obj)
        return value if np.isfinite(value) else None
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (pd.Timestamp, datetime)):
        return pd.Timestamp(obj).isoformat()
    return obj


def _symbol_slug(symbol: str) -> str:
    return symbol.replace("/", "_").replace("-", "_").upper()


def _source_commit() -> str | None:
    env = os.getenv("GITHUB_SHA") or os.getenv("SOURCE_COMMIT")
    if env and len(env) >= 7:
        return env.strip()
    try:
        out = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
        return out or None
    except Exception:
        return None


def build_symbol_frame(symbol: str, *, now: datetime, one_hour_bars: int, four_hour_bars: int) -> pd.DataFrame:
    one = fetch_coinex_klines(symbol, period="1hour", market_type="spot", bars=one_hour_bars)
    four = fetch_coinex_klines(symbol, period="4hour", market_type="spot", bars=four_hour_bars)
    one = closed_bar_snapshot_v53(one, decision_time=pd.Timestamp(now), contract=CandleTimeContractV53("1h"))
    four = closed_bar_snapshot_v53(four, decision_time=pd.Timestamp(now), contract=CandleTimeContractV53("4h"))
    if len(one) < 900 or len(four) < 150:
        raise ValueError(f"insufficient closed history for {symbol}: 1h={len(one)}, 4h={len(four)}")
    one["asset"] = symbol
    four["asset"] = symbol
    return build_multitimeframe_feature_frame_v53({"1h": one, "4h": four}, decision_timeframe="1h")


def write_frozen_input(frame: pd.DataFrame, symbol: str, directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    slug = _symbol_slug(symbol)
    csv_path = directory / f"{slug}.csv.gz"
    manifest_path = directory / f"{slug}.manifest.json"
    frame.to_csv(csv_path, index=False, compression="gzip", float_format="%.12g")
    # Re-read the exact persisted bytes before manifesting so replay validates the
    # serialized snapshot rather than an in-memory representation with different
    # dtype/float formatting.
    persisted = pd.read_csv(csv_path)
    for col in [c for c in persisted.columns if c == "timestamp" or c == "decision_at" or c.endswith("_at")]:
        persisted[col] = pd.to_datetime(persisted[col], utc=True, errors="coerce")
    manifest = dataset_manifest_v54(persisted, symbol=symbol)
    manifest["snapshot_file"] = csv_path.name
    manifest_path.write_text(json.dumps(_json_safe(manifest), indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return manifest


def read_frozen_input(symbol: str, directory: Path) -> tuple[pd.DataFrame, dict]:
    slug = _symbol_slug(symbol)
    csv_path = directory / f"{slug}.csv.gz"
    manifest_path = directory / f"{slug}.manifest.json"
    if not csv_path.exists() or not manifest_path.exists():
        raise FileNotFoundError(f"frozen input missing for {symbol}")
    frame = pd.read_csv(csv_path)
    for col in [c for c in frame.columns if c == "timestamp" or c == "decision_at" or c.endswith("_at")]:
        frame[col] = pd.to_datetime(frame[col], utc=True, errors="coerce")
    expected = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual = dataset_manifest_v54(frame, symbol=symbol)
    if actual["frame_sha256"] != expected.get("frame_sha256"):
        raise ValueError(f"frozen input hash mismatch for {symbol}")
    if actual["schema_sha256"] != expected.get("schema_sha256"):
        raise ValueError(f"frozen input schema mismatch for {symbol}")
    return frame, expected


def run_universe(
    symbols: tuple[str, ...],
    *,
    now: datetime,
    config: V54AuditConfig,
    one_hour_bars: int,
    four_hour_bars: int,
    freeze_dir: Path | None = None,
    replay_dir: Path | None = None,
) -> dict:
    per_symbol: dict[str, dict] = {}
    blocked: dict[str, str] = {}
    manifests: dict[str, dict] = {}
    for symbol in symbols:
        try:
            if replay_dir is not None:
                frame, manifest = read_frozen_input(symbol, replay_dir)
            else:
                frame = build_symbol_frame(symbol, now=now, one_hour_bars=one_hour_bars, four_hour_bars=four_hour_bars)
                manifest = dataset_manifest_v54(frame, symbol=symbol)
                if freeze_dir is not None:
                    manifest = write_frozen_input(frame, symbol, freeze_dir)
            manifests[symbol] = manifest
            per_symbol[symbol] = audit_feature_families_v54(frame, config)
        except Exception as exc:
            blocked[symbol] = f"{type(exc).__name__}: {exc}"

    policy = V54PromotionPolicy()
    evidence = summarize_family_evidence_v54(per_symbol, policy)
    report = {
        "experiment": "V54_REAL_COINEX_FEATURE_AUDIT",
        "status": "V54_EMPIRICAL_INCOMPLETE",
        "generated_at": pd.Timestamp(now).isoformat(),
        "source_commit": _source_commit(),
        "symbols_requested": list(symbols),
        "symbols_completed": sorted(per_symbol),
        "blocked": blocked,
        "dataset_manifests": manifests,
        "per_symbol": per_symbol,
        "cross_symbol_evidence": evidence,
        "replay_mode": bool(replay_dir is not None),
        "promotion_interpretation": "research-stage evidence only; never paper/live authorization",
        "paper_execution": False,
        "live_execution": False,
    }
    completion = assess_v54_completion(report)
    report["completion"] = completion
    report["status"] = completion["empirical_status"]
    return report


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="*", default=list(DEFAULT_SYMBOLS))
    p.add_argument("--one-hour-bars", type=int, default=3000)
    p.add_argument("--four-hour-bars", type=int, default=1200)
    p.add_argument("--min-train", type=int, default=1200)
    p.add_argument("--test-rows", type=int, default=240)
    p.add_argument("--step-rows", type=int, default=240)
    p.add_argument("--bootstrap-resamples", type=int, default=2000)
    p.add_argument("--bootstrap-block", type=int, default=24)
    p.add_argument("--freeze-dir", type=Path, default=None)
    p.add_argument("--replay-dir", type=Path, default=None)
    p.add_argument("--output", type=Path, default=Path("artifacts/v54/feature_audit.json"))
    args = p.parse_args()
    if args.freeze_dir is not None and args.replay_dir is not None:
        raise SystemExit("--freeze-dir and --replay-dir are mutually exclusive")

    cfg = V54AuditConfig(
        min_train_rows=args.min_train,
        test_rows=args.test_rows,
        step_rows=args.step_rows,
        purge_bars=1,
        horizon_bars=1,
        bootstrap_resamples=args.bootstrap_resamples,
        bootstrap_block=args.bootstrap_block,
    )
    report = run_universe(
        tuple(args.symbols),
        now=datetime.now(timezone.utc),
        config=cfg,
        one_hour_bars=args.one_hour_bars,
        four_hour_bars=args.four_hour_bars,
        freeze_dir=args.freeze_dir,
        replay_dir=args.replay_dir,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_json_safe(report), indent=2, sort_keys=True, allow_nan=False) + "\n"
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
