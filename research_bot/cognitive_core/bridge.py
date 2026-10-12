from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Any

from ..contracts import Decision, EvidenceStamp, SignalEvidence
from ..decision import DecisionEngine

FREEZE_SHA = "a4302666178d8727f27d6da06e3b49eb166a7e55"
MANIFEST_SHA256 = "08c731c4bbd959ef81e1a3581ba0abc05e5cac600d05bff4eaf7560f24e21819"


def load_protocol(path: Path | None = None) -> dict[str, Any]:
    """Load the byte-locked protocol from a source checkout, never from outcomes."""
    target = path or Path(__file__).with_name("manifest.json")
    raw = target.read_bytes()
    if sha256(raw).hexdigest() != MANIFEST_SHA256:
        raise ValueError("PREREGISTRATION_DIGEST_MISMATCH")
    result = json.loads(raw)
    adr = target.with_name("ADR.md").read_bytes()
    if sha256(adr).hexdigest() != result["adr_sha256"]:
        raise ValueError("ADR_DIGEST_MISMATCH")
    return result


def _aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("TIMEZONE_REQUIRED")


def _digest(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("SHA256_REQUIRED")


def _time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class DetectorEvent:
    """Input DTO only: no new decision/status hierarchy or implied forecast."""

    asset: str
    venue: str
    strategy_id: str
    confirmed_at: datetime
    decision_at: datetime
    entry_not_before: datetime
    detector_version: str
    snapshot_sha256: str
    evidence: tuple[EvidenceStamp, ...]
    reasons: tuple[str, ...]
    side: str = "LONG"
    fixture: bool = False


@dataclass(frozen=True)
class Forecast:
    """Gross return in decimal units; penalties in the same units.

    Caller must provide a frozen, independently validated scoring object.
    Scorer identity alone does not prove calibration or authorize execution.
    """

    expected_return: float
    risk_penalty: float
    uncertainty_penalty: float
    confidence: float
    scorer_sha256: str
    available_at: datetime


def bridge_signal(
    event: DetectorEvent,
    forecast: Forecast | None = None,
    *,
    decision_engine: DecisionEngine | None = None,
) -> SignalEvidence:
    protocol = load_protocol()
    for value in (event.confirmed_at, event.decision_at, event.entry_not_before):
        _aware(value)
    if not event.confirmed_at <= event.decision_at < event.entry_not_before:
        raise ValueError("CAUSAL_TIME_ORDER_REQUIRED")
    if event.venue != "CoinEx" or event.asset not in protocol["future_window"]["assets"]:
        raise ValueError("VENUE_OR_ASSET_OUT_OF_SCOPE")
    if event.side != "LONG" or event.detector_version != "1.0.0":
        raise ValueError("SIDE_OR_DETECTOR_VERSION_OUT_OF_SCOPE")
    if event.strategy_id not in protocol["strategy_ids"]:
        raise ValueError("STRATEGY_NOT_REGISTERED")
    _digest(event.snapshot_sha256)
    if not isinstance(event.fixture, bool):
        raise ValueError("FIXTURE_FLAG_MUST_BE_BOOLEAN")
    if not event.evidence or not event.reasons or any(not isinstance(r, str) or not r for r in event.reasons):
        raise ValueError("EVIDENCE_AND_REASONS_REQUIRED")
    for stamp in event.evidence:
        _aware(stamp.observed_at)
        _aware(stamp.available_at)
        if (not stamp.source or stamp.observed_at > event.confirmed_at
                or not stamp.is_available(event.decision_at)):
            raise ValueError("POINT_IN_TIME_EVIDENCE_NOT_AVAILABLE")
    sealed = protocol["data"]["prohibited_holdouts"][0]
    start = datetime.fromisoformat(sealed["start_bar_open"])
    end = datetime.fromisoformat(sealed["expected_last_bar_close"])
    future = datetime.fromisoformat(protocol["future_window"]["start_open"])
    times = (event.confirmed_at, event.decision_at, event.entry_not_before,
             *(s.observed_at for s in event.evidence))
    if any(start <= t <= end or t >= future for t in times):
        raise ValueError("SEALED_WINDOW_ACCESS_FORBIDDEN")
    # Freeze deliberately records unrecovered raw bytes. Do not let caller metadata
    # assert dataset integrity; a separately reviewed amendment must enable real use.
    if not event.fixture:
        raise ValueError("RAW_DATA_NOT_VERIFIED_ENGINEERING_FIXTURES_ONLY")
    identity = {
        "asset": event.asset, "venue": event.venue, "strategy_id": event.strategy_id,
        "confirmed_at": _time(event.confirmed_at), "snapshot": event.snapshot_sha256,
        "detector_version": event.detector_version, "prereg": MANIFEST_SHA256,
        "side": event.side,
    }
    event_id = sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    metadata = {
        **identity, "event_id": event_id, "freeze_sha": FREEZE_SHA,
        "decision_at": _time(event.decision_at),
        "entry_not_before": _time(event.entry_not_before),
        "evidence_state": "ENGINEERING", "fixture": True,
        "promotion_allowed": False, "execution_allowed": False,
        "cost_model_id": protocol["cost_model"]["id"],
    }
    cost = protocol["cost_model"]["primary_bps"] / 10000
    if forecast is None:
        return SignalEvidence(
            asset=event.asset, timestamp=event.decision_at, expected_return=0.0,
            expected_cost=cost, risk_penalty=0.0, uncertainty_penalty=0.0,
            confidence=0.0, decision=Decision.NO_TRADE,
            reasons=event.reasons + ("FORECAST_UNAVAILABLE",), evidence=event.evidence,
            metadata={**metadata, "forecast_state": "UNESTIMATED",
                      "numeric_placeholders_are_estimates": False},
        )
    _digest(forecast.scorer_sha256)
    _aware(forecast.available_at)
    if forecast.available_at > event.decision_at:
        raise ValueError("FORECAST_NOT_AVAILABLE")
    values = (forecast.expected_return, forecast.risk_penalty,
              forecast.uncertainty_penalty, forecast.confidence)
    if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in values):
        raise ValueError("FINITE_FORECAST_REQUIRED")
    if (forecast.risk_penalty < 0 or forecast.uncertainty_penalty < 0
            or not 0 <= forecast.confidence <= 1):
        raise ValueError("INVALID_FORECAST_BOUNDS")
    signal = (decision_engine or DecisionEngine()).evaluate(
        asset=event.asset, timestamp=event.decision_at,
        expected_return=forecast.expected_return, expected_cost=cost,
        risk_penalty=forecast.risk_penalty, uncertainty_penalty=forecast.uncertainty_penalty,
        confidence=forecast.confidence, evidence=event.evidence,
        metadata={**metadata, "forecast_state": "FIXTURE_SCORED",
                  "scorer_sha256": forecast.scorer_sha256},
    )
    from dataclasses import replace
    return replace(signal, reasons=event.reasons + signal.reasons)
