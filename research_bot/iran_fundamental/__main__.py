"""Uploaded-evidence dossier; no exchange or mail side effects."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path

from .contracts import ResearchRequest, timestamp
from .engine import FundamentalEngine
from .storage import ResearchStore


def csv_cell(value: object) -> str:
    """Keep untrusted text inert when imported into spreadsheet software."""
    text = str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description="Iran equity research; uploaded claims remain unverified")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--idempotency-key", required=True)
    parser.add_argument("--as-of", help="timezone-aware ISO datetime; defaults to request time")
    parser.add_argument("--scope", choices=("SEPARATE", "CONSOLIDATED"), default="SEPARATE")
    args = parser.parse_args()
    if not args.bundle.is_file() or args.bundle.stat().st_size > 4_000_000: parser.error("bounded evidence JSON file required")
    payload = json.loads(args.bundle.read_text(encoding="utf-8"))
    as_of = timestamp(args.as_of) if args.as_of else datetime.now(timezone.utc)
    request = ResearchRequest(args.symbol, as_of, args.idempotency_key, None, args.scope, args.as_of is None)
    with ResearchStore(str(args.database)) as store:
        engine = FundamentalEngine(store); report = engine.analyze(request, payload)
        target = args.output / report["job_id"]; target.mkdir(parents=True, exist_ok=True)
        for name, content in {"report.json": json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), "report.html": engine.render_html(report["job_id"])}.items():
            path = target / name
            if path.exists() and path.read_text(encoding="utf-8") != content: raise ValueError("IMMUTABLE_REPORT_ARTIFACT_CONFLICT")
            path.write_text(content, encoding="utf-8")
        facts_path = target / "facts.csv"
        if not facts_path.exists():
            with facts_path.open("w", encoding="utf-8-sig", newline="") as stream:
                fields = list(report["facts"][0]) if report["facts"] else ["Variable", "Value", "Status"]
                writer = csv.DictWriter(stream, fields)
                writer.writeheader()
                writer.writerows({key: csv_cell(value) for key, value in row.items()} for row in report["facts"])
        print(json.dumps({"job_id": report["job_id"], "status": report["status"], "decision": report["decision"], "artifacts": str(target), "email_sent": False}, ensure_ascii=False))


if __name__ == "__main__": main()
