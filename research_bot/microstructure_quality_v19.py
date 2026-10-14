from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class V19QualityGateConfig:
    symbols: tuple[str, ...] = ("BTC/USDT", "ETH/USDT")
    cadence_minutes: int = 30
    min_elapsed_hours: int = 168
    min_expected_opportunities: int = 336
    min_authorized_coverage_ratio: float = 0.80
    max_p95_clock_skew_seconds: float = 60.0
    max_p95_trade_staleness_seconds: float = 30.0


def _utc(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _p95(values: list[float]) -> float | None:
    clean = np.asarray([float(x) for x in values if x is not None and np.isfinite(float(x))], dtype=float)
    return float(np.quantile(clean, 0.95)) if len(clean) else None


def _snapshot_time(snapshot: dict) -> pd.Timestamp | None:
    value = snapshot.get("generated_at")
    if value is None:
        return None
    try:
        return _utc(value)
    except Exception:
        return None


def evaluate_quality_gate(
    snapshots: Iterable[dict],
    *,
    pilot_start_at: str,
    as_of: str | None = None,
    config: V19QualityGateConfig | None = None,
) -> dict:
    """Evaluate the pre-registered prospective data-quality maturity gate.

    Snapshot opportunity counts are based on elapsed wall-clock time from an
    explicitly supplied pilot start. Actual snapshots are deduplicated by their
    final SHA-256 and then collapsed to 30-minute measurement slots, so repeated
    manual reruns cannot inflate coverage.
    """
    cfg = config or V19QualityGateConfig()
    if cfg.cadence_minutes <= 0:
        raise ValueError("cadence_minutes must be positive")
    start = _utc(pilot_start_at)
    rows = []
    seen_hashes: set[str] = set()
    for snap in snapshots:
        digest = str(snap.get("snapshot_sha256") or "")
        if not digest or digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        ts = _snapshot_time(snap)
        if ts is None or ts < start:
            continue
        rows.append((ts, snap))

    inferred_as_of = max((ts for ts, _ in rows), default=start)
    end = _utc(as_of) if as_of is not None else inferred_as_of
    if end < start:
        raise ValueError("as_of precedes pilot_start_at")

    # As-of evaluations must never count observations recorded after that cutoff.
    # Keep the reported unique-hash count consistent with admitted observations.
    rows = [(ts, snap) for ts, snap in rows if ts <= end]
    seen_hashes = {str(snap["snapshot_sha256"]) for _, snap in rows}

    elapsed_hours = float((end - start).total_seconds() / 3600.0)
    expected = int(np.floor(elapsed_hours * 60.0 / cfg.cadence_minutes))
    expected = max(0, expected)

    # Slots are based on actual timestamps, not nominal cron start times. Multiple
    # reruns in the same slot count once.
    unique_slots: dict[pd.Timestamp, dict] = {}
    for ts, snap in sorted(rows, key=lambda x: x[0]):
        slot = ts.floor(f"{cfg.cadence_minutes}min")
        unique_slots.setdefault(slot, snap)

    symbol_stats: dict[str, dict] = {}
    for symbol in cfg.symbols:
        authorized_slots: set[pd.Timestamp] = set()
        accepted_venues: list[float] = []
        clock_skews: list[float] = []
        trade_staleness: list[float] = []
        provider_failures = 0
        observed_symbol_slots = 0
        for slot, snap in unique_slots.items():
            provider_failures += sum(1 for x in (snap.get("provider_failures") or []) if x.get("symbol") == symbol)
            row = next((x for x in (snap.get("symbols") or []) if x.get("symbol") == symbol), None)
            if row is None:
                continue
            observed_symbol_slots += 1
            if row.get("feature_authorized"):
                authorized_slots.add(slot)
                if row.get("accepted_venues") is not None:
                    accepted_venues.append(float(row["accepted_venues"]))
                if row.get("venue_clock_skew_seconds") is not None:
                    clock_skews.append(float(row["venue_clock_skew_seconds"]))
                for venue in row.get("venues") or []:
                    stale = venue.get("trade_staleness_seconds")
                    if stale is not None:
                        trade_staleness.append(float(stale))

        denom = expected if expected > 0 else max(1, len(unique_slots))
        coverage = float(len(authorized_slots) / denom)
        skew95 = _p95(clock_skews)
        stale95 = _p95(trade_staleness)
        reasons: list[str] = []
        if coverage < cfg.min_authorized_coverage_ratio:
            reasons.append("AUTHORIZED_COVERAGE_BELOW_TARGET")
        if skew95 is None or skew95 > cfg.max_p95_clock_skew_seconds:
            reasons.append("P95_CLOCK_SKEW_UNACCEPTABLE")
        if stale95 is None or stale95 > cfg.max_p95_trade_staleness_seconds:
            reasons.append("P95_TRADE_STALENESS_UNACCEPTABLE")
        symbol_stats[symbol] = {
            "observed_symbol_slots": int(observed_symbol_slots),
            "authorized_slots": int(len(authorized_slots)),
            "expected_opportunities": int(expected),
            "authorized_coverage_ratio": coverage,
            "mean_accepted_venues": float(np.mean(accepted_venues)) if accepted_venues else None,
            "p95_clock_skew_seconds": skew95,
            "p95_trade_staleness_seconds": stale95,
            "provider_failure_events": int(provider_failures),
            "quality_reasons": reasons,
        }

    global_reasons: list[str] = []
    if elapsed_hours < cfg.min_elapsed_hours:
        global_reasons.append("INSUFFICIENT_ELAPSED_TIME")
    if expected < cfg.min_expected_opportunities:
        global_reasons.append("INSUFFICIENT_MEASUREMENT_OPPORTUNITIES")
    for symbol, stats in symbol_stats.items():
        global_reasons.extend(f"{symbol}:{x}" for x in stats["quality_reasons"])

    passed = not global_reasons
    return {
        "version": "v0.19",
        "gate": "GATE_Q_PROSPECTIVE_MICROSTRUCTURE_DATA_QUALITY",
        "pilot_start_at": start.isoformat(),
        "as_of": end.isoformat(),
        "elapsed_hours": elapsed_hours,
        "unique_snapshot_hashes": int(len(seen_hashes)),
        "unique_measurement_slots": int(len(unique_slots)),
        "expected_measurement_opportunities": int(expected),
        "symbols": symbol_stats,
        "decision": "GATE_Q_PASSED_FEATURE_FREEZE_ALLOWED" if passed else "GATE_Q_NOT_MATURE",
        "reasons": sorted(set(global_reasons)),
        "feature_predictive_modeling_authorized": bool(passed),
        "paper_strategy_replacement_authorized": False,
        "live_execution_authorized": False,
        "config": asdict(cfg),
    }
