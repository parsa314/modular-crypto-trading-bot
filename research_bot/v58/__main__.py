from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import run_synthetic_engineering_pipeline, synthetic_integration_fixture, write_pipeline_result
from .development_sources import SOURCES
from .real_replay import replay_archive


def _archive_identity(venue: str) -> tuple[str, int, int]:
    identities = {(row["archive_sha256"], row["source_run"], row["source_artifact"])
                  for row in SOURCES if row["venue"] == venue}
    if len(identities) != 1:
        raise ValueError("venue must have exactly one frozen archive identity")
    return identities.pop()


def main() -> int:
    parser = argparse.ArgumentParser(description="V58 research-only engineering runner")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("synthetic-demo", help="execute the connected pipeline on a non-market fixture")
    demo.add_argument("--output", type=Path, required=True)
    replay = sub.add_parser("verified-replay", help="replay an exact frozen development archive")
    replay.add_argument("--archive", type=Path, required=True)
    replay.add_argument("--venue", choices=("binance", "coinex"), required=True)
    replay.add_argument("--output", type=Path, required=True)
    ai = sub.add_parser("synthetic-ai-demo", help="exercise causal events, walk-forward ML, utility and shared-capital risk")
    ai.add_argument("--output", type=Path, required=True)
    ai.add_argument("--bars", type=int, default=1600)
    ai.add_argument("--seed", type=int, default=58)
    market = sub.add_parser(
        "verified-market-audit",
        help="run the frozen no-retuning model audit on an exact development archive",
    )
    market.add_argument("--archive", type=Path, required=True)
    market.add_argument("--venue", choices=("binance", "coinex"), required=True)
    market.add_argument("--output", type=Path, required=True)
    scan = sub.add_parser(
        "signal-scan",
        help="run unified V58 confluence + MTF FVG/ICT/TSI research signal discovery",
    )
    scan.add_argument("--csv", type=Path, required=True)
    scan.add_argument("--symbol", required=True)
    scan.add_argument("--venue", default="research_csv")
    scan.add_argument("--htf", default="1h")
    scan.add_argument("--ltf", default="5min")
    scan.add_argument("--confirmation", choices=("tsi", "structure", "either", "both"), default="either")
    scan.add_argument("--stop-mode", choices=("midpoint", "zone_edge", "swing"), default="zone_edge")
    scan.add_argument("--rr", type=float, default=2.0)
    scan.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "synthetic-ai-demo":
            from .integrated import run_synthetic_ai_demo
            summary = run_synthetic_ai_demo(output=args.output, bars_per_asset=args.bars, seed=args.seed)
            print(f"SYNTHETIC_ENGINEERING_ONLY folds={summary['fold_count']} "
                  f"test_events={summary['test_event_count']} artifact_hash={summary['artifact_map_sha256']}")
            return 0
        if args.command == "synthetic-demo":
            result = run_synthetic_engineering_pipeline(synthetic_integration_fixture())
            write_pipeline_result(result, args.output)
            print(f"SYNTHETIC_ENGINEERING_ONLY events={len(result.records)} replay_hash={result.replay_hash}")
            return 0
        if args.command == "verified-replay":
            archive_hash, source_run, source_artifact = _archive_identity(args.venue)
            summary = replay_archive(path=args.archive, venue=args.venue, expected_sha256=archive_hash,
                                     source_run=source_run, source_artifact=source_artifact, output=args.output)
            print(f"REAL_MARKET_DEVELOPMENT_EVIDENCE datasets={len(summary['datasets'])} "
                  f"summary_hash={summary['summary_hash']}")
            return 0
        if args.command == "signal-scan":
            import pandas as pd
            from .signal_finder import CombinedSignalConfig, scan_combined_signals, write_signal_scan
            config = CombinedSignalConfig(
                htf=args.htf,
                ltf=args.ltf,
                confirmation=args.confirmation,
                stop_mode=args.stop_mode,
                reward_risk=args.rr,
            )
            result = scan_combined_signals(
                pd.read_csv(args.csv),
                config,
                symbol=args.symbol,
                venue=args.venue,
            )
            write_signal_scan(result, args.output)
            summary = result["summary"]
            print(
                "V58_RESEARCH_SIGNAL_DISCOVERY "
                f"combined={summary['combined_count']} "
                f"confluence10={summary['confluence10_count']} "
                f"fvg_ict_tsi={summary['fvg_ict_tsi_count']} "
                f"short_signal_only={summary['short_count']}"
            )
            return 0
        if args.command == "verified-market-audit":
            from .market_audit import audit_verified_archive
            summary = audit_verified_archive(path=args.archive, venue=args.venue, output=args.output)
            aggregate = summary["aggregate"]
            print(
                "REAL_MARKET_DEVELOPMENT_MODEL_AUDIT "
                f"test_events={aggregate['test_event_count']} "
                f"admitted={aggregate['admitted_event_count']} "
                f"gate={summary['development_gate']} "
                f"artifact_hash={summary['artifact_map_sha256']}"
            )
            return 0
    except (ValueError, PermissionError, OSError) as exc:
        parser.exit(2, f"error: {exc}\n")
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
