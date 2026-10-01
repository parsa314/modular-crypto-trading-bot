"""Replay byte-verified real market archives as development-only evidence."""
from __future__ import annotations

from collections import Counter
import gzip
import json
from pathlib import Path

from .data_audit import audit_archive, canonical_json
from .development_sources import SOURCES
from .events import stable_hash
from .immutable_io import write_immutable_bundle
from .pipeline import run_verified_development_pipeline, pipeline_result_files


def replay_archive(*, path: Path, venue: str, expected_sha256: str, source_run: int,
                   source_artifact: int, output: Path) -> dict:
    if not any(row["venue"] == venue and row["archive_sha256"] == expected_sha256
               and row["source_run"] == source_run and row["source_artifact"] == source_artifact
               for row in SOURCES):
        raise PermissionError("archive provenance is not a frozen development source")
    report, members = audit_archive(
        path, venue=venue, expected_sha256=expected_sha256,
        source_run=source_run, source_artifact=source_artifact,
    )
    summaries = []
    files = {}
    for dataset in report["datasets"]:
        result = run_verified_development_pipeline(
            gzip.decompress(members[dataset["file"]]), venue=venue, symbol=dataset["symbol"],
            dataset_sha256=dataset["csv_sha256"],
            manifest_status="HISTORICAL_BYTES_VERIFIED_DEVELOPMENT_ONLY",
        )
        for name, content in pipeline_result_files(result).items():
            key = f"{dataset['dataset_id']}/{name}"
            if key in files:
                raise ValueError("duplicate dataset output identity")
            files[key] = content
        arm_counts = Counter(row["strategy_arm"] for row in result.records)
        outcome_counts = Counter(row["outcome"] or row["outcome_state"] for row in result.records)
        arm_statistics = {}
        for arm in sorted(arm_counts):
            rows = [row for row in result.records if row["strategy_arm"] == arm]
            resolved = [row for row in rows if row["net_return"] is not None]
            labels = Counter(row["outcome"] or row["outcome_state"] for row in rows)
            arm_statistics[arm] = {
                "event_count": len(rows), "outcome_counts": dict(sorted(labels.items())),
                "mean_gross_return": sum(row["gross_return"] for row in resolved) / len(resolved) if resolved else None,
                "mean_net_return_24bps": sum(row["net_return"] for row in resolved) / len(resolved) if resolved else None,
                "ambiguous_bar_count": sum(bool(row["ambiguous_bar"]) for row in rows),
            }
        summaries.append({
            "dataset_id": dataset["dataset_id"], "venue": venue, "symbol": dataset["symbol"],
            "row_count": dataset["row_count"], "start_timestamp": dataset["start_timestamp"],
            "end_timestamp": dataset["end_timestamp"], "csv_sha256": dataset["csv_sha256"],
            "event_count": len(result.records), "arm_counts": dict(sorted(arm_counts.items())),
            "outcome_counts": dict(sorted(outcome_counts.items())), "replay_hash": result.replay_hash,
            "arm_statistics": arm_statistics,
            "ledger_valid": result.ledger.verify(),
            "classification": "REAL_MARKET_DEVELOPMENT_EVIDENCE",
        })
    summary = {
        "classification": "REAL_MARKET_DEVELOPMENT_EVIDENCE",
        "archive_sha256": report["archive_sha256"], "venue": venue,
        "datasets": summaries, "model_training": False,
        "scientific_alpha_claim_authorized": False, "paper_execution": False, "live_execution": False,
    }
    summary["summary_hash"] = stable_hash(summary)
    files[f"{venue}_real_replay_summary.json"] = canonical_json(summary)
    write_immutable_bundle(output, files)
    return summary
