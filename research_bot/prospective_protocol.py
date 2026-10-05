"""Immutable prospective-window registration and terminal continuity veto.

Registration is not activation, sample collection, maturity or promotion.
A scheduler must bind the record to reviewed immutable collector provenance.
This module cannot rehabilitate an old window by importing historical bars.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re


UNIVERSE = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "XRP/USDT:USDT", "DOGE/USDT:USDT")
TERMINAL = "BLOCKED_SCHEDULER_CONTINUITY"


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timezone-aware UTC clock required")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ProspectiveWindow:
    window_id: str
    start_at: datetime
    end_at: datetime
    collector_git_sha: str
    venue: str = "okx"
    market_type: str = "swap"
    input_kind: str = "ORDERBOOK_ONLY"
    timeframe: str = "4h"
    symbols: tuple[str, ...] = UNIVERSE
    max_scheduler_gap_hours: float = 5.5
    first_seen_deadline_minutes: int = 60

    def __post_init__(self):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{3,79}", self.window_id) or self.window_id in {"V25", "V0_25"}:
            raise ValueError("A distinct canonical window identity is required")
        start, end = utc(self.start_at), utc(self.end_at)
        if start >= end or start.minute or start.second or start.microsecond or start.hour % 4:
            raise ValueError("Start must be a four-hour UTC boundary before end")
        if not re.fullmatch(r"[0-9a-f]{40}", self.collector_git_sha) or len(set(self.collector_git_sha)) == 1:
            raise ValueError("A non-placeholder immutable collector SHA is required")
        if (self.symbols != UNIVERSE or self.venue != "okx" or self.market_type != "swap"
                or self.input_kind != "ORDERBOOK_ONLY" or self.timeframe != "4h"
                or isinstance(self.max_scheduler_gap_hours, bool) or self.max_scheduler_gap_hours != 5.5
                or type(self.first_seen_deadline_minutes) is not int or self.first_seen_deadline_minutes != 60):
            raise ValueError("Frozen universe, timing and market contract cannot be changed")
        object.__setattr__(self, "start_at", start)
        object.__setattr__(self, "end_at", end)

    def payload(self):
        record = asdict(self)
        record.update(start_at=self.start_at.isoformat(), end_at=self.end_at.isoformat())
        return record


def register_window(window: ProspectiveWindow, path: str | Path, *, registered_at: datetime) -> dict:
    registered_at = utc(registered_at)
    if registered_at >= window.start_at:
        raise ValueError("Prospective registration must precede the first decision boundary")
    payload = window.payload()
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    record = {"schema_version": 1, "protocol": payload, "protocol_sha256": sha256(canonical).hexdigest(),
              "registered_at": registered_at.isoformat(), "status": "PRE_REGISTERED_AWAITING_CANONICAL_SCHEDULER",
              "collection_active": False, "countable_samples": 0, "execution_authorized": False,
              "paper_authorized": False, "economic_metrics": None, "backfill_authorized": False}
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    return record


def continuity_status(window: ProspectiveWindow, *, current_at: datetime,
                      previous_at: datetime | None, previous_status: str | None = None) -> dict:
    current = utc(current_at)
    previous = utc(previous_at) if previous_at is not None else window.start_at
    if previous_at is not None and not window.start_at <= previous < current:
        raise ValueError("Accepted link must belong to this window and precede current clock")
    gap = max(0., (current - previous).total_seconds() / 3600)
    if previous_status == TERMINAL:
        status = TERMINAL  # terminal veto cannot reset after a later good run
    elif current < window.start_at:
        status = "WAITING_FOR_BOUNDARY"
    elif gap > window.max_scheduler_gap_hours:
        status = TERMINAL
    elif current >= window.end_at:
        status = "WINDOW_ENDED_REQUIRES_EVIDENCE_AUDIT"
    else:
        status = "CONTINUITY_OK"
    return {"window_id": window.window_id, "status": status, "gap_hours": gap,
            "collection_allowed": status == "CONTINUITY_OK", "execution_authorized": False,
            "backfill_authorized": False, "economic_decision": "NOT_EVALUATED"}
