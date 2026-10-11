"""Offline closed-bar research entry point; no credentials or network access.

Input is a JSON object with strategy (ma_cross/rsi/rsi_ma), decision_time and
candles [{symbol, close, closed_at, available_at, source}]. Decimal prices must
be strings. Optional parameters describe the generator, including interval.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re

from research_bot.contracts import EvidenceStamp, ResearchStatus
from research_bot.jamalian_data import Candle
from research_bot.jamalian_phase_one import JamalianPhaseOneResearch
from research_bot.operational_event_ledger import OperationalResearchEventLedger
from research_bot.strategies.baselines import BASELINE_REGISTRY


def run(document: dict, *, dataset_sha256: str, events_path: Path | None = None) -> dict:
    if not isinstance(dataset_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", dataset_sha256):
        raise ValueError("exact source-byte SHA-256 required")
    at = datetime.fromisoformat(document["decision_time"])
    strategy = document["strategy"]
    parameters = dict(document.get("parameters", {}))
    if "bar_interval_seconds" in parameters:
        seconds = parameters.pop("bar_interval_seconds")
        if type(seconds) is not int or seconds <= 0:
            raise ValueError("bar_interval_seconds must be a positive integer")
        parameters["bar_interval"] = timedelta(seconds=seconds)
    if "oversold" in parameters:
        parameters["oversold"] = Decimal(parameters["oversold"])
    if "execution_delay_seconds" in parameters:
        seconds = parameters.pop("execution_delay_seconds")
        if type(seconds) is not int or seconds <= 0:
            raise ValueError("execution_delay_seconds must be a positive integer")
        parameters["execution_delay"] = timedelta(seconds=seconds)
    if strategy not in BASELINE_REGISTRY:
        raise ValueError("phase-one CLI supports only ma_cross, rsi and rsi_ma")
    # Exact input-byte digest belongs to the run report and provenance. Absence
    # of an experiment id defaults to engineering diagnostics, never a prereg.
    experiment_id = document.get("experiment_id", "jamalian_phase_one_engineering_v3")
    candles = [Candle(row["symbol"], row["close"], EvidenceStamp(row["source"],
        datetime.fromisoformat(row["closed_at"]), datetime.fromisoformat(row["available_at"]),
        lineage=dataset_sha256, status=ResearchStatus.UNVERIFIED)) for row in document["candles"]]
    generator, param_type = BASELINE_REGISTRY[strategy]
    candidates = generator(param_type(**parameters)).generate(candles, at)
    ledger = JamalianPhaseOneResearch(OperationalResearchEventLedger(experiment_id, events_path))
    for candidate in candidates:
        ledger.record(candidate)
    report = ledger.export()
    report["dataset_sha256"] = dataset_sha256
    report["validation_status"] = "NOT_RUN_REQUIRES_PREREGISTERED_DATA_AND_COSTS"
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path, help="Independent append-only JSONL event ledger")
    args = parser.parse_args()
    if len({p.resolve() for p in (args.input, args.output, args.events)}) != 3:
        parser.error("input, report and event ledger must use distinct paths")
    if args.output.exists():
        parser.error("report output already exists; choose a new path")
    raw = args.input.read_bytes()
    document = json.loads(raw, parse_float=Decimal)
    report = run(document, dataset_sha256=sha256(raw).hexdigest(), events_path=args.events)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2, ensure_ascii=False, allow_nan=False)
        output.write("\n")
    print(f"{len(report['records'])} research records; PAPER_OFF / LIVE_OFF")


if __name__ == "__main__":
    main()
