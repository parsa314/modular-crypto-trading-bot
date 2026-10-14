"""Fail-closed, read-only MT5 DEMO order/position reconciliation.

No broker I/O or order submission occurs here. This independent pure policy
only evaluates an externally observed broker snapshot and the complete
locally journaled intent/outcome event history. An unknown broker fill is
NEVER assumed to have failed. A stale/missing snapshot is ALWAYS a veto.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class BrokerDemoSnapshot:
    account_key: str
    captured_at: datetime
    positions: tuple[Mapping[str, Any], ...]
    open_orders: tuple[Mapping[str, Any], ...]
    observation_complete: bool
    account_is_demo: bool


@dataclass(frozen=True)
class ReconciliationVerdict:
    approved: bool
    reasons: tuple[str, ...]
    snapshot_sha256: str
    unmatched_intents: tuple[str, ...]
    positions_count: int
    open_orders_count: int


def _sha(data: object) -> str:
    return sha256(json.dumps(data, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def reconcile_demo_before_new_order(
    *,
    broker: BrokerDemoSnapshot,
    journal_rows: Iterable[Mapping[str, Any]],
    now: datetime,
    current_unsubmitted_intent: str | None = None,
    max_age_seconds: int = 20,
) -> ReconciliationVerdict:
    """Compare COMPLETE broker state and journal without inventing broker fills.

    A journal INTENT that lacks a terminal BROKER outcome is ambiguous after
    reconnect. EXECUTION_ERROR is also ambiguous (network may disconnect AFTER
    acceptance). Pending broker orders and managed positions are explicit
    admission vetoes. The caller must query ALL relevant bot owned orders and
    positions; empty/incomplete queries must not be silently interpreted flat.
    """
    reasons: list[str] = []
    if now.tzinfo is None or now.utcoffset() is None:
        reasons.append("NAIVE_RECONCILIATION_CLOCK")
    if broker.captured_at.tzinfo is None or broker.captured_at.utcoffset() is None:
        reasons.append("UNVERIFIED_BROKER_SNAPSHOT_CLOCK")
    if not broker.account_key or ":" not in broker.account_key:
        reasons.append("INVALID_ACCOUNT_IDENTITY")
    if not broker.account_is_demo:
        reasons.append("REAL_OR_UNVERIFIED_ACCOUNT_REFUSED")
    if not broker.observation_complete:
        reasons.append("INCOMPLETE_BROKER_ORDER_POSITION_SNAPSHOT")
    if now.tzinfo and broker.captured_at.tzinfo:
        age=(now.astimezone(timezone.utc)-broker.captured_at.astimezone(timezone.utc)).total_seconds()
        if age < 0 or age > max_age_seconds:
            reasons.append("STALE_OR_FUTURE_BROKER_SNAPSHOT")
    seen_orders: set[int] = set()
    for kind, values in (("POSITION", broker.positions), ("PENDING_ORDER", broker.open_orders)):
        for row in values:
            try:
                ticket=int(row.get("ticket", 0))
                if ticket <= 0 or ticket in seen_orders:
                    reasons.append("INVALID_OR_DUPLICATE_BROKER_TICKET")
                seen_orders.add(ticket)
            except (TypeError, ValueError, AttributeError):
                reasons.append("INVALID_BROKER_TICKET")
    if broker.positions:
        reasons.append("OPEN_BOT_POSITION_REQUIRES_MANAGEMENT_NOT_NEW_ENTRY")
    if broker.open_orders:
        reasons.append("PENDING_BOT_ORDER_REQUIRES_RECONCILIATION")

    unresolved: list[str] = []
    intents: dict[str, str] = {}
    terminal: dict[str, str] = {}
    for row in journal_rows:
        record_type = str(row.get("record_type", ""))
        sid = str(row.get("signal_id", ""))
        if record_type in {"INTENT", "CLOSE_INTENT"}:
            if not sid:
                reasons.append("MALFORMED_JOURNAL_INTENT")
            else:
                intents[sid] = record_type
        if record_type in {"EXECUTION_RESULT", "CLOSE_RESULT", "EXECUTION_ERROR"}:
            if sid:
                terminal[sid] = record_type
    for sid, kind in intents.items():
        if sid == current_unsubmitted_intent:
            continue
        if sid not in terminal:
            unresolved.append(sid)
        elif terminal[sid] == "EXECUTION_ERROR":
            unresolved.append(sid)  # network error does not prove no fill
    if unresolved:
        reasons.append("UNRESOLVED_LOCAL_ORDER_OR_CLOSE_INTENTS")

    # A prior local fill is not proof of current flatness. A locally
    # journalled CLOSE_RESULT is likewise not a substitute for broker deal
    # history (including SL/TP/partial fills); require independent replay.
    if any(
        sid != current_unsubmitted_intent and terminal.get(sid) == "EXECUTION_RESULT"
        for sid in intents
    ):
        reasons.append("BROKER_DEAL_HISTORY_NOT_RECONCILED")

    payload = {
        "account_key": broker.account_key,
        "captured_at": broker.captured_at.isoformat(),
        "positions": list(broker.positions),
        "open_orders": list(broker.open_orders),
        "complete": broker.observation_complete,
        "is_demo": broker.account_is_demo,
        "unresolved": sorted(set(unresolved)),
        "vetoes": sorted(set(reasons)),
    }
    return ReconciliationVerdict(
        approved=not reasons,
        reasons=tuple(sorted(set(reasons))),
        snapshot_sha256=_sha(payload),
        unmatched_intents=tuple(sorted(set(unresolved))),
        positions_count=len(broker.positions),
        open_orders_count=len(broker.open_orders),
    )
