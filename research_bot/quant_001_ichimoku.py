"""Frozen research hypothesis: H4 cloud trend and H1 Kijun pullback.

This module never emits an order and makes no profitability or execution claim.
The precise research rules are frozen in docs/MASTER_QUANT_001_PROTOCOL.md.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import hashlib
import json
import math
from typing import Any

import pandas as pd

from .strategy_research_012 import _aware, validated_prefix

VERSION = "ICH_KIJUN_PULLBACK_V1"


@dataclass(frozen=True)
class DataIdentity:
    venue: str
    instrument: str
    timeframe: str
    source_id: str
    raw_sha256: str
    availability: str

    def __post_init__(self):
        if not self.venue.strip() or not self.instrument.strip() or not self.source_id.strip():
            raise ValueError("SOURCE_IDENTITY_REQUIRED")
        if self.timeframe not in {"H1", "H4"}:
            raise ValueError("INVALID_SOURCE_TIMEFRAME")
        if len(self.raw_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.raw_sha256):
            raise ValueError("INVALID_SOURCE_SHA256")
        if self.availability not in {"FIRST_SEEN_OBSERVED", "HISTORICAL_CLOSE_ASSUMED"}:
            raise ValueError("INVALID_AVAILABILITY_PROVENANCE")


@dataclass(frozen=True)
class ResearchSetup:
    setup_id: str
    version: str
    direction: str
    market_regime: dict[str, Any]
    timeframe_context: dict[str, Any]
    trigger: dict[str, Any]
    confirmation: dict[str, Any]
    entry_rule: dict[str, Any]
    invalidation: dict[str, Any]
    stop_rule: dict[str, Any]
    target_rule: dict[str, Any]
    timeout: dict[str, Any]
    cancellation_rule: tuple[str, ...]
    evidence_at: str
    source_provenance: dict[str, Any]
    execution_eligible: bool = False
    evidence_state: str = "RESEARCH_HYPOTHESIS_ONLY"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _prefix_hash(x: pd.DataFrame) -> str:
    cols = ["timestamp", "available_at", "open", "high", "low", "close", "volume"]
    values = [[v.isoformat() if isinstance(v, pd.Timestamp) else str(v) for v in row]
              for row in x[cols].itertuples(index=False, name=None)]
    return hashlib.sha256(json.dumps(values, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _midpoint(x: pd.DataFrame, window: int) -> pd.Series:
    return (x.high.rolling(window).max() + x.low.rolling(window).min()) / 2.0


def evaluate_kijun_pullback(*, h1: pd.DataFrame, h4: pd.DataFrame,
                            h1_source: DataIdentity, h4_source: DataIdentity,
                            as_of: datetime) -> ResearchSetup | None:
    """Return one causal long hypothesis, or None when its setup is absent.

    Observed-at identity is caller-supplied and cannot independently prove
    provenance. The output must not be used for execution or accounting.
    """
    if (h1_source.timeframe, h4_source.timeframe) != ("H1", "H4"):
        raise ValueError("SOURCE_TIMEFRAME_MISMATCH")
    if (h1_source.venue, h1_source.instrument) != (h4_source.venue, h4_source.instrument):
        raise ValueError("CROSS_FEED_INSTRUMENT_MISMATCH")
    now = _aware(as_of)
    one = validated_prefix(h1, timeframe="H1", as_of=now.to_pydatetime())
    four = validated_prefix(h4, timeframe="H4", as_of=now.to_pydatetime())
    one_at, four_at = _aware(one.available_at.iloc[-1]), _aware(four.available_at.iloc[-1])
    if now - one_at > timedelta(minutes=15) or now - four_at > timedelta(hours=4, minutes=15):
        raise ValueError("STALE_CONTEXT")

    h4_tenkan, h4_kijun = _midpoint(four, 9), _midpoint(four, 26)
    cloud_a = ((h4_tenkan + h4_kijun) / 2.0).shift(26)
    cloud_b = _midpoint(four, 52).shift(26)
    latest = four.iloc[-1]
    vals = [float(latest.close), float(h4_tenkan.iloc[-1]), float(h4_kijun.iloc[-1]),
            float(cloud_a.iloc[-1]), float(cloud_b.iloc[-1])]
    if not all(math.isfinite(v) for v in vals):
        raise ValueError("H4_CONTEXT_UNAVAILABLE")
    close4, tenkan4, kijun4, a4, b4 = vals
    if not (close4 > max(a4, b4) and tenkan4 > kijun4):
        return None

    kijun1 = _midpoint(one, 26)
    last, previous = one.iloc[-1], one.iloc[-2]
    k1, prev_k1 = float(kijun1.iloc[-1]), float(kijun1.iloc[-2])
    if not all(math.isfinite(v) for v in (k1, prev_k1)):
        raise ValueError("H1_CONTEXT_UNAVAILABLE")
    if not (previous.close > prev_k1 and last.low <= k1 and last.close > k1 and last.close > last.open):
        return None

    low = float(last.low)
    provenance = {"H1": {**asdict(h1_source), "prefix_sha256": _prefix_hash(one)},
                  "H4": {**asdict(h4_source), "prefix_sha256": _prefix_hash(four)}}
    body = {
        "version": VERSION,
        "direction": "LONG",
        "market_regime": {"type": "H4_ABOVE_VISIBLE_CLOUD_TENKAN_ABOVE_KIJUN", "model": "RULE_PROXY"},
        "timeframe_context": {"instrument": h1_source.instrument, "venue": h1_source.venue,
            "entry_timeframe": "H1", "trend_timeframe": "H4", "h1_available_at": one_at.isoformat(),
            "h4_available_at": four_at.isoformat()},
        "trigger": {"type": "H1_KIJUN_TOUCH_RECLAIM_BULLISH", "bar_open": last.timestamp.isoformat(),
            "bar_available_at": one_at.isoformat(), "bar_low": str(low), "kijun": str(k1),
            "previous_close": str(float(previous.close)), "previous_kijun": str(prev_k1)},
        "confirmation": {"type": "H4_CLOUD_TK", "close": str(close4), "cloud_a": str(a4),
            "cloud_b": str(b4), "tenkan": str(tenkan4), "kijun": str(kijun4)},
        "entry_rule": {"kind": "FIRST_H1_OPEN_STRICTLY_AFTER_DECISION", "max_delay_hours": 2,
            "price_condition": "ENTRY_GT_TRIGGER_LOW", "price": None},
        "invalidation": {"kind": "TRIGGER_BAR_LOW", "level": str(low)},
        "stop_rule": {"kind": "TRIGGER_BAR_LOW", "level": str(low), "gap_fill_guaranteed": False},
        "target_rule": {"kind": "ENTRY_PLUS_R_MULTIPLE", "multiple": "2", "price": None},
        "timeout": {"unit": "COMPLETED_H1_BARS_FROM_ENTRY", "count": 8},
        "cancellation_rule": ("NO_ELIGIBLE_ENTRY_WITHIN_2H", "ENTRY_AT_OR_BELOW_INVALIDATION",
            "STALE_OR_INVALID_DATA", "RISK_VETO", "UNVERIFIED_EXECUTION_POLICY"),
        "evidence_at": now.isoformat(), "source_provenance": provenance,
    }
    identity = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return ResearchSetup(setup_id=identity, **body)
