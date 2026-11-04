"""Research-only contracts for existing ICT, SMC and Brooks baseline signals.

This adapter does not alter the original detectors, evaluate economic value,
create orders, infer probabilities or establish venue/broker provenance. The
caller must supply a previously specified exit policy; no policy is optimized
or silently selected here. BTC/USDT spot is long-only in this prototype.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from typing import Any

import pandas as pd

from .strategies import ict_strategy, smc_strategy, al_brooks_strategy
from .strategy_research_012 import _aware, validated_prefix

_DETECTORS = {"ICT": ict_strategy, "SMC": smc_strategy, "AL_BROOKS": al_brooks_strategy}
_BLOBS = {"ICT":"50a02bc82b4230de98cd4ae4d44a47294ea453b7",
          "SMC":"03047417a3da3ddfb7d9b3e063f27dfca913f672",
          "AL_BROOKS":"e22647dca9efb31f186f3fd913bd9702c4352e22"}
_DURATIONS = {"M15":timedelta(minutes=15), "H1":timedelta(hours=1), "H4":timedelta(hours=4)}


def _positive(value: Any) -> Decimal:
    if isinstance(value, (float,bool)):
        raise ValueError("EXACT_POSITIVE_DECIMAL_REQUIRED")
    try:
        number = Decimal(str(value))
    except (InvalidOperation,ValueError) as exc:
        raise ValueError("EXACT_POSITIVE_DECIMAL_REQUIRED") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError("EXACT_POSITIVE_DECIMAL_REQUIRED")
    return number


@dataclass(frozen=True)
class ResearchExitPolicy:
    """Caller-declared policy; provenance/freeze claim is not authenticated."""
    policy_id: str
    frozen_at: datetime
    target_r: Decimal
    entry_deadline_bars: int
    exit_timeout_bars: int

    def __post_init__(self):
        if not self.policy_id.strip():
            raise ValueError("POLICY_ID_REQUIRED")
        _aware(self.frozen_at)
        _positive(self.target_r)
        for value in (self.entry_deadline_bars,self.exit_timeout_bars):
            if isinstance(value,bool) or not isinstance(value,int) or value <= 0:
                raise ValueError("POSITIVE_POLICY_BAR_COUNT_REQUIRED")


@dataclass(frozen=True)
class SourceIdentity:
    venue: str
    instrument: str
    market_type: str
    timeframe: str
    source_id: str
    raw_sha256: str
    availability: str

    def __post_init__(self):
        if not all((self.venue.strip(),self.instrument.strip(),self.source_id.strip())):
            raise ValueError("SOURCE_IDENTITY_REQUIRED")
        if self.market_type != "SPOT":
            raise ValueError("ONLY_SPOT_RESEARCH_SUPPORTED")
        if self.timeframe not in _DURATIONS:
            raise ValueError("UNSUPPORTED_TIMEFRAME")
        if len(self.raw_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.raw_sha256):
            raise ValueError("INVALID_SOURCE_SHA256")
        if self.availability not in {"FIRST_SEEN_OBSERVED","HISTORICAL_CLOSE_ASSUMED"}:
            raise ValueError("AVAILABILITY_PROVENANCE_REQUIRED")


@dataclass(frozen=True)
class ResearchDecision:
    status: str
    reason: str
    candidate: dict[str,Any] | None


def _prefix_hash(frame: pd.DataFrame) -> str:
    columns=["timestamp","available_at","open","high","low","close","volume"]
    values=[[v.isoformat() if isinstance(v,pd.Timestamp) else str(v) for v in row]
            for row in frame[columns].itertuples(index=False,name=None)]
    return hashlib.sha256(json.dumps(values,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()


def evaluate_baseline_contract(*, style: str, frame: pd.DataFrame, as_of: datetime,
                               source: SourceIdentity, policy: ResearchExitPolicy | None) -> ResearchDecision:
    """Convert an unmodified long baseline hit to an opt-in research contract.

    Latest completed bar must be fresh (<=15min availability delay). Existing
    ICT/SMC descriptions are OHLC proxies; no institutional order flow is implied.
    Missing data, absent policy, short signals and bad stop geometry fail closed.
    """
    if style not in _DETECTORS:
        raise ValueError("UNKNOWN_BASELINE_STYLE")
    now=_aware(as_of)
    if policy is None:
        return ResearchDecision("NO_TRADE","RESEARCH_POLICY_UNAVAILABLE",None)
    if _aware(policy.frozen_at) > now:
        return ResearchDecision("NO_TRADE","POLICY_NOT_YET_AVAILABLE",None)
    try:
        # Drop bars that cannot yet have closed before parsing their other
        # fields: a malformed future bar must not change a past decision.
        timestamps=frame["timestamp"].map(_aware)
        eligible=frame.loc[timestamps+_DURATIONS[source.timeframe] <= now]
        x=validated_prefix(eligible,timeframe=source.timeframe,as_of=now.to_pydatetime())
    except (ValueError,TypeError,KeyError):
        return ResearchDecision("NO_TRADE","DATA_UNAVAILABLE_OR_INVALID",None)
    available=_aware(x.available_at.iloc[-1])
    prefix_available=_aware(x.available_at.max())
    candidate_available=max(prefix_available,_aware(policy.frozen_at))
    if now-available > timedelta(minutes=15):
        return ResearchDecision("NO_TRADE","STALE_SOURCE",None)
    signal=_DETECTORS[style].evaluate(x,as_of=now.to_pydatetime())
    if signal.style != style or _aware(signal.decision_at) != available:
        return ResearchDecision("NO_TRADE","INVALID_SIGNAL_IDENTITY_OR_TIME",None)
    if signal.action == "HOLD":
        return ResearchDecision("NO_TRADE","BASELINE_NO_SIGNAL",None)
    if signal.action != "BUY":
        return ResearchDecision("NO_TRADE","SHORT_NOT_SUPPORTED_SPOT",None)
    last=x.iloc[-1]
    if style == "ICT":
        age=signal.evidence.get("sweep_bars_ago")
        if age is None or not math.isfinite(age) or int(age) != age or not 1 <= int(age) <= 8:
            return ResearchDecision("NO_TRADE","SWEEP_EVIDENCE_INVALID",None)
        reference=x.iloc[len(x)-1-int(age)]
        stop=float(reference.low)
        stop_source="CONFIRMED_PAST_SWEEP_LOW_PROXY"
        reference_at=reference.available_at.isoformat()
    elif style == "SMC":
        stop=signal.evidence.get("ob_low")
        if stop is None or not math.isfinite(stop):
            return ResearchDecision("NO_TRADE","ORDER_BLOCK_PROXY_UNAVAILABLE",None)
        stop=float(stop)
        stop_source="PRIOR_OPPOSING_CANDLE_LOW_PROXY"
        reference_at=prefix_available.isoformat()
    else:
        stop=float(last.low)
        stop_source="SIGNAL_BAR_LOW_PROXY"
        reference_at=available.isoformat()
    if not math.isfinite(stop) or stop <= 0 or stop >= float(last.close):
        return ResearchDecision("NO_TRADE","INVALID_STOP_GEOMETRY",None)

    policy_body=asdict(policy)
    policy_body["frozen_at"]=_aware(policy.frozen_at).isoformat()
    policy_body["target_r"]=str(_positive(policy.target_r))
    entry_expiry=now+policy.entry_deadline_bars*_DURATIONS[source.timeframe]
    baseline_evidence=dict(signal.evidence)
    body={"version":"BASELINE_TRADE_CONTRACT_V1","symbol":source.instrument,"style":style,"direction":"LONG",
        "timeframe":source.timeframe,"decision_at":now.isoformat(),"available_at":candidate_available.isoformat(),
        "setup_type":"BASELINE_"+style+"_LONG",
        "features":{"baseline_evidence":baseline_evidence,"baseline_score_ordinal":signal.score,
                    "available_at":prefix_available.isoformat(),
                    "score_is_probability":False},
        "expiry":entry_expiry.isoformat(),
        "strategy_blob_sha":_BLOBS[style],"evidence_at":now.isoformat(),
        "timeframe_context":{"timeframe":source.timeframe,"bar_open":last.timestamp.isoformat(),
                             "bar_available_at":available.isoformat(),"market_regime":"UNCLASSIFIED"},
        "trigger":{"baseline_reasons":list(signal.reasons),"baseline_score_ordinal":signal.score,
                   "score_is_probability":False,"baseline_evidence":baseline_evidence},
        "confirmation":{"type":"BASELINE_DETECTOR_ONLY","status":"PROXY_UNVALIDATED"},
        "entry_rule":{"kind":"FIRST_BAR_OPEN_STRICTLY_AFTER_DECISION","max_delay_bars":policy.entry_deadline_bars,"price":None},
        "invalidation":{"level":str(stop),"source":stop_source,"source_available_at":reference_at},
        "stop_rule":{"kind":"STRUCTURAL_PROXY_LEVEL","level":str(stop),"gap_fill_guaranteed":False},
        "target_rule":{"kind":"ENTRY_PLUS_R_MULTIPLE","multiple":str(policy.target_r),"price":None},
        "timeout":{"unit":"COMPLETED_BARS_FROM_ENTRY","count":policy.exit_timeout_bars},
        "cancellation_rule":["ENTRY_NOT_STRICTLY_AFTER_DECISION","ENTRY_AT_OR_BELOW_INVALIDATION",
                             "ENTRY_DEADLINE_ELAPSED","DATA_INVALID_OR_STALE","RISK_VETO","UNVERIFIED_EXECUTION_POLICY"],
        "source_provenance":{**asdict(source),"prefix_sha256":_prefix_hash(x),"independent_authentication":False},
        "exit_policy":policy_body,"policy_freeze_independently_verified":False,
        "execution_eligible":False,"evidence_state":"RESEARCH_HYPOTHESIS_ONLY"}
    encoded=json.dumps(body,sort_keys=True,separators=(",",":"),allow_nan=False)
    event_id=hashlib.sha256(encoded.encode()).hexdigest()
    candidate={"setup_id":event_id,"evidence_id":event_id,**body}
    return ResearchDecision("CANDIDATE","BASELINE_HIT_RESEARCH_ONLY",candidate)
