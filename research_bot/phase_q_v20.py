from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Iterable

import numpy as np
import pandas as pd

from research_bot.integrity_v19 import payload_sha256
from research_bot.microstructure_quality_v19 import V19QualityGateConfig, evaluate_quality_gate


PHASE_Q_PROTOCOL_VERSION = "v0.20-phase-q-1"
PHASE_Q_PILOT_START_AT = "2026-09-10T13:00:00+00:00"
PHASE_Q_MATURITY_NOT_BEFORE = "2026-09-17T13:00:00+00:00"
COUNTABLE_EVENT = "schedule"
COUNTABLE_REF = "refs/heads/main"


@dataclass(frozen=True)
class V20PhaseQConfig:
    symbols: tuple[str, ...] = ("BTC/USDT", "ETH/USDT")
    cadence_minutes: int = 30
    min_elapsed_hours: int = 168
    min_expected_opportunities: int = 336
    min_authorized_coverage_ratio: float = 0.80
    max_p95_clock_skew_seconds: float = 60.0
    max_p95_trade_staleness_seconds: float = 30.0
    # Diagnostic warning limits. These do not replace the frozen core gate above.
    warn_max_missing_run_slots: int = 4
    warn_provider_failure_snapshot_ratio: float = 0.20
    warn_duplicate_slot_ratio: float = 0.05


def _utc(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def phase_q_protocol_metadata() -> dict:
    return {
        "protocol_version": PHASE_Q_PROTOCOL_VERSION,
        "pilot_start_at": PHASE_Q_PILOT_START_AT,
        "maturity_not_before": PHASE_Q_MATURITY_NOT_BEFORE,
        "countable_event": COUNTABLE_EVENT,
        "countable_ref": COUNTABLE_REF,
        "backfill_allowed": False,
        "manual_dispatch_counts_toward_maturity": False,
        "trading_horizon": "4h",
        "measurement_cadence_target": "30min",
    }


def validate_phase_q_snapshot(snapshot: dict) -> list[str]:
    """Return reasons a snapshot is not countable toward the prospective Phase-Q gate."""
    reasons: list[str] = []
    if snapshot.get("version") != "v0.19":
        reasons.append("VERSION_MISMATCH")
    if snapshot.get("research_status") != "PROSPECTIVE_MULTI_VENUE_MICROSTRUCTURE_COLLECTION_ONLY":
        reasons.append("RESEARCH_STATUS_MISMATCH")
    if snapshot.get("collection_rule") != "prospective_only_fixed_window_no_backfill_no_signal":
        reasons.append("COLLECTION_RULE_MISMATCH")

    digest = str(snapshot.get("snapshot_sha256") or "")
    if not digest:
        reasons.append("SNAPSHOT_HASH_MISSING")
    elif digest != payload_sha256(snapshot, exclude_keys=("snapshot_sha256",)):
        reasons.append("SNAPSHOT_HASH_MISMATCH")

    protocol = snapshot.get("phase_q_protocol") or {}
    if protocol.get("protocol_version") != PHASE_Q_PROTOCOL_VERSION:
        reasons.append("PHASE_Q_PROTOCOL_MISMATCH")
    try:
        if _utc(protocol.get("pilot_start_at")) != _utc(PHASE_Q_PILOT_START_AT):
            reasons.append("PHASE_Q_START_MISMATCH")
    except Exception:
        reasons.append("PHASE_Q_START_INVALID")

    provenance = snapshot.get("ci_provenance") or {}
    if provenance.get("event_name") != COUNTABLE_EVENT:
        reasons.append("NON_SCHEDULE_EVENT")
    if provenance.get("ref") != COUNTABLE_REF:
        reasons.append("NON_MAIN_REF")

    if snapshot.get("signal_authorized") is not False:
        reasons.append("SIGNAL_AUTHORIZATION_BREACH")
    if snapshot.get("paper_strategy_replacement_authorized") is not False:
        reasons.append("PAPER_AUTHORIZATION_BREACH")
    if snapshot.get("live_execution_authorized") is not False:
        reasons.append("LIVE_AUTHORIZATION_BREACH")

    try:
        generated = _utc(snapshot.get("generated_at"))
        if generated < _utc(PHASE_Q_PILOT_START_AT):
            reasons.append("BEFORE_PILOT_START")
    except Exception:
        reasons.append("GENERATED_AT_INVALID")
    return sorted(set(reasons))


def _measurement_slots(snapshots: list[dict], cadence_minutes: int) -> dict[pd.Timestamp, list[dict]]:
    slots: dict[pd.Timestamp, list[dict]] = {}
    for snap in snapshots:
        ts = _utc(snap["generated_at"])
        slot = ts.floor(f"{int(cadence_minutes)}min")
        slots.setdefault(slot, []).append(snap)
    return slots


def _max_missing_run_slots(slots: list[pd.Timestamp], cadence_minutes: int) -> int:
    if len(slots) < 2:
        return 0
    ordered = sorted(slots)
    maximum = 0
    for left, right in zip(ordered[:-1], ordered[1:]):
        delta_slots = int(round((right - left).total_seconds() / (60.0 * cadence_minutes)))
        maximum = max(maximum, max(0, delta_slots - 1))
    return maximum


def _ledger_hash(snapshots: list[dict]) -> str:
    digests = sorted(str(x["snapshot_sha256"]) for x in snapshots)
    return sha256("\n".join(digests).encode("utf-8")).hexdigest()


def evaluate_phase_q(
    snapshots: Iterable[dict],
    *,
    as_of: str,
    config: V20PhaseQConfig | None = None,
) -> dict:
    """Evaluate the frozen seven-day prospective microstructure data-quality pilot.

    Only hash-valid scheduled snapshots produced from ``main`` under the exact
    Phase-Q protocol are countable. Manual dispatches, pull-request smoke tests,
    pre-start rows and tampered payloads are retained in exclusion diagnostics but
    can never increase maturity or coverage.
    """
    cfg = config or V20PhaseQConfig()
    as_of_ts = _utc(as_of)
    if as_of_ts < _utc(PHASE_Q_PILOT_START_AT):
        raise ValueError("as_of precedes pilot_start_at")
    all_snapshots = list(snapshots)
    countable: list[dict] = []
    excluded: list[dict] = []
    for snap in all_snapshots:
        reasons = validate_phase_q_snapshot(snap)
        try:
            if _utc(snap.get("generated_at")) > as_of_ts:
                reasons.append("AFTER_AS_OF")
        except Exception:
            # validate_phase_q_snapshot already records invalid event timestamps.
            pass
        if reasons:
            excluded.append({
                "snapshot_sha256": snap.get("snapshot_sha256"),
                "generated_at": snap.get("generated_at"),
                "reasons": reasons,
            })
        else:
            countable.append(snap)

    qcfg = V19QualityGateConfig(
        symbols=cfg.symbols,
        cadence_minutes=cfg.cadence_minutes,
        min_elapsed_hours=cfg.min_elapsed_hours,
        min_expected_opportunities=cfg.min_expected_opportunities,
        min_authorized_coverage_ratio=cfg.min_authorized_coverage_ratio,
        max_p95_clock_skew_seconds=cfg.max_p95_clock_skew_seconds,
        max_p95_trade_staleness_seconds=cfg.max_p95_trade_staleness_seconds,
    )
    core = evaluate_quality_gate(
        countable,
        pilot_start_at=PHASE_Q_PILOT_START_AT,
        as_of=as_of,
        config=qcfg,
    )

    slots = _measurement_slots(countable, cfg.cadence_minutes)
    unique_slots = sorted(slots)
    duplicate_slot_snapshots = int(sum(max(0, len(rows) - 1) for rows in slots.values()))
    duplicate_slot_ratio = float(duplicate_slot_snapshots / max(1, len(countable)))
    max_missing = _max_missing_run_slots(unique_slots, cfg.cadence_minutes)

    snapshots_with_provider_failure = 0
    provider_failure_events = 0
    venue_seen: dict[str, int] = {}
    venue_accepted: dict[str, int] = {}
    for snap in countable:
        failures = snap.get("provider_failures") or []
        if failures:
            snapshots_with_provider_failure += 1
            provider_failure_events += len(failures)
        for row in snap.get("symbols") or []:
            for venue in row.get("accepted_venue_names") or []:
                venue_accepted[venue] = venue_accepted.get(venue, 0) + 1
            for venue_row in row.get("venues") or []:
                venue = venue_row.get("venue")
                if venue:
                    venue_seen[venue] = venue_seen.get(venue, 0) + 1

    provider_failure_snapshot_ratio = float(snapshots_with_provider_failure / max(1, len(countable)))
    warnings: list[str] = []
    if max_missing > cfg.warn_max_missing_run_slots:
        warnings.append("LONG_MEASUREMENT_GAP")
    if provider_failure_snapshot_ratio > cfg.warn_provider_failure_snapshot_ratio:
        warnings.append("HIGH_PROVIDER_FAILURE_SNAPSHOT_RATIO")
    if duplicate_slot_ratio > cfg.warn_duplicate_slot_ratio:
        warnings.append("HIGH_DUPLICATE_SLOT_RATIO")

    gate_passed = core["decision"] == "GATE_Q_PASSED_FEATURE_FREEZE_ALLOWED"
    return {
        "version": "v0.20",
        "stage": "PHASE_Q_PROSPECTIVE_MICROSTRUCTURE_DATA_QUALITY",
        "protocol": phase_q_protocol_metadata(),
        "as_of": _utc(as_of).isoformat(),
        "config": asdict(cfg),
        "core_gate": core,
        "countable_snapshot_count": int(len(countable)),
        "excluded_snapshot_count": int(len(excluded)),
        "excluded_snapshots": excluded,
        "countable_snapshot_hashes": sorted(str(x["snapshot_sha256"]) for x in countable),
        "evidence_ledger_sha256": _ledger_hash(countable),
        "diagnostics": {
            "unique_measurement_slots": int(len(unique_slots)),
            "duplicate_slot_snapshots": duplicate_slot_snapshots,
            "duplicate_slot_ratio": duplicate_slot_ratio,
            "max_consecutive_missing_slots_between_observations": int(max_missing),
            "snapshots_with_provider_failure": int(snapshots_with_provider_failure),
            "provider_failure_events": int(provider_failure_events),
            "provider_failure_snapshot_ratio": provider_failure_snapshot_ratio,
            "venue_seen_counts": dict(sorted(venue_seen.items())),
            "venue_accepted_counts": dict(sorted(venue_accepted.items())),
            "warnings": warnings,
        },
        "decision": "PHASE_Q_PASSED_FEATURE_FREEZE_ALLOWED" if gate_passed else "PHASE_Q_CONTINUE_PROSPECTIVE_COLLECTION",
        "data_quality_gate_passed": bool(gate_passed),
        "feature_freeze_authorized": bool(gate_passed),
        # Passing data quality does not silently authorize model fitting. A separate
        # frozen Phase-P feature/predictive protocol must be committed first.
        "predictive_modeling_authorized": False,
        "predictive_modeling_block_reason": "REQUIRES_SEPARATE_PRE_REGISTERED_PHASE_P_PROTOCOL",
        "paper_strategy_replacement_authorized": False,
        "testnet_promotion_authorized": False,
        "live_execution_authorized": False,
        "next_allowed_action": "FREEZE_4H_FEATURE_SPEC" if gate_passed else "CONTINUE_PHASE_Q_COLLECTION",
    }
