from __future__ import annotations

import json
import os

import pandas as pd
import psycopg

from research_bot.finalize_v51 import PROSPECTIVE_END_V51, audit_prospective_evidence_v51


QUERY = """
SELECT venue, symbol, timeframe, bar_open_at, bar_close_at, first_seen_at,
       open, high, low, close, volume, payload_sha256,
       prospective_eligible, source_commit, collector_version
FROM v51_research.prospective_ohlcv
ORDER BY venue, symbol, bar_open_at
"""


def main() -> None:
    dsn = os.environ["V51_DATABASE_URL"]
    now = pd.Timestamp.now(tz="UTC")
    with psycopg.connect(dsn) as conn:
        frame = pd.read_sql_query(QUERY, conn)

    audit = audit_prospective_evidence_v51(frame) if not frame.empty else None
    if now < PROSPECTIVE_END_V51:
        state = "V51_WINDOW_OPEN"
    elif audit is None or audit.eligible_rows == 0:
        state = "V51_INCOMPLETE_EVIDENCE"
    else:
        state = "V51_EVIDENCE_READY_FOR_FROZEN_TERMINAL_ANALYSIS"

    payload = {
        "state": state,
        "checked_at": now.isoformat(),
        "prospective_end": PROSPECTIVE_END_V51.isoformat(),
        "paper_execution": False,
        "live_execution": False,
        "kraken_touched": False,
        "economic_decision_emitted": False,
        "audit": None if audit is None else {
            "rows_total": audit.rows_total,
            "eligible_rows": audit.eligible_rows,
            "ineligible_rows": audit.ineligible_rows,
            "invalid_rows": audit.invalid_rows,
            "eligible_series": audit.eligible_series,
            "eligible_blocks": audit.eligible_blocks,
            "source_commits": list(audit.source_commits),
        },
    }
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
