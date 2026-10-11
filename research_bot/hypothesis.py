"""ADR-JAM-001: uncalibrated hypotheses, separate from decision evidence.

Keep contracts.py intact: it is a module, not a contracts/ package. Financial
forecasts do not belong to indicator generators until independently calibrated.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
import math
from types import MappingProxyType
from typing import Any, Mapping

from .contracts import EvidenceStamp, ResearchStatus
from .jamalian_data import available, aware, currencies


class HypothesisFamily(str, Enum):
    TECHNICAL_BASELINE = "TECHNICAL_BASELINE"
    MEAN_REVERSION = "MEAN_REVERSION"
    CONFLUENCE = "CONFLUENCE"


class HypothesisSide(str, Enum):
    LONG = "LONG"
    NEUTRAL = "NEUTRAL"


def utc_text(value: datetime) -> str:
    aware(value)
    return value.astimezone(timezone.utc).isoformat()


def freeze(value: Any) -> Any:
    """Detach caller containers and recursively freeze a finite JSON value."""
    if isinstance(value, Mapping):
        if not all(isinstance(k, str) for k in value):
            raise ValueError("mapping keys must be strings")
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, Decimal) and value.is_finite():
        return str(value)
    raise ValueError("metadata must contain only finite JSON values")


def plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [plain(v) for v in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(plain(freeze(value)), sort_keys=True, separators=(",", ":"),
                     allow_nan=False, ensure_ascii=False)


@dataclass(frozen=True)
class HypothesisCandidate:
    strategy_id: str
    family: HypothesisFamily
    symbol: str
    side: HypothesisSide
    timestamp: datetime
    earliest_executable: datetime
    reasons: tuple[str, ...]
    evidence: tuple[EvidenceStamp, ...]
    parameters: Mapping[str, Any]
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, str) or not self.strategy_id.strip():
            raise ValueError("strategy identity required")
        if not isinstance(self.family, HypothesisFamily) or not isinstance(self.side, HypothesisSide):
            raise ValueError("typed hypothesis family and side required")
        currencies(self.symbol)
        aware(self.timestamp)
        aware(self.earliest_executable)
        if self.earliest_executable <= self.timestamp:
            raise ValueError("execution must occur strictly after decision time")
        reasons, evidence = tuple(self.reasons), tuple(self.evidence)
        if not reasons or not all(isinstance(r, str) and r.strip() for r in reasons):
            raise ValueError("nonempty hypothesis reasons required")
        if not evidence or not all(isinstance(e, EvidenceStamp) for e in evidence):
            raise ValueError("point-in-time input evidence required")
        for stamp in evidence:
            available(stamp, self.timestamp)
        for name in ("parameters", "metadata"):
            if not isinstance(getattr(self, name), Mapping):
                raise ValueError("parameters and metadata must be mappings")
            object.__setattr__(self, name, freeze(getattr(self, name)))
        object.__setattr__(self, "reasons", reasons)
        object.__setattr__(self, "evidence", evidence)

    @property
    def status(self) -> ResearchStatus:
        return ResearchStatus.HYPOTHESIS

    @property
    def hypothesis_id(self) -> str:
        # Explicit decision time, parameters AND input provenance identify a trial
        # event. There is no wall-clock fallback or randomly generated identity.
        return sha256(canonical_json(self.to_dict(include_id=False)).encode()).hexdigest()

    def to_dict(self, *, include_id: bool = True) -> dict:
        result = {"strategy_id": self.strategy_id, "family": self.family.value,
            "symbol": self.symbol, "side": self.side.value,
            "timestamp": utc_text(self.timestamp),
            "earliest_executable": utc_text(self.earliest_executable),
            "reasons": list(self.reasons), "status": self.status.value,
            "parameters": plain(self.parameters), "metadata": plain(self.metadata),
            "evidence": [{"source": e.source, "observed_at": utc_text(e.observed_at),
                "available_at": utc_text(e.available_at), "lineage": e.lineage,
                "reliability_tier": e.reliability_tier, "status": e.status.value}
                for e in self.evidence]}
        if include_id:
            result["hypothesis_id"] = self.hypothesis_id
        return result
