"""Reproducible native development study on verified Binance 2024 archives.

This is a distinct preregistered study; it cannot rescue the blocked 2022-2024
Logistic protocol. One year of development data cannot authorize promotion.
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import importlib.metadata
import json
from pathlib import Path
import subprocess

import pandas as pd

from research_bot.binance_spot_archive import load_monthly_spot_archives
from .artifacts import ImmutableArtifactStore
from .hashing import stable_hash
from .native_dataset import build_native_events
from .native_strategy import MODEL_FEATURES, NativeStrategyConfig
from .tournament import TournamentConfig, WalkForwardConfig, run_tournament


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT")
START, END = "2024-01-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00"
STUDY = {"experiment_id": "V59_NATIVE_BINANCE_2024_DEVELOPMENT_V1", "venue": "binance", "market": "spot",
         "symbols": list(SYMBOLS), "start": START, "end_exclusive": END,
         "native": asdict(NativeStrategyConfig(timeframe="1h")),
         "tournament": asdict(TournamentConfig(walk_forward=WalkForwardConfig(max_folds=12),
                                               evidence_class="REAL_MARKET_EVENT_DATA")),
         "execution_authorized": False, "final_evaluation": False,
         "known_limitation": "single_year_development_not_promotion_evidence"}


def source_hashes():
    root = Path(__file__).parent
    paths = ("native_study.py", "native_strategy.py", "native_dataset.py", "native_features.py", "native_confluence.py", "tournament.py")
    return {p: sha256((root / p).read_bytes()).hexdigest() for p in paths}


def freeze(output: Path):
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("Study output must be empty before registration")
    store = ImmutableArtifactStore(output)
    record = {"config": STUDY, "config_hash": stable_hash(STUDY), "source_hashes": source_hashes(),
              "registered_at": datetime.now(timezone.utc).isoformat(),
              "base_git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "metrics_status": "NOT_EVALUATED", "execution_authorized": False}
    store.write_json("registration.json", record)
    return record


def load_verified_binance_bundle(root: Path):
    root = Path(root)
    manifest = json.loads((root / "manifest.json").read_bytes())
    if (manifest.get("status") != "PASS" or manifest.get("symbol") not in SYMBOLS
            or manifest.get("market") != "spot" or manifest.get("timeframe") != "1h"
            or manifest.get("start_inclusive") != START or manifest.get("end_exclusive") != END
            or manifest.get("source") != "Binance Vision official Spot monthly klines"):
        raise ValueError("Study source identity, quality or date-window mismatch")
    for file, field in (("quality.json", "quality_sha256"), ("ohlcv.parquet", "dataset_sha256")):
        if sha256((root / file).read_bytes()).hexdigest() != manifest.get(field):
            raise ValueError("Study normalized/quality file hash mismatch")
    quality = json.loads((root / "quality.json").read_bytes())
    if quality.get("valid") is not True or quality.get("imputed_rows") != 0 or quality.get("clipped_values") != 0:
        raise ValueError("Study cannot consume imputed or failed-quality data")
    expected_names = {f"{manifest['symbol']}-1h-2024-{m:02d}.zip" for m in range(1, 13)}
    files = manifest.get("files") or []
    if {f.get("filename") for f in files} != expected_names or len(files) != 12:
        raise ValueError("Complete unique official monthly source set required")
    for entry in files:
        name = entry["filename"]
        source = root / "raw" / name
        checksum = source.with_name(name + ".CHECKSUM").read_bytes()
        parts = checksum.decode().split()
        digest = sha256(source.read_bytes()).hexdigest()
        if (parts != [digest, name] or digest != entry.get("sha256")
                or sha256(checksum).hexdigest() != entry.get("checksum_sha256")):
            raise ValueError("Official raw archive checksum mismatch")
    frame = pd.read_parquet(root / "ohlcv.parquet")
    raw = load_monthly_spot_archives(root / "raw", manifest["symbol"], timeframe="1h")
    pd.testing.assert_frame_equal(frame.reset_index(drop=True), raw.reset_index(drop=True),
                                  check_dtype=False, rtol=1e-12, atol=1e-12)
    return frame, manifest


def run(inputs: list[Path], output: Path):
    output = Path(output)
    record = json.loads((output / "registration.json").read_bytes())
    if record.get("source_hashes") != source_hashes() or record.get("config_hash") != stable_hash(STUDY):
        raise ValueError("Registered code/config changed; register a distinct trial")
    store = ImmutableArtifactStore(output)
    if (output / "summary.json").exists():
        raise FileExistsError("Completed study cannot be overwritten")
    bundles = [load_verified_binance_bundle(p) for p in inputs]
    if len(bundles) != len(SYMBOLS) or {m["symbol"] for _, m in bundles} != set(SYMBOLS):
        raise ValueError("All five independently verified symbols are mandatory")
    dataset, audits, input_hashes = [], [], {}
    for frame, manifest in sorted(bundles, key=lambda x: x[1]["symbol"]):
        events, audit = build_native_events(frame, venue="binance", symbol=manifest["symbol"].replace("USDT", "/USDT"),
                                            data_version=manifest["dataset_sha256"], as_of=END,
                                            config=NativeStrategyConfig(timeframe="1h"))
        dataset.append(events)
        audits.append({"symbol": manifest["symbol"], **audit})
        input_hashes[manifest["symbol"]] = manifest["dataset_sha256"]
    events = pd.concat(dataset, ignore_index=True)
    store.write_bytes("events.csv", events.to_csv(index=False).encode())
    store.write_json("dataset_audits.json", audits)
    tournament = run_tournament(events, feature_columns=MODEL_FEATURES,
                                 config=TournamentConfig(walk_forward=WalkForwardConfig(max_folds=12),
                                                         evidence_class="REAL_MARKET_EVENT_DATA")) if len(events) else None
    if tournament is not None:
        store.write_json("tournament.json", tournament)
    summary = {"experiment_id": STUDY["experiment_id"], "status": "DEVELOPMENT_EVALUATED_NO_MODEL_PROMOTED",
               "rows": len(events), "input_hashes": input_hashes, "source_hashes": source_hashes(),
               "asset_count": len(bundles), "trial_count": len(tournament["trial_results"]) if tournament else 0,
               "execution_authorized": False, "promotion_review_authorized": False,
               "portfolio_sharpe": None, "forward_days": 0,
               "single_year_red_flag": True, "economic_metrics_are_event_diagnostics": True,
               "package_versions": {n: importlib.metadata.version(n) for n in ("numpy", "pandas", "scikit-learn", "pyarrow")}}
    store.write_json("summary.json", summary)
    store.write_json("manifest.json", {"files": {p.name: sha256(p.read_bytes()).hexdigest()
                                                  for p in sorted(output.iterdir()) if p.is_file()}})
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "run"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inputs", nargs="+", type=Path)
    args = parser.parse_args(argv)
    if args.command == "run" and not args.inputs:
        parser.error("run requires --inputs")
    result = freeze(args.output) if args.command == "freeze" else run(args.inputs, args.output)
    print(json.dumps({"status": result.get("status", "REGISTERED"), "rows": result.get("rows"),
                      "execution_authorized": False}, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
