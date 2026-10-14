from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Iterable

import numpy as np
import pandas as pd

from research_bot.common_anchor_v21 import V21CommonAnchorConfig, V21_PROTOCOL_VERSION
from research_bot.forward_microstructure_v19 import TRADE_SIDE_SEMANTICS
from research_bot.integrity_v19 import payload_sha256


PHASE_Q_V21_START_AT = "2026-09-10T18:00:00+00:00"
PHASE_Q_V21_MATURITY_NOT_BEFORE = "2026-09-17T18:00:00+00:00"
COUNTABLE_EVENT = "schedule"
COUNTABLE_REF = "refs/heads/main"


@dataclass(frozen=True)
class V21PhaseQConfig:
    symbols: tuple[str, ...] = ("BTC/USDT", "ETH/USDT")
    venues: tuple[str, ...] = ("coinex", "okx", "kucoin")
    cadence_minutes: int = 30
    min_elapsed_hours: int = 168
    min_expected_opportunities: int = 336
    min_authorized_coverage_ratio: float = 0.80
    max_p95_book_age_seconds: float = 20.0
    max_p95_trade_staleness_seconds: float = 30.0
    warn_provider_failure_snapshot_ratio: float = 0.20
    warn_duplicate_slot_ratio: float = 0.05
    warn_max_consecutive_missing_slots: int = 4


def _utc(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def protocol_metadata_v21() -> dict:
    return {
        "protocol_version": V21_PROTOCOL_VERSION,
        "pilot_start_at": PHASE_Q_V21_START_AT,
        "maturity_not_before": PHASE_Q_V21_MATURITY_NOT_BEFORE,
        "countable_event": COUNTABLE_EVENT,
        "countable_ref": COUNTABLE_REF,
        "backfill_allowed": False,
        "manual_dispatch_counts_toward_maturity": False,
        "trading_horizon": "4h",
        "measurement_cadence_target": "30min",
        "trade_window_seconds": 60,
        "common_anchor_required": True,
    }


def _critical_config_matches(snapshot: dict) -> bool:
    cfg = snapshot.get("config") or {}
    expected = V21CommonAnchorConfig()
    return (
        tuple(cfg.get("symbols") or ()) == expected.symbols
        and tuple(cfg.get("venues") or ()) == expected.venues
        and int(cfg.get("trade_window_seconds", -1)) == expected.trade_window_seconds
        and int(cfg.get("min_recent_trade_count", -1)) == expected.min_recent_trade_count
        and float(cfg.get("max_trade_staleness_seconds", -1)) == expected.max_trade_staleness_seconds
        and float(cfg.get("max_book_age_seconds", -1)) == expected.max_book_age_seconds
        and float(cfg.get("min_signed_trade_coverage", -1)) == expected.min_signed_trade_coverage
        and float(cfg.get("max_spread_bps", -1)) == expected.max_spread_bps
        and float(cfg.get("max_mid_dispersion_bps", -1)) == expected.max_mid_dispersion_bps
        and int(cfg.get("min_venues_per_symbol", -1)) == expected.min_venues_per_symbol
        and bool(cfg.get("allow_local_book_timestamp_fallback", True)) is False
    )


def validate_phase_q_v21_snapshot(snapshot: dict, *, as_of: str | None = None) -> list[str]:
    reasons: list[str] = []
    if snapshot.get("version") != "v0.21":
        reasons.append("VERSION_MISMATCH")
    if snapshot.get("research_status") != "PROSPECTIVE_COMMON_ANCHOR_MICROSTRUCTURE_COLLECTION_ONLY":
        reasons.append("RESEARCH_STATUS_MISMATCH")
    if snapshot.get("collection_rule") != "prospective_only_common_anchor_no_backfill_no_signal":
        reasons.append("COLLECTION_RULE_MISMATCH")

    digest = str(snapshot.get("snapshot_sha256") or "")
    if not digest:
        reasons.append("SNAPSHOT_HASH_MISSING")
    elif digest != payload_sha256(snapshot, exclude_keys=("snapshot_sha256",)):
        reasons.append("SNAPSHOT_HASH_MISMATCH")

    protocol = snapshot.get("phase_q_protocol") or {}
    expected_protocol = protocol_metadata_v21()
    for key, expected in expected_protocol.items():
        if protocol.get(key) != expected:
            reasons.append(f"PHASE_Q_PROTOCOL_FIELD_MISMATCH:{key}")

    contract = snapshot.get("measurement_contract") or {}
    expected_contract = {
        "primary_trading_horizon": "4h",
        "snapshot_measurement_frequency_target": "30min",
        "reported_trade_window_seconds": 60,
        "trade_window_anchor_mode": "single_common_anchor_per_symbol_cycle",
        "book_age_measured_separately": True,
        "trade_side_semantics": TRADE_SIDE_SEMANTICS,
        "rest_snapshot_not_event_stream": True,
    }
    for key, expected in expected_contract.items():
        if contract.get(key) != expected:
            reasons.append(f"MEASUREMENT_CONTRACT_MISMATCH:{key}")

    if not _critical_config_matches(snapshot):
        reasons.append("CRITICAL_CONFIG_MISMATCH")

    provenance = snapshot.get("ci_provenance") or {}
    if provenance.get("event_name") != COUNTABLE_EVENT:
        reasons.append("NON_SCHEDULE_EVENT")
    if provenance.get("ref") != COUNTABLE_REF:
        reasons.append("NON_MAIN_REF")
    if not provenance.get("sha"):
        reasons.append("MISSING_SOURCE_COMMIT_SHA")
    if provenance.get("repository") not in {"parsa314/modular-crypto-trading-bot"}:
        reasons.append("REPOSITORY_PROVENANCE_MISMATCH")

    if snapshot.get("signal_authorized") is not False:
        reasons.append("SIGNAL_AUTHORIZATION_BREACH")
    if snapshot.get("paper_strategy_replacement_authorized") is not False:
        reasons.append("PAPER_AUTHORIZATION_BREACH")
    if snapshot.get("testnet_promotion_authorized") is not False:
        reasons.append("TESTNET_AUTHORIZATION_BREACH")
    if snapshot.get("live_execution_authorized") is not False:
        reasons.append("LIVE_AUTHORIZATION_BREACH")

    symbols = snapshot.get("symbols") or []
    by_symbol = {row.get("symbol"): row for row in symbols if row.get("symbol")}
    if set(by_symbol) != set(V21CommonAnchorConfig().symbols):
        reasons.append("CONFIGURED_SYMBOL_COVERAGE_SCHEMA_MISMATCH")
    actual_authorized = sum(1 for row in by_symbol.values() if row.get("feature_authorized") is True)
    if int(snapshot.get("authorized_symbol_count", -1)) != actual_authorized:
        reasons.append("AUTHORIZED_SYMBOL_COUNT_MISMATCH")
    for symbol, row in by_symbol.items():
        names = row.get("accepted_venue_names") or []
        if len(names) != len(set(names)):
            reasons.append(f"{symbol}:DUPLICATE_ACCEPTED_VENUE_NAME")
        if row.get("feature_authorized"):
            if row.get("trade_window_start") is None or row.get("trade_window_end") is None or row.get("common_anchor_at") is None:
                reasons.append(f"{symbol}:MISSING_COMMON_WINDOW_METADATA")
            else:
                try:
                    anchor = _utc(row["common_anchor_at"])
                    if _utc(row["trade_window_end"]) != anchor:
                        reasons.append(f"{symbol}:WINDOW_END_NOT_ANCHOR")
                    if _utc(row["trade_window_start"]) != anchor - pd.Timedelta(seconds=60):
                        reasons.append(f"{symbol}:WINDOW_START_NOT_ANCHOR_MINUS_60S")
                except Exception:
                    reasons.append(f"{symbol}:INVALID_COMMON_WINDOW_METADATA")

    try:
        generated = _utc(snapshot.get("generated_at"))
        if generated < _utc(PHASE_Q_V21_START_AT):
            reasons.append("BEFORE_PILOT_START")
        if as_of is not None and generated > _utc(as_of):
            reasons.append("FUTURE_SNAPSHOT_RELATIVE_TO_AS_OF")
    except Exception:
        reasons.append("GENERATED_AT_INVALID")
    return sorted(set(reasons))


def _p95(values: list[float]) -> float | None:
    clean = np.asarray([float(x) for x in values if x is not None and np.isfinite(float(x))], dtype=float)
    return float(np.quantile(clean, 0.95)) if len(clean) else None


def _max_missing_run(slots: list[pd.Timestamp], cadence_minutes: int) -> int:
    if len(slots) < 2:
        return 0
    maximum = 0
    for left, right in zip(slots[:-1], slots[1:]):
        steps = int(round((right - left).total_seconds() / (cadence_minutes * 60.0)))
        maximum = max(maximum, max(0, steps - 1))
    return maximum


def evaluate_phase_q_v21(
    snapshots: Iterable[dict],
    *,
    as_of: str,
    config: V21PhaseQConfig | None = None,
) -> dict:
    cfg = config or V21PhaseQConfig()
    if cfg.cadence_minutes <= 0:
        raise ValueError("cadence_minutes must be positive")
    start = _utc(PHASE_Q_V21_START_AT)
    end = _utc(as_of)
    if end < start:
        raise ValueError("as_of precedes v0.21 pilot start")

    countable_raw: list[dict] = []
    excluded: list[dict] = []
    seen_hashes: set[str] = set()
    for snap in snapshots:
        reasons = validate_phase_q_v21_snapshot(snap, as_of=end.isoformat())
        digest = str(snap.get("snapshot_sha256") or "")
        if digest in seen_hashes and digest:
            reasons = sorted(set(reasons + ["DUPLICATE_SNAPSHOT_HASH"]))
        if reasons:
            excluded.append({"snapshot_sha256": digest or None, "generated_at": snap.get("generated_at"), "reasons": reasons})
            continue
        seen_hashes.add(digest)
        countable_raw.append(snap)

    # Earliest valid scheduled snapshot in each slot wins. This prevents reruns or
    # repeated artifacts inside one nominal slot from cherry-picking a healthier row.
    slots: dict[pd.Timestamp, dict] = {}
    for snap in sorted(countable_raw, key=lambda x: _utc(x["generated_at"])):
        slot = _utc(snap["generated_at"]).floor(f"{int(cfg.cadence_minutes)}min")
        if start <= slot <= end:
            slots.setdefault(slot, snap)
    unique_slots = sorted(slots)

    elapsed_hours = float((end - start).total_seconds() / 3600.0)
    expected = max(0, int(np.floor(elapsed_hours * 60.0 / cfg.cadence_minutes)))
    symbol_stats: dict[str, dict] = {}
    global_reasons: list[str] = []

    for symbol in cfg.symbols:
        authorized = 0
        observed = 0
        book_ages: list[float] = []
        staleness: list[float] = []
        provider_failure_events = 0
        for slot, snap in slots.items():
            provider_failure_events += sum(1 for x in (snap.get("provider_failures") or []) if x.get("symbol") == symbol)
            row = next((x for x in (snap.get("symbols") or []) if x.get("symbol") == symbol), None)
            if row is None:
                continue
            observed += 1
            if row.get("feature_authorized"):
                authorized += 1
                if row.get("max_book_age_seconds") is not None:
                    book_ages.append(float(row["max_book_age_seconds"]))
                if row.get("max_trade_staleness_seconds") is not None:
                    staleness.append(float(row["max_trade_staleness_seconds"]))
        denom = expected if expected > 0 else max(1, len(unique_slots))
        coverage = float(authorized / denom)
        p95_book = _p95(book_ages)
        p95_stale = _p95(staleness)
        reasons: list[str] = []
        if coverage < cfg.min_authorized_coverage_ratio:
            reasons.append("AUTHORIZED_COVERAGE_BELOW_TARGET")
        if p95_book is None or p95_book > cfg.max_p95_book_age_seconds:
            reasons.append("P95_BOOK_AGE_UNACCEPTABLE")
        if p95_stale is None or p95_stale > cfg.max_p95_trade_staleness_seconds:
            reasons.append("P95_TRADE_STALENESS_UNACCEPTABLE")
        symbol_stats[symbol] = {
            "observed_slots": int(observed),
            "authorized_slots": int(authorized),
            "expected_opportunities": int(expected),
            "authorized_coverage_ratio": coverage,
            "p95_book_age_seconds": p95_book,
            "p95_trade_staleness_seconds": p95_stale,
            "provider_failure_events": int(provider_failure_events),
            "quality_reasons": reasons,
        }
        global_reasons.extend(f"{symbol}:{x}" for x in reasons)

    if elapsed_hours < cfg.min_elapsed_hours:
        global_reasons.append("INSUFFICIENT_ELAPSED_TIME")
    if expected < cfg.min_expected_opportunities:
        global_reasons.append("INSUFFICIENT_MEASUREMENT_OPPORTUNITIES")

    duplicate_slot_count = max(0, len(countable_raw) - len(unique_slots))
    duplicate_slot_ratio = float(duplicate_slot_count / max(1, len(countable_raw)))
    failure_snapshots = sum(1 for snap in slots.values() if snap.get("provider_failures"))
    failure_ratio = float(failure_snapshots / max(1, len(unique_slots)))
    max_missing = _max_missing_run(unique_slots, cfg.cadence_minutes)
    warnings: list[str] = []
    if duplicate_slot_ratio > cfg.warn_duplicate_slot_ratio:
        warnings.append("HIGH_DUPLICATE_SLOT_RATIO")
    if failure_ratio > cfg.warn_provider_failure_snapshot_ratio:
        warnings.append("HIGH_PROVIDER_FAILURE_SNAPSHOT_RATIO")
    if max_missing > cfg.warn_max_consecutive_missing_slots:
        warnings.append("LONG_MEASUREMENT_GAP")

    passed = not global_reasons
    ledger_material = "\n".join(str(slots[s]["snapshot_sha256"]) for s in unique_slots)
    ledger_hash = sha256(ledger_material.encode("utf-8")).hexdigest()
    return {
        "version": "v0.21",
        "stage": "PHASE_Q_COMMON_ANCHOR_PROSPECTIVE_DATA_QUALITY",
        "protocol": protocol_metadata_v21(),
        "as_of": end.isoformat(),
        "config": asdict(cfg),
        "elapsed_hours": elapsed_hours,
        "expected_measurement_opportunities": int(expected),
        "countable_unique_slots": int(len(unique_slots)),
        "countable_snapshot_hashes": [str(slots[s]["snapshot_sha256"]) for s in unique_slots],
        "evidence_ledger_sha256": ledger_hash,
        "excluded_snapshot_count": int(len(excluded)),
        "excluded_snapshots": excluded,
        "symbols": symbol_stats,
        "diagnostics": {
            "duplicate_slot_count": int(duplicate_slot_count),
            "duplicate_slot_ratio": duplicate_slot_ratio,
            "snapshots_with_provider_failure": int(failure_snapshots),
            "provider_failure_snapshot_ratio": failure_ratio,
            "max_consecutive_missing_slots_between_observations": int(max_missing),
            "warnings": warnings,
        },
        "decision": "PHASE_Q_V21_PASSED_FEATURE_FREEZE_ALLOWED" if passed else "PHASE_Q_V21_CONTINUE_COLLECTION",
        "data_quality_gate_passed": bool(passed),
        "feature_freeze_authorized": bool(passed),
        "predictive_modeling_authorized": False,
        "predictive_modeling_block_reason": "REQUIRES_SEPARATE_FROZEN_PROSPECTIVE_FEATURE_PROTOCOL",
        "paper_strategy_replacement_authorized": False,
        "testnet_promotion_authorized": False,
        "live_execution_authorized": False,
    }
