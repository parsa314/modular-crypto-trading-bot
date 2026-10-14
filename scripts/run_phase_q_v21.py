from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from research_bot.phase_q_v21 import evaluate_phase_q_v21


def _load_snapshots(input_dir: Path) -> tuple[list[dict], list[dict]]:
    snapshots: list[dict] = []
    errors: list[dict] = []
    for path in sorted(input_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                snapshots.append(payload)
            else:
                errors.append({"path": str(path), "reason": "NON_OBJECT_JSON"})
        except Exception as exc:
            errors.append({"path": str(path), "reason": f"{type(exc).__name__}:{exc}"})
    return snapshots, errors


def _render_markdown(result: dict, load_errors: list[dict]) -> str:
    lines = [
        "# v0.21 Phase-Q Common-Anchor Quality Report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        f"Decision: **{result['decision']}**",
        "",
        f"Elapsed hours: {result['elapsed_hours']:.2f}",
        f"Expected opportunities: {result['expected_measurement_opportunities']}",
        f"Countable unique slots: {result['countable_unique_slots']}",
        f"Excluded snapshots: {result['excluded_snapshot_count']}",
        f"Ledger SHA-256: `{result['evidence_ledger_sha256']}`",
        "",
        "## Per-symbol quality",
        "",
        "| Symbol | Observed | Authorized | Coverage | p95 book age | p95 trade stale | Provider failures | Reasons |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for symbol, stats in result["symbols"].items():
        p95_book = "n/a" if stats["p95_book_age_seconds"] is None else f"{stats['p95_book_age_seconds']:.2f}s"
        p95_stale = "n/a" if stats["p95_trade_staleness_seconds"] is None else f"{stats['p95_trade_staleness_seconds']:.2f}s"
        lines.append(
            f"| {symbol} | {stats['observed_slots']} | {stats['authorized_slots']} | {stats['authorized_coverage_ratio']:.2%} | "
            f"{p95_book} | {p95_stale} | {stats['provider_failure_events']} | {', '.join(stats['quality_reasons']) or '—'} |"
        )
    lines.extend([
        "",
        "## Diagnostics",
        "",
        f"- Duplicate slot ratio: {result['diagnostics']['duplicate_slot_ratio']:.2%}",
        f"- Provider-failure snapshot ratio: {result['diagnostics']['provider_failure_snapshot_ratio']:.2%}",
        f"- Maximum consecutive missing slots: {result['diagnostics']['max_consecutive_missing_slots_between_observations']}",
        f"- Warnings: {', '.join(result['diagnostics']['warnings']) or 'none'}",
        f"- JSON load errors: {len(load_errors)}",
        "",
        "## Authorization boundary",
        "",
        f"- feature_freeze_authorized = {str(result['feature_freeze_authorized']).lower()}",
        "- predictive_modeling_authorized = false",
        "- paper_strategy_replacement_authorized = false",
        "- testnet_promotion_authorized = false",
        "- live_execution_authorized = false",
        "",
        "> Passing Phase-Q only permits freezing the next prospective 4h feature specification. It never authorizes a strategy or real-money execution.",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", default="artifacts/v21/source_snapshots")
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--output", default="artifacts/v21/phase_q_quality_gate.json")
    parser.add_argument("--markdown", default="artifacts/v21/PHASE_Q_COMMON_ANCHOR_REPORT.md")
    args = parser.parse_args()

    snapshots, load_errors = _load_snapshots(Path(args.input_dir))
    result = evaluate_phase_q_v21(snapshots, as_of=args.as_of)
    result["input_snapshot_file_count"] = len(snapshots)
    result["input_load_errors"] = load_errors

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    markdown = Path(args.markdown)
    markdown.parent.mkdir(parents=True, exist_ok=True)
    markdown.write_text(_render_markdown(result, load_errors), encoding="utf-8")
    print(json.dumps({
        "decision": result["decision"],
        "elapsed_hours": result["elapsed_hours"],
        "countable_unique_slots": result["countable_unique_slots"],
        "expected": result["expected_measurement_opportunities"],
        "excluded": result["excluded_snapshot_count"],
        "ledger_sha256": result["evidence_ledger_sha256"],
    }, indent=2))


if __name__ == "__main__":
    main()
