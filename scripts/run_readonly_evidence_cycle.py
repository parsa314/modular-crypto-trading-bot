"""One-shot research-only evidence cycle on a validated historical closed-bar CSV.

No broker/order transport, no paper/live mode, no invented ML risk forecast.
This is an evaluation of a single terminal phase-one baseline hypothesis, not
an economic backtest or a prospective-data certificate.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess

import pandas as pd

from research_bot.four_systems.binance_history import read_research_csv
from research_bot.strategies.baselines import BASELINE_REGISTRY
from scripts.run_jamalian_research import run as run_phase_one


def _git_commit() -> str | None:
    try:
        value = subprocess.check_output(
            ["git", "rev-parse", "--verify", "HEAD"],
            stderr=subprocess.DEVNULL, text=True, timeout=5,
        ).strip()
        return value if len(value) == 40 and all(c in "0123456789abcdef" for c in value) else None
    except (OSError, subprocess.SubprocessError):
        return None


def _boundary(value: str | None) -> pd.Timestamp | None:
    if value is None:
        return None
    result = pd.Timestamp(value)
    if pd.isna(result) or result.tzinfo is None:
        raise ValueError("timezone-aware UTC boundaries required")
    return result.tz_convert("UTC")


def run_cycle(
    csv: Path,
    output: Path,
    *,
    strategy: str,
    start: str | None = None,
    end: str | None = None,
    events: Path | None = None,
) -> dict:
    """Run an actual one-shot canonical hypothesis/NO_TRADE decision and write evidence."""
    if strategy not in BASELINE_REGISTRY:
        raise ValueError("UNREGISTERED_PHASE_ONE_STRATEGY")
    csv, output = Path(csv), Path(output)
    if not csv.is_file():
        raise ValueError(f"HISTORICAL_INPUT_NOT_FOUND: {csv}")
    if output.exists():
        raise FileExistsError(f"existing evidence must not be overwritten: {output}")
    if events is not None and len({csv.resolve(), output.resolve(), Path(events).resolve()}) != 3:
        raise ValueError("data, report and event ledger must use distinct files")
    beginning, ending = _boundary(start), _boundary(end)
    if beginning is not None and ending is not None and beginning >= ending:
        raise ValueError("start must precede exclusive end")
    sidecar = csv.with_suffix(".manifest.json")
    if not sidecar.is_file():
        raise ValueError("REQUIRED_DATA_MANIFEST_MISSING")
    dataset_hash = sha256(csv.read_bytes()).hexdigest()
    manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    if manifest.get("csv_sha256") != dataset_hash:
        raise ValueError("DATA_MANIFEST_HASH_MISMATCH")
    frame = read_research_csv(csv)
    selected = frame
    if beginning is not None:
        selected = selected.loc[selected.timestamp >= beginning]
    if ending is not None:
        selected = selected.loc[selected.timestamp < ending]
    if selected.empty:
        raise ValueError("EMPTY_REQUESTED_HISTORICAL_WINDOW")
    if len(selected) > 1 and not selected.timestamp.diff().iloc[1:].eq(pd.Timedelta(hours=1)).all():
        raise ValueError("WINDOW_CONTAINS_CANDLE_GAPS")
    decision_time = selected.available_at.iloc[-1]
    candles = [{
        "symbol": str(row.symbol),
        "close": str(row.close),
        "closed_at": row.timestamp.isoformat(),
        "available_at": row.available_at.isoformat(),
        "source": str(manifest.get("source") or "HISTORICAL_BACKFILL_UNVERIFIED_SOURCE"),
    } for row in selected.itertuples(index=False)]
    document = {
        "strategy": strategy,
        "decision_time": decision_time.isoformat(),
        "experiment_id": "jamalian_phase_one_evidence_cycle_v1",
        "candles": candles,
    }
    phase = run_phase_one(document, dataset_sha256=dataset_hash, events_path=events)
    decisions = phase["records"]
    histogram: Counter[str] = Counter()
    no_trade = 0
    matched = 0
    risk_evaluated = 0
    for row in decisions:
        signal = row["signal"]
        decision = signal["decision"]
        if decision == "NO_TRADE":
            no_trade += 1
        matched += int(bool(signal["metadata"].get("candidate", False)))
        risk_evaluated += int(row["risk"] is not None)
        histogram.update(signal["reasons"] or ["NO_DECISION_REASON_RECORDED"])
    commit = _git_commit()
    identity = {
        "dataset_sha256": dataset_hash,
        "strategy": strategy,
        "first_ts": selected.timestamp.iloc[0].isoformat(),
        "last_ts": selected.timestamp.iloc[-1].isoformat(),
        "commit": commit,
    }
    report = {
        "schema": "jamalian-readonly-evidence-cycle-v1",
        "run_id": sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "commit_sha": commit,
        "commit_verification": "LOCAL_GIT_HEAD_ONLY" if commit else "UNAVAILABLE",
        "scientific_status": "HYPOTHESIS_NOT_ECONOMICALLY_VALIDATED",
        "data": {
            "source": manifest.get("source"),
            "sha256": dataset_hash,
            "manifest_sha256": sha256(sidecar.read_bytes()).hexdigest(),
            "availability_basis": frame.attrs.get("availability_basis"),
            "data_class": "HISTORICAL_BACKFILL_NOT_PROSPECTIVE",
            "symbol": selected.symbol.iloc[0],
            "rows": len(selected),
            "gaps": int(selected.timestamp.diff().iloc[1:].ne(pd.Timedelta(hours=1)).sum()),
            "first_ts": selected.timestamp.iloc[0].isoformat(),
            "last_ts": selected.timestamp.iloc[-1].isoformat(),
        },
        "signals": {
            "strategy": strategy,
            "count": len(decisions),
            "rule_matched": matched,
            "causal_input_schema_validated": True,
            "full_prefix_invariance_proven_by_this_run": False,
        },
        "decisions": {
            "total": len(decisions),
            "no_trade": no_trade,
            "reason_histogram": dict(sorted(histogram.items())),
        },
        "model": {"status": "NOT_TRAINED", "calibration": "NOT_RUN"},
        "risk": {"status": "NOT_EVALUATED" if not risk_evaluated else "PARTIALLY_EVALUATED",
                 "evaluated_count": risk_evaluated, "cvar_95": None},
        "execution": {"mode": "RESEARCH_ONLY", "orders_sent": 0, "fills": 0,
                      "reconciliation": "NOT_APPLICABLE_NO_ORDER_TRANSPORT"},
        "economic_validation": {"status": "NOT_RUN"},
        "operational_phase_one": phase,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--strategy", required=True, choices=sorted(BASELINE_REGISTRY))
    parser.add_argument("--start", help="inclusive, timezone-aware timestamp")
    parser.add_argument("--end", help="exclusive, timezone-aware timestamp")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--events", type=Path)
    args = parser.parse_args()
    report = run_cycle(
        args.csv, args.output, strategy=args.strategy,
        start=args.start, end=args.end, events=args.events,
    )
    print(json.dumps({
        "run_id": report["run_id"],
        "decisions": report["decisions"]["total"],
        "orders_sent": 0,
        "scientific_status": report["scientific_status"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
