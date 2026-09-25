from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from research_bot.v58.data_freeze import FrozenFileSpec, frozen_60_20_20_split, verify_frozen_ohlcv


def _verify_group(root: Path, datasets: list[dict]) -> list[dict]:
    results = []
    for item in datasets:
        spec = FrozenFileSpec(
            symbol=item["symbol"],
            file_name=item["file"],
            compressed_sha256=item["compressed_sha256"],
            frame_sha256=item["frame_sha256"],
            schema_sha256=item["schema_sha256"],
            rows=int(item["rows"]),
            first_bar=item["first_bar"],
            last_bar=item["last_bar"],
            timeframe=item["timeframe"],
        )
        result = verify_frozen_ohlcv(root / spec.file_name, spec)
        results.append(result)
    return results


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, default=Path("V58_DATA_MANIFEST.json"))
    p.add_argument("--coinex-dir", type=Path, required=True)
    p.add_argument("--binance-dir", type=Path, required=True)
    args = p.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    coinex = _verify_group(args.coinex_dir, manifest["historical_primary"]["datasets"])
    binance = _verify_group(args.binance_dir, manifest["historical_external_transfer"]["datasets"])

    first_file = args.coinex_dir / manifest["historical_primary"]["datasets"][0]["file"]
    frame = pd.read_csv(first_file, compression="gzip")
    observed_split = frozen_60_20_20_split(frame)
    expected_split = manifest["historical_primary"]["split_contract"]
    for key in ("development", "validation", "internal_test_spent"):
        if observed_split[key] != expected_split[key]:
            raise ValueError(f"split mismatch for {key}: {observed_split[key]} != {expected_split[key]}")

    holdout = manifest["prospective_final_temporal_holdout"]
    if holdout["status"] != "SEALED_FUTURE_NOT_AVAILABLE" or holdout["bytes_available"] is not False:
        raise ValueError("prospective holdout must remain sealed and unavailable in Phase 2")
    if int(holdout["bars"]) != 540:
        raise ValueError("prospective holdout bar count changed")

    print(json.dumps({
        "status": "V58_PHASE2_DATA_FREEZE_VERIFIED",
        "coinex_files": len(coinex),
        "binance_files": len(binance),
        "historical_primary_split": observed_split,
        "prospective_holdout": holdout,
        "training_authorized": manifest["training_authorized"],
        "promotion_authorized": manifest["promotion_authorized"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
