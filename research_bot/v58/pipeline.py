"""Deterministic V58 engineering pipeline.

This module connects the implemented research components.  Outcome generation
is deliberately limited to explicitly synthetic fixtures until a real-data
split manifest and holdout gate are sealed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .barriers import BarrierPolicy, DecisionEvent, OutcomeState, materialize_entry, resolve_barriers
from .contracts import Direction, assert_research_only
from .events import stable_hash
from .generators import generate_candidates
from .ledger import EvidenceLedger
from .regimes import add_causal_regime
from .targets import OHLCBar


@dataclass(frozen=True)
class PipelineResult:
    records: tuple[dict, ...]
    ledger: EvidenceLedger
    replay_hash: str


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
    assert_research_only()
    if venue.lower() != "synthetic":
        raise PermissionError("real-market outcome generation is blocked until the data/split seal passes")
    policy = policy or BarrierPolicy()
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
            data_version="SYNTHETIC_ENGINEERING_ONLY", code_version="V58_CORE_REPAIR",
            strategy_version=event.feature_schema_version,
        )
        entry_bar = _bar(frame.iloc[event.row_index + 1])
        entered = materialize_entry(decision, entry_bar=entry_bar, policy=policy)
        path = [_bar(row) for _, row in frame.iloc[event.row_index + 1:event.row_index + 1 + policy.holding_horizon_bars].iterrows()]
        outcome = resolve_barriers(entered, direction=Direction.LONG, bars=path, policy=policy)
        regime_row = regimes.iloc[event.row_index]
        record = {
            "classification": "SYNTHETIC_ENGINEERING_ONLY",
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
                      payload={"arm": event.strategy_arm.value, "subtype": event.setup_subtype},
                      timestamp=event.event_timestamp)
        ledger.append(event_id=event.event_id, stage="ENTRY", status="MATERIALIZED",
                      payload={"entry_id": entered.entry_id, "price": entered.entry_price},
                      timestamp=entered.entry_time)
        ledger.append(event_id=event.event_id, stage="OUTCOME", status=outcome.state.value,
                      payload={"label": record["outcome"], "reason": outcome.exit_reason},
                      timestamp=outcome.resolved_at or entered.entry_time)
    canonical = json.dumps(records, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return PipelineResult(tuple(records), ledger, stable_hash({"records": json.loads(canonical)}))


def write_pipeline_result(result: PipelineResult, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    events = "".join(json.dumps(row, sort_keys=True, allow_nan=False) + "\n" for row in result.records)
    (output / "synthetic_events.jsonl").write_text(events, encoding="utf-8")
    result.ledger.write_jsonl(output / "evidence_ledger.jsonl")
    manifest = {
        "classification": "SYNTHETIC_ENGINEERING_ONLY",
        "event_count": len(result.records), "ledger_valid": result.ledger.verify(),
        "replay_hash": result.replay_hash, "model_training": False,
        "paper_execution": False, "live_execution": False,
    }
    (output / "run_manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8",
    )


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
