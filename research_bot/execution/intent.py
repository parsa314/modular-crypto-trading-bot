"""Model-independent, authenticated order contract; no research imports."""
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import re

from .constitution import assert_risk_hash


def digest(value):
    def normalize(item):
        if isinstance(item, datetime):
            return utc(item).isoformat()
        if isinstance(item, dict):
            return {k: normalize(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalize(v) for v in item]
        return item
    return hashlib.sha256(json.dumps(normalize(value), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('TIMEZONE_AWARE_TIMESTAMP_REQUIRED')
    return value.astimezone(timezone.utc)


def text(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError('CANONICAL_IDENTIFIER_REQUIRED')
    return value


def sha(value, size=64):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{'+str(size)+'}', value) or len(set(value)) == 1:
        raise ValueError('INVALID_PROVENANCE_HASH')
    return value


@dataclass(frozen=True)
class FrozenModelManifest:
    """Artifact identity; no constructor value authorizes private execution."""
    model_id: str
    model_version: str
    model_sha256: str
    scaler_sha256: str
    feature_schema_sha256: str
    training_dataset_hash: str
    validation_protocol_hash: str
    git_commit: str
    strategy_version: str
    approved_symbols: tuple[str, ...]
    approved_timeframes: tuple[str, ...]
    approved_venues: tuple[str, ...]
    risk_constitution_hash: str
    promotion_evidence_hash: str
    created_at: datetime
    promotion_decision: str = 'NOT_PROMOTED'
    purpose: str = 'ENGINEERING_REPLAY'

    def __post_init__(self):
        for name in ('model_id', 'model_version', 'strategy_version'):
            text(getattr(self, name))
        for name in ('model_sha256', 'scaler_sha256', 'feature_schema_sha256',
                     'training_dataset_hash', 'validation_protocol_hash'):
            sha(getattr(self, name))
        sha(self.git_commit, 40)
        sha(self.risk_constitution_hash)
        sha(self.promotion_evidence_hash)
        object.__setattr__(self, 'created_at', utc(self.created_at))
        for name in ('approved_symbols', 'approved_timeframes', 'approved_venues'):
            values = getattr(self, name)
            if not isinstance(values, tuple) or not values or len(set(values)) != len(values):
                raise ValueError('IMMUTABLE_UNIQUE_MANIFEST_SCOPE_REQUIRED')
            for value in values:
                text(value)
        if self.purpose not in {'ENGINEERING_REPLAY', 'PRODUCTION'} or self.promotion_decision not in {'NOT_PROMOTED', 'PROMOTED', 'REJECTED', 'REVOKED'}:
            raise ValueError('INVALID_PROMOTION_STATE')

    @property
    def sha256(self):
        return digest(asdict(self))

    @classmethod
    def from_json(cls, payload):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('DUPLICATE_MANIFEST_KEY')
                result[key] = value
            return result
        def reject(value):
            raise ValueError('NONFINITE_MANIFEST_VALUE')
        data = json.loads(payload, object_pairs_hook=unique, parse_constant=reject)
        if not isinstance(data, dict) or set(data)-{f.name for f in fields(cls)}:
            raise ValueError('UNKNOWN_MANIFEST_KEY')
        for name in ('approved_symbols', 'approved_timeframes', 'approved_venues'):
            if not isinstance(data.get(name), list):
                raise ValueError('INVALID_MANIFEST_SCOPE')
            data[name] = tuple(data[name])
        data['created_at'] = datetime.fromisoformat(data['created_at'])
        return cls(**data)


@dataclass(frozen=True)
class ApprovedOrderIntent:
    account_id: str
    event_id: str
    symbol: str
    venue: str
    timeframe: str
    strategy_id: str
    model_id: str
    decision_hash: str
    risk_hash: str
    manifest_hash: str
    decision_at: datetime
    valid_from: datetime
    valid_until: datetime
    approved_notional: float
    limit_price: float
    stop: float
    target: float
    round_trip_cost_bps: float
    portfolio_decision_id: str
    feature_snapshot_id: str
    created_at: datetime
    side: str = 'buy'
    entry_type: str = 'LIMIT'
    time_in_force: str = 'IOC'
    mode: str = 'ENGINEERING_REPLAY'

    def __post_init__(self):
        for name in ('account_id', 'event_id', 'symbol', 'venue', 'timeframe', 'strategy_id', 'model_id'):
            text(getattr(self, name))
        for name in ('decision_hash', 'manifest_hash', 'portfolio_decision_id', 'feature_snapshot_id', 'risk_hash'):
            sha(getattr(self, name))
        if self.symbol.count('/') != 1 or ':' in self.symbol or self.symbol.split('/')[1] != 'USDT':
            raise ValueError('UNLEVERAGED_USDT_SPOT_REQUIRED')
        for name in ('decision_at', 'valid_from', 'valid_until', 'created_at'):
            object.__setattr__(self, name, utc(getattr(self, name)))
        if not self.decision_at < self.valid_from < self.valid_until:
            raise ValueError('INVALID_INTENT_TIME_WINDOW')
        if not self.valid_from <= self.created_at < self.valid_until:
            raise ValueError('INTENT_CREATION_OUTSIDE_WINDOW')
        for name in ('approved_notional', 'limit_price', 'stop', 'target', 'round_trip_cost_bps'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError('NONFINITE_ORDER_VALUE')
            if value < 0 or (name != 'round_trip_cost_bps' and value == 0):
                raise ValueError('INVALID_ORDER_VALUE')
        if not self.stop < self.limit_price < self.target or self.round_trip_cost_bps >= 10_000:
            raise ValueError('INVALID_PROTECTIVE_BARRIERS_OR_COST')
        if (self.side, self.entry_type, self.time_in_force, self.mode) != ('buy', 'LIMIT', 'IOC', 'ENGINEERING_REPLAY'):
            raise ValueError('PRIVATE_OR_SHORT_EXECUTION_NOT_AUTHORIZED')

    def payload(self):
        data = asdict(self)
        for name in ('decision_at', 'valid_from', 'valid_until', 'created_at'):
            data[name] = data[name].isoformat()
        return data

    @property
    def sha256(self):
        return digest(self.payload())

    @property
    def client_order_id(self):
        # Account/event identity, independent of changed size or prices. A
        # modified replay cannot bypass a previous reservation after restart.
        return 'C' + digest({'account': self.account_id, 'event': self.event_id})[:30]

    @property
    def intent_id(self):
        return digest({'event_id': self.event_id, 'portfolio_decision_id': self.portfolio_decision_id,
                       'venue': self.venue, 'symbol': self.symbol, 'side': self.side, 'version': 'INTENT_V1'})


@dataclass(frozen=True)
class SealedIntent:
    intent: ApprovedOrderIntent
    mac: str


class IntentAuthority:
    """Trusted L0 signer; its key is never an input to models or ledger JSON."""
    def __init__(self, key: bytes):
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError('AUTHORITY_KEY_TOO_SHORT')
        self._key = key

    def seal(self, intent: ApprovedOrderIntent):
        return SealedIntent(intent, hmac.new(self._key, intent.sha256.encode(), hashlib.sha256).hexdigest())

    def verify(self, sealed: SealedIntent):
        if not isinstance(sealed, SealedIntent) or not isinstance(sealed.intent, ApprovedOrderIntent):
            raise ValueError('SEALED_INTENT_REQUIRED')
        mac = hmac.new(self._key, sealed.intent.sha256.encode(), hashlib.sha256).hexdigest()
        if not isinstance(sealed.mac, str) or not hmac.compare_digest(mac, sealed.mac):
            raise ValueError('INTENT_AUTHENTICATION_FAILED')
        return sealed.intent
