"""DEMO-only emergency flatten orchestrator for *bot-owned* MT5 positions.

This is NOT a multi-host/HA safety controller. A caller must explicitly
enable DEMO exits and supply the broker's same-account verified terminal.
No real-money MT5 account, foreign-magic positions, or unexpected symbols are
touched. Every attempted close is journaled first. The system never silently
retries an uncertain close after a disconnect: it returns NEEDS_RECONCILIATION.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Iterable

from .mt5_direct_strategy import DirectStrategyJournal
from .mt5_global_demo_kill_switch import PersistentMT5DemoKillSwitch


@dataclass(frozen=True)
class EmergencyFlattenReport:
    account_key: str
    status: str
    attempted_tickets: tuple[int, ...]
    acknowledged_tickets: tuple[int, ...]
    unresolved_tickets: tuple[int, ...]
    details_sha256: str


def _hash(data: object) -> str:
    return sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def attempt_latched_demo_flatten(
    *, executor: Any, kill_switch: PersistentMT5DemoKillSwitch,
    journal: DirectStrategyJournal, venue_symbols: Iterable[str],
    allow_demo_closes: bool = False,
) -> EmergencyFlattenReport:
    """One pass only: query -> reserve close intent -> close -> verify.

    `allow_demo_closes` requires explicit operator enablement, AFTER verified
    broker DEMO account checks. The shared risk latch must already be active.
    Failures/partial fills leave the account in an unresolved safety state.
    """
    account = executor.account_summary()  # adapter validates DEMO account
    if not executor.connected or account.get("trade_mode") != 0:
        raise RuntimeError("REAL_OR_DISCONNECTED_ACCOUNT_REFUSED")
    key = f'{account.get("server","")}:{account.get("login","")}'
    state = kill_switch.snapshot(account_key=key)
    if state is None or not state.halted:
        raise RuntimeError("GLOBAL_DEMO_KILL_NOT_LATCHED")
    allowed=tuple(sorted(set(venue_symbols)))
    configured={str(x) for x in executor.config.symbol_map.values()}
    if not allowed or not set(allowed) <= configured:
        raise RuntimeError("SYMBOL_NOT_IN_BROKER_ALLOWLIST")
    before = {}
    for symbol in allowed:
        before[symbol] = list(executor.bot_positions(symbol))
    tickets = [int(p["ticket"]) for positions in before.values() for p in positions]
    if any(x <= 0 for x in tickets) or len(tickets) != len(set(tickets)):
        raise RuntimeError("INVALID_OR_DUPLICATED_BROKER_POSITION_TICKETS")
    if not allow_demo_closes or not executor.submission_enabled:
        return EmergencyFlattenReport(
            key, "DRY_RUN_ONLY", (), (), tuple(tickets),
            _hash({"account":key,"positions":before,"mode":"DRY_RUN"}),
        )
    attempted = []
    acknowledged = []
    failures = []
    for symbol, positions in before.items():
        for position in positions:
            ticket = int(position["ticket"])
            # Idempotency is preserved across restarts; never retry an existing
            # close intent without reading and reconciling broker deals first.
            close_id = f"global-emergency:{key}:{ticket}"
            try:
                journal.reserve_close_intent(close_id, {
                    "account_key": key,
                    "symbol": symbol,
                    "ticket": ticket,
                    "reason": state.reason,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
            except Exception:
                failures.append(ticket)
                continue
            attempted.append(ticket)
            try:
                result = executor.close_bot_position(
                    ticket=ticket, venue_symbol=symbol,
                    now=datetime.now(timezone.utc),
                )
                journal.record_close_result(close_id, result, reason="GLOBAL_DEMO_KILL")
                if result.status == "CLOSED_DEMO":
                    acknowledged.append(ticket)
                else:
                    failures.append(ticket)
            except Exception as exc:
                # Network exception may follow a broker-side fill; deliberately
                # no retry, avoid closing a second unrelated position.
                journal.record_error(close_id, exc)
                failures.append(ticket)
    # Independently query broker after all closes. A successful ACK alone
    # cannot guarantee the ticket no longer exists.
    remaining = []
    try:
        for symbol in allowed:
            remaining.extend(int(p["ticket"]) for p in executor.bot_positions(symbol))
    except Exception:
        remaining = tickets
    unresolved = tuple(sorted(set(failures+remaining+[
        t for t in tickets if t not in acknowledged
    ])))
    status = "VERIFIED_BROKER_FLAT_DEMO" if not unresolved else "NEEDS_RECONCILIATION"
    return EmergencyFlattenReport(
        key, status, tuple(attempted), tuple(acknowledged), unresolved,
        _hash({"account":key,"attempted":attempted,"acks":acknowledged,
               "unresolved":unresolved,"kill_reason":state.reason}),
    )
