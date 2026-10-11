"""Offline replay of hashed, public CoinEx H4 responses; no network/order API.

Collection metadata and raw responses must already exist in --data. Re-running
on the same bytes/config/code is deterministic. Output is create-only.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

import pandas as pd

from research_bot.course.economic_acceptance import assess_event_panel, finalize_family
from research_bot.course.labels import replay_course_events
from research_bot.course.strategies import CourseStrategy, validated_candles
from research_bot.trial_registry_v17 import TrialRecord, append_trial


def run(data_dir: Path, output_dir: Path, source_git_sha: str) -> None:
    if len(source_git_sha) != 40 or any(c not in "0123456789abcdef" for c in source_git_sha):
        raise ValueError("EXPLICIT_SOURCE_COMMIT_SHA_REQUIRED")
    protocol_path = Path(__file__).resolve().parents[1] / "research_bot/course/configs/course_economic_pilot_v1.json"
    protocol_bytes = protocol_path.read_bytes()
    protocol = json.loads(protocol_bytes)
    manifest = json.loads((data_dir / "DATA_MANIFEST.json").read_text())
    if manifest["protocol_sha256"] != sha256(protocol_bytes).hexdigest() or manifest["venue"] != "CoinEx":
        raise ValueError("PROTOCOL_OR_VENUE_MISMATCH")
    if [item["asset"] for item in manifest["files"]] != protocol["assets"]:
        raise ValueError("INCOMPLETE_ASSET_CORPUS")
    output_dir.mkdir(parents=True, exist_ok=False)
    panels = []
    all_records = []
    for item in manifest["files"]:
        raw = (data_dir / item["path"]).read_bytes()
        if sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError("RAW_DATA_HASH_MISMATCH")
        payload = json.loads(raw)
        if payload["code"] != 0:
            raise ValueError("PROVIDER_ERROR")
        frame = pd.DataFrame(payload["data"])
        frame["open_time"] = pd.to_datetime(frame.created_at, unit="ms", utc=True)
        frame = frame.loc[(frame.open_time >= pd.Timestamp(protocol["raw_start"])) &
                          (frame.open_time < pd.Timestamp(protocol["raw_end"]))].sort_values("open_time")
        expected = pd.date_range(protocol["raw_start"], protocol["raw_end"], freq="4h", inclusive="left")
        if not pd.Index(frame.open_time).equals(expected) or not frame.market.eq(item["asset"]+"USDT").all():
            raise ValueError("INCOMPLETE_OR_WRONG_MARKET_CORPUS")
        frame["close_time"] = frame.open_time + pd.Timedelta(seconds=protocol["timeframe_seconds"])
        frame["available_at"] = frame.close_time + pd.Timedelta(seconds=protocol["availability_assumption_seconds_after_close"])
        for column in ["open", "high", "low", "close", "volume"]:
            frame[column] = pd.to_numeric(frame[column], errors="raise")
        validated_candles(frame, frame.available_at.max().to_pydatetime(), max_age_seconds=14400)
        for sid in protocol["strategy_ids"]:
            strategy = CourseStrategy(sid, enabled=True)
            events = replay_course_events(frame, strategy, symbol=item["asset"]+"USDT", venue="CoinEx")
            records = [asdict(event) for event in events if
                       pd.Timestamp(protocol["entry_start"]) <= event.entry_time < pd.Timestamp(protocol["entry_end"])]
            panel = assess_event_panel(pd.DataFrame(records), protocol)
            panel.update(asset=item["asset"], strategy_id=sid, config_hash=strategy.config_hash,
                         raw_dataset_sha256=item["sha256"], boundary_censoring="UNMATURED_POSITIONS_NOT_LIQUIDATED")
            panels.append(panel)
            all_records.extend(records)
            print(item["asset"], sid, len(records), flush=True)
    finalize_family(panels, protocol)
    summary = {"protocol_version": protocol["version"], "protocol_sha256": sha256(protocol_bytes).hexdigest(),
               "protocol_git_sha": manifest["protocol_git_sha"], "data_manifest": manifest,
               "source_git_sha": source_git_sha,
               "economic_confirmation": "ECONOMIC_CONFIRMATION_DEFERRED", "execution_authorization": "DENIED_RESEARCH_ONLY",
               "experimental_classification": "RETROSPECTIVE_ASSUMPTION_CONDITIONAL_PILOT",
               "fixed_family_size": len(panels), "completed_event_count": len(all_records), "panels": panels}
    (output_dir / "economic_report.json").write_text(json.dumps(summary, indent=2, allow_nan=False))
    (output_dir / "events.json").write_text(json.dumps(all_records, indent=2, default=str, allow_nan=False))
    (output_dir / "protocol.json").write_bytes(protocol_bytes)
    for panel in panels:
        trial = TrialRecord(
            trial_id=sha256((panel["asset"]+panel["strategy_id"]+panel["config_hash"]+
                             summary["protocol_sha256"]+panel["raw_dataset_sha256"]).encode()).hexdigest(),
            git_commit=source_git_sha, dataset_fingerprint=panel["raw_dataset_sha256"],
            feature_family=panel["strategy_id"], model_family="FIXED_COURSE_RULE_NO_ML",
            hyperparameters={"strategy_config_hash": panel["config_hash"], "protocol_sha256": summary["protocol_sha256"]},
            random_seed=protocol["bootstrap_seed"], forecast_horizon="SOURCE_EXIT_OR_TIMEOUT_VARIABLE_EVENT_HORIZON",
            decision_rule="AVAILABLE_PREFIX_NEXT_OPEN_OHLC_V2_RESEARCH_ONLY",
            cost_model={"roundtrip_bps": protocol["roundtrip_cost_bps"], "status": protocol["cost_status"]},
            train_window="NO_MODEL_FIT_WARMUP_FROM_"+protocol["raw_start"],
            validation_window=protocol["entry_start"]+"/"+protocol["entry_end"]+"_RETROSPECTIVE_PILOT",
            holdout_window=None, return_path_artifact="events.json", status="HYPOTHESIS")
        panel["trial_fingerprint"] = append_trial(output_dir / "trial_registry.jsonl", trial)
    (output_dir / "economic_report.json").write_text(json.dumps(summary, indent=2, allow_nan=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-git-sha", required=True)
    args = parser.parse_args()
    run(args.data, args.output, args.source_git_sha)
