"""Research-only four-style signal evaluation on a user-supplied REAL CSV.

This command intentionally does not download prices, simulate transactions,
estimate performance, or submit orders. It prints dataset hash and current
causal signals so an independent backtest can subsequently use real evidence.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

import pandas as pd

from research_bot.strategies import evaluate_all, combine


def evaluate_file(path: Path, *, as_of: str, expected_sha256: str | None = None) -> dict:
    data = path.read_bytes()
    digest = sha256(data).hexdigest()
    if expected_sha256 and digest.lower() != expected_sha256.lower():
        raise ValueError("INPUT_SHA256_MISMATCH")
    if not data:
        raise ValueError("EMPTY_INPUT_FILE")
    t = pd.Timestamp(as_of)
    if t.tzinfo is None:
        raise ValueError("AS_OF_MUST_BE_TIMEZONE_AWARE")
    # No invented bars: fail on missing/short/invalid source rows.
    frame = pd.read_csv(path)
    votes = evaluate_all(frame, as_of=t.to_pydatetime())
    merged = combine(votes, as_of=t.to_pydatetime())
    return {
        "status": "RESEARCH_SIGNAL_ONLY_NO_PERFORMANCE_CLAIM",
        "source_sha256": digest,
        "as_of": t.isoformat(),
        "family_signals": {
            name: {
                **asdict(vote),
                "decision_at": vote.decision_at.isoformat(),
            }
            for name, vote in votes.items()
        },
        "confluence": {**asdict(merged), "decision_at": merged.decision_at.isoformat()},
        "market_performance_verified": False,
        "paper_execution_enabled": False,
        "live_execution_enabled": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--as-of", required=True, help="Timezone-aware ISO-8601 UTC cutoff")
    parser.add_argument("--expected-sha256", default=None)
    args = parser.parse_args()
    result = evaluate_file(args.csv, as_of=args.as_of, expected_sha256=args.expected_sha256)
    print(json.dumps(result, sort_keys=True, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
