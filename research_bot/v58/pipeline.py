"""Deterministic V58 engineering pipeline.

This module connects the research components for synthetic engineering checks
and exact, frozen historical development archives. Neither path authorizes
model training, holdout access, or trading execution.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
import math
from hashlib import sha256
from pathlib import Path

import numpy as np
import pandas as pd

from .barriers import BarrierPolicy, DecisionEvent, barrier_policy_hash, materialize_entry, resolve_barriers
from .contracts import Direction, assert_research_only
from .events import stable_hash
from .features import bar_duration
from .data_audit import digest, inspect_csv
from .development_sources import SOURCES
from .immutable_io import write_immutable_bundle
from .generators import generate_candidates
from .ledger import EvidenceLedger
from .regimes import add_causal_regime
from .targets import OHLCBar


@dataclass(frozen=True)
class PipelineResult:
    records: tuple[dict, ...]
    ledger: EvidenceLedger
    replay_hash: str
    metadata: dict


def _source_fingerprint() -> str:
    """Bind results to installed source bytes, including the frozen intake list."""
    return stable_hash({path.name: sha256(path.read_bytes()).hexdigest()
                        for path in sorted(Path(__file__).parent.glob("*.py"))})


def _replay_hash(records: tuple | list, ledger: EvidenceLedger, metadata: dict) -> str:
    entries = ledger.entries
    return stable_hash({"records": records, "metadata": metadata,
                        "ledger_head": entries[-1].entry_hash if entries else None})


def _bar(row: pd.Series) -> OHLCBar:
    return OHLCBar(
        timestamp=pd.Timestamp(row.timestamp).to_pydatetime(),
        open=float(row.open), high=float(row.high), low=float(row.low), close=float(row.close),
    )


def _json_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return value


def run_synthetic_engineering_pipeline(
    frame: pd.DataFrame, *, venue: str = "synthetic", symbol: str = "BTC/USDT",
    timeframe: str = "4h", round_trip_cost_bps: float = 24.0,
    policy: BarrierPolicy | None = None,
) -> PipelineResult:
    """Run the connected event/label path on an explicitly synthetic frame.

    The name and venue check are intentional: this function is proof that the
    software path executes, not authorization to inspect historical outcomes.
    """
    return _run_pipeline(
        frame, venue=venue, symbol=symbol, timeframe=timeframe,
        round_trip_cost_bps=round_trip_cost_bps, policy=policy,
        classification="SYNTHETIC_ENGINEERING_ONLY",
        data_version=sha256(frame.to_json(orient="split", date_format="iso", date_unit="ns",
                                         double_precision=15).encode()).hexdigest(),
        provenance={"source": "CALLER_SUPPLIED_SYNTHETIC_FIXTURE"},
    )


def run_verified_development_pipeline(
    raw_csv: bytes, *, venue: str, symbol: str, dataset_sha256: str,
    manifest_status: str, timeframe: str = "4h", round_trip_cost_bps: float = 24.0,
    policy: BarrierPolicy | None = None,
) -> PipelineResult:
    """Parse exact CSV bytes pinned by the reviewed development-source registry.

    A DataFrame, claimed digest, or status string cannot authenticate a source.
    Registry updates require an audited intake; there is no caller override.
    """
    if manifest_status != "HISTORICAL_BYTES_VERIFIED_DEVELOPMENT_ONLY":
        raise PermissionError("dataset is not authorized by the development-only intake")
    if venue.lower() not in {"binance", "coinex"}:
        raise ValueError("unsupported audited venue")
    if len(dataset_sha256) != 64 or any(c not in "0123456789abcdef" for c in dataset_sha256.lower()):
        raise ValueError("dataset_sha256 must be SHA-256 hex")
    if not isinstance(raw_csv, bytes):
        raise PermissionError("verified development intake requires the original CSV bytes")
    actual_hash = digest(raw_csv)
    source = next((row for row in SOURCES if row["venue"] == venue.lower()
                   and row["symbol"] == symbol and row["csv_sha256"] == actual_hash), None)
    if source is None or dataset_sha256.lower() != actual_hash:
        raise PermissionError("CSV identity is not a frozen development source")
    if timeframe != "4h":
        raise ValueError("frozen development sources require the 4h timeframe")
    frame, quality = inspect_csv(raw_csv)
    if any(quality[key] != source[key] for key in
           ("row_count", "start_timestamp", "end_timestamp", "schema_hash")):
        raise PermissionError("CSV structure differs from the frozen development source")
    return _run_pipeline(
        frame, venue=venue.lower(), symbol=symbol, timeframe=timeframe,
        round_trip_cost_bps=round_trip_cost_bps, policy=policy,
        classification="REAL_MARKET_DEVELOPMENT_EVIDENCE", data_version=actual_hash,
        provenance=dict(source),
    )


def _run_pipeline(
    frame: pd.DataFrame, *, venue: str, symbol: str, timeframe: str,
    round_trip_cost_bps: float, policy: BarrierPolicy | None,
    classification: str, data_version: str, provenance: dict,
) -> PipelineResult:
    assert_research_only()
    if classification == "SYNTHETIC_ENGINEERING_ONLY" and venue.lower() != "synthetic":
        raise PermissionError("real-market outcome generation requires verified development intake")
    policy = policy or BarrierPolicy()
    if not math.isfinite(float(round_trip_cost_bps)) or round_trip_cost_bps < 0:
        raise ValueError("round_trip_cost_bps must be finite and non-negative")
    duration_seconds = int(bar_duration(timeframe).total_seconds())
    metadata = {
        "evidence_schema_version": 2, "classification": classification,
        "data_version": data_version, "source_code_sha256": _source_fingerprint(),
        "venue": venue, "symbol": symbol, "timeframe": timeframe,
        "bar_duration_seconds": duration_seconds, "policy": asdict(policy),
        "policy_hash": barrier_policy_hash(policy), "round_trip_cost_bps": round_trip_cost_bps,
        "provenance": provenance,
    }
    candidates = generate_candidates(frame, venue=venue, symbol=symbol, timeframe=timeframe)
    regimes = add_causal_regime(frame, timeframe=timeframe)
    ledger = EvidenceLedger()
    records: list[dict] = []
    for event in candidates:
        if event.row_index + 1 >= len(frame):
            continue
        decision = DecisionEvent(
            event_id=event.event_id, symbol=event.symbol, venue=event.venue,
            strategy_arm=event.strategy_arm, direction=event.direction,
            decision_time=event.event_timestamp, decision_atr=event.atr_at_event,
            feature_snapshot_id=event.feature_snapshot_id,
            data_version=data_version, code_version=metadata["source_code_sha256"],
            strategy_version=event.feature_schema_version,
            bar_duration_seconds=duration_seconds,
        )
        entry_bar = _bar(frame.iloc[event.row_index + 1])
        entered = materialize_entry(decision, entry_bar=entry_bar, policy=policy)
        path = [_bar(row) for _, row in frame.iloc[event.row_index + 1:event.row_index + 1 + policy.holding_horizon_bars].iterrows()]
        outcome = resolve_barriers(entered, direction=Direction.LONG, bars=path, policy=policy)
        regime_row = regimes.iloc[event.row_index]
        record = {
            "classification": classification,
            "data_version": data_version, "source_code_sha256": metadata["source_code_sha256"],
            "policy_hash": entered.policy_hash, "strategy_version": event.feature_schema_version,
            "event_id": event.event_id, "venue": event.venue, "symbol": event.symbol,
            "timeframe": event.timeframe, "event_timestamp": event.event_timestamp.isoformat(),
            "strategy_arm": event.strategy_arm.value, "setup_subtype": event.setup_subtype,
            "direction": event.direction.value, "feature_snapshot_id": event.feature_snapshot_id,
            "atr_at_event": event.atr_at_event, "entry_time": entered.entry_time.isoformat(),
            "entry_price": entered.entry_price, "stop_price": entered.stop_price,
            "target_price": entered.target_price, "horizon_bars": policy.holding_horizon_bars,
            "outcome_state": outcome.state.value,
            "outcome": outcome.target_class.value if outcome.target_class else None,
            "bars_observed": outcome.observed_bars,
            "resolved_at": outcome.resolved_at.isoformat() if outcome.resolved_at else None,
            "exit_price": outcome.exit_price, "exit_reason": outcome.exit_reason,
            "ambiguous_bar": outcome.intrabar_ambiguity,
            "gross_return": outcome.gross_return, "gross_return_R": outcome.gross_return_r,
            "cost_bps": round_trip_cost_bps,
            "net_return": outcome.after_cost(round_trip_cost_bps),
            "regime_label": regime_row.regime,
            "regime_confidence": float(regime_row.regime_confidence),
            "contributing_families": list(event.contributing_families),
            "states": list(event.states),
        }
        records.append(record)
        ledger.append(event_id=event.event_id, stage="CANDIDATE", status="GENERATED",
                      payload={"arm": event.strategy_arm.value, "subtype": event.setup_subtype,
                               "run_metadata_hash": stable_hash(metadata)},
                      timestamp=event.event_timestamp)
        ledger.append(event_id=event.event_id, stage="ENTRY", status="MATERIALIZED",
                      payload={"entry_id": entered.entry_id, "price": entered.entry_price},
                      timestamp=entered.entry_time)
        ledger.append(event_id=event.event_id, stage="OUTCOME", status=outcome.state.value,
                      payload={"label": record["outcome"], "reason": outcome.exit_reason},
                      timestamp=outcome.resolved_at or entered.entry_time)
    canonical = json.dumps(records, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return PipelineResult(tuple(json.loads(canonical)), ledger, _replay_hash(records, ledger, metadata), metadata)


def pipeline_result_files(result: PipelineResult) -> dict[str, bytes]:
    """Validate the complete result before generating any filesystem effects."""
    if not result.ledger.verify():
        raise ValueError("ledger integrity verification failed")
    classification = result.metadata.get("classification")
    if classification not in {"SYNTHETIC_ENGINEERING_ONLY", "REAL_MARKET_DEVELOPMENT_EVIDENCE"}:
        raise ValueError("missing or invalid evidence classification")
    if any(row.get("classification") != classification for row in result.records):
        raise ValueError("mixed evidence classifications")
    if result.replay_hash != _replay_hash(result.records, result.ledger, result.metadata):
        raise ValueError("result integrity verification failed")
    events = "".join(json.dumps(row, sort_keys=True, allow_nan=False) + "\n" for row in result.records)
    ledger = "".join(json.dumps(asdict(entry), sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
                     for entry in result.ledger.entries)
    manifest = {
        "classification": classification, "metadata": result.metadata,
        "event_count": len(result.records), "ledger_valid": result.ledger.verify(),
        "replay_hash": result.replay_hash, "model_training": False,
        "paper_execution": False, "live_execution": False,
    }
    return {"synthetic_events.jsonl": events.encode("utf-8"),
            "evidence_ledger.jsonl": ledger.encode("utf-8"),
            "run_manifest.json": (json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")}


def write_pipeline_result(result: PipelineResult, output: Path) -> None:
    write_immutable_bundle(output, pipeline_result_files(result))


def synthetic_integration_fixture() -> pd.DataFrame:
    """Non-market fixture that deterministically emits all five strategy arms."""
    n = 250
    x = pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC"),
        "open": np.full(n, 99.9), "high": np.full(n, 101.0),
        "low": np.full(n, 99.0), "close": np.full(n, 100.0), "volume": np.full(n, 1000.0),
    })
    x.loc[210, "high"] = 105.0
    x.loc[220, ["open", "high", "low", "close"]] = [100.0, 100.5, 97.0, 99.5]
    x.loc[221, ["open", "high", "low", "close"]] = [98.0, 100.6, 97.9, 100.5]
    x.loc[222, ["open", "high", "low", "close"]] = [100.0, 110.1, 99.9, 110.0]
    x.loc[223, ["open", "high", "low", "close"]] = [110.0, 111.0, 109.0, 110.0]
    return x
