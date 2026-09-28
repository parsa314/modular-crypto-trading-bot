from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import run_synthetic_engineering_pipeline, synthetic_integration_fixture, write_pipeline_result


def main() -> int:
    parser = argparse.ArgumentParser(description="V58 research-only engineering runner")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("synthetic-demo", help="execute the connected pipeline on a non-market fixture")
    demo.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "synthetic-demo":
        result = run_synthetic_engineering_pipeline(synthetic_integration_fixture())
        write_pipeline_result(result, args.output)
        print(f"SYNTHETIC_ENGINEERING_ONLY events={len(result.records)} replay_hash={result.replay_hash}")
        return 0
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
