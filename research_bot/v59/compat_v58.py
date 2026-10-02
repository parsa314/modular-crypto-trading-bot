from __future__ import annotations

from datetime import datetime
from typing import Any

from .contracts import Direction, Regime, SignalCandidate
from .hashing import stable_hash


def _parse_utc(value: Any) -> datetime:
    from pandas import Timestamp

    stamp = Timestamp(value)
    if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError("V58 adapter requires UTC timestamps")
    return stamp.to_pydatetime()


def adapt_v58_signal(row: dict[str, Any], *, data_version: str, strategy_version: str) -> SignalCandidate:
    """Translate one V58 unified-signal row into the V59 immutable contract.

    The adapter does not invent absent regime evidence. Missing regime evidence
    is represented explicitly as UNKNOWN/0.0, which will fail the default
    economic regime-confidence gate until a proper V59 regime snapshot is joined.
    """
    required = {
        "signal_id", "strategy_id", "family", "symbol", "venue", "direction",
        "signal_time", "entry_time", "entry_reference_price", "stop_price",
        "target_price",
    }
    missing = required - row.keys()
    if missing:
        raise ValueError(f"V58 signal missing fields: {sorted(missing)}")
    direction = Direction.LONG if str(row["direction"]).lower() == "long" else Direction.SHORT
    decision_at = _parse_utc(row["signal_time"])
    entry_time = _parse_utc(row["entry_time"])
    if entry_time <= decision_at:
        # V58 signal files can encode decision time equal to next-open timestamp.
        # V59 requires explicit strictly-later entry time, so an ambiguous row
        # must not be silently upgraded.
        raise ValueError("V58 signal lacks a strictly-after-decision entry timestamp for V59")
    source_hash = stable_hash(row)
    feature_snapshot_id = stable_hash(
        {
            "source_signal_id": row["signal_id"],
            "confirmations": row.get("confirmations", []),
            "family": row["family"],
        }
    )
    return SignalCandidate(
        event_id=str(row["signal_id"]),
        strategy_id=str(row["strategy_id"]),
        strategy_family=str(row["family"]),
        symbol=str(row["symbol"]),
        venue=str(row["venue"]),
        direction=direction,
        decision_at=decision_at,
        entry_time=entry_time,
        entry_price=float(row["entry_reference_price"]),
        stop_price=float(row["stop_price"]),
        target_price=float(row["target_price"]),
        horizon_bars=int(row.get("horizon_bars", 12)),
        regime=Regime.UNKNOWN,
        regime_confidence=0.0,
        feature_snapshot_id=feature_snapshot_id,
        data_version=data_version,
        strategy_version=strategy_version,
        source_hash=source_hash,
        confirmations=tuple(row.get("confirmations", ())),
    )
