"""Research-only, causal adapter around the four frozen baseline strategies.

It never creates orders, probabilities, prices for execution or economic claims.
Bar availability must be supplied by the caller; historical close-time assumptions
are explicitly distinguished from observed first-seen timestamps.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Mapping

import pandas as pd

from .strategies import al_brooks_strategy, ichimoku_strategy, ict_strategy, smc_strategy
from .strategies.common import closed_bars

BASELINE_SOURCE_SHA = "3f6ceca5b87bead9417172253d74278a3ccb375b"
BASELINE_BLOBS = {
    "ICHIMOKU": "ef1ff4fc6964778532480f258f7970b5bc1133eb",
    "ICT": "50a02bc82b4230de98cd4ae4d44a47294ea453b7",
    "SMC": "03047417a3da3ddfb7d9b3e063f27dfca913f672",
    "AL_BROOKS": "e22647dca9efb31f186f3fd913bd9702c4352e22",
}
_ENGINES = {"ICHIMOKU": ichimoku_strategy, "ICT": ict_strategy,
            "SMC": smc_strategy, "AL_BROOKS": al_brooks_strategy}
_DURATIONS = {"M15": timedelta(minutes=15), "H1": timedelta(hours=1), "H4": timedelta(hours=4)}


@dataclass(frozen=True)
class SignalCandidate:
    symbol: str
    style: str
    direction: str
    timeframe: str
    decision_at: str
    available_at: str
    setup_type: str
    features: Mapping[str, Any]
    invalidation: None
    expiry: None
    evidence_id: str
    source_id: str
    source_sha256: str
    availability_kind: str
    strategy_blob_sha: str
    eligible_for_execution: bool = False
    status: str = "RESEARCH_ONLY_INCOMPLETE_CONTRACT"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _aware(value: Any) -> pd.Timestamp:
    t = pd.Timestamp(value)
    if pd.isna(t) or t.tzinfo is None or t.utcoffset() is None:
        raise ValueError("TIMEZONE_AWARE_TIMESTAMP_REQUIRED")
    return t.tz_convert("UTC")


def baseline_candidates(frame: pd.DataFrame, *, symbol: str, timeframe: str,
                        as_of: datetime, source_id: str, source_sha256: str,
                        availability_kind: str) -> tuple[SignalCandidate, ...]:
    """Produce baseline research events from completed, contiguous PIT bars.

    Only rows available by as_of are supplied to strategy engines. The provided
    data hash identifies the caller's raw file, while input_hash identifies the
    exact consumed closed-bar subset. Neither proves external provenance.
    """
    if not symbol or not source_id or timeframe not in _DURATIONS:
        raise ValueError("INVALID_SOURCE_SYMBOL_OR_TIMEFRAME")
    if len(source_sha256) != 64 or any(c not in "0123456789abcdef" for c in source_sha256):
        raise ValueError("SOURCE_SHA256_REQUIRED")
    if availability_kind not in {"FIRST_SEEN_OBSERVED", "HISTORICAL_CLOSE_ASSUMED"}:
        raise ValueError("AVAILABILITY_PROVENANCE_REQUIRED")
    cutoff = _aware(as_of)
    if not {"timestamp", "available_at"}.issubset(frame.columns):
        raise ValueError("MISSING_EVENT_TIME")
    x = frame.copy()
    x["timestamp"] = x["timestamp"].map(_aware)
    x["available_at"] = x["available_at"].map(_aware)
    x = x.loc[(x["timestamp"] <= cutoff) & (x["available_at"] <= cutoff)].copy()
    if x.empty:
        raise ValueError("NO_AVAILABLE_BARS")
    if (x["available_at"] < x["timestamp"] + _DURATIONS[timeframe]).any():
        raise ValueError("BAR_NOT_CLOSED_WHEN_AVAILABLE")
    x = closed_bars(x, as_of=cutoff.to_pydatetime(), min_rows=90)
    if not x["timestamp"].diff().dropna().eq(pd.Timedelta(_DURATIONS[timeframe])).all():
        raise ValueError("NONCONTIGUOUS_BARS")
    columns = ["timestamp", "available_at", "open", "high", "low", "close", "volume"]
    # Hash canonical textual representations of the normalized values rather
    # than rounded JSON floats; the source-file digest remains a separate field.
    material = json.dumps([[v.isoformat() if isinstance(v, pd.Timestamp) else str(v)
                            for v in row] for row in x[columns].itertuples(index=False,name=None)],
                          separators=(",", ":"),ensure_ascii=True)
    input_hash = hashlib.sha256(material.encode()).hexdigest()
    result = []
    for name, engine in _ENGINES.items():
        signal = engine.evaluate(x, as_of=cutoff.to_pydatetime())
        if signal.action == "HOLD":
            continue
        decision_at = _aware(signal.decision_at)
        features = {"baseline_reasons": list(signal.reasons), "baseline_evidence": dict(signal.evidence),
                    "uncalibrated_score": signal.score, "score_is_probability": False,
                    "consumed_rows": len(x), "input_sha256": input_hash,
                    "feature_available_at": decision_at.isoformat()}
        content = {"symbol": symbol, "style": name, "direction": signal.action, "timeframe": timeframe,
                   "decision_at": decision_at.isoformat(), "features": features,
                   "source_sha256": source_sha256, "source_id": source_id,
                   "availability_kind": availability_kind, "strategy_blob_sha": BASELINE_BLOBS[name]}
        event_id = hashlib.sha256(json.dumps(content,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
        result.append(SignalCandidate(symbol, name, signal.action, timeframe, decision_at.isoformat(),
            decision_at.isoformat(), "BASELINE:"+"|".join(signal.reasons), features, None, None,
            event_id, source_id, source_sha256, availability_kind, BASELINE_BLOBS[name]))
    return tuple(result)
