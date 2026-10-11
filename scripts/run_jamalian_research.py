"""Offline closed-bar research entry point; no credentials or network access.

Input is a JSON object with strategy (rsi_ma/macd_rsi_ema), decision_time and
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

from research_bot.contracts import EvidenceStamp, ResearchStatus
from research_bot.jamalian_data import Candle
from research_bot.jamalian_integration import JamalianResearchLedger
from research_bot.strategies.jamalian_hypotheses import RsiMaHypothesis, MacdRsiEmaHypothesis


def run(document: dict, *, dataset_sha256: str) -> dict:
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
    generators = {"rsi_ma": RsiMaHypothesis, "macd_rsi_ema": MacdRsiEmaHypothesis}
    if strategy not in generators:
        raise ValueError("closed-bar CLI supports rsi_ma and macd_rsi_ema; use typed book API for grid/triangle")
    candles = [Candle(row["symbol"], row["close"], EvidenceStamp(row["source"],
        datetime.fromisoformat(row["closed_at"]), datetime.fromisoformat(row["available_at"]),
        lineage=dataset_sha256, status=ResearchStatus.UNVERIFIED)) for row in document["candles"]]
    signals = generators[strategy](**parameters).generate(candles, at)
    ledger = JamalianResearchLedger()
    for signal in signals:
        ledger.record(signal)
    report = ledger.export()
    report["dataset_sha256"] = dataset_sha256
    report["validation_status"] = "NOT_RUN_REQUIRES_PREREGISTERED_DATA_AND_COSTS"
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    document = json.loads(raw, parse_float=Decimal)
    report = run(document, dataset_sha256=sha256(raw).hexdigest())
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2, ensure_ascii=False, allow_nan=False)
        output.write("\n")
    print(f"{len(report['records'])} research records; PAPER_OFF / LIVE_OFF")


if __name__ == "__main__":
    main()
