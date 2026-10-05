"""Operator-owned L0 risk constants shared by research and execution.

This immutable configuration is not a Python security sandbox. Model outputs
are untrusted data and cannot supply configuration to the runtime.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from types import MappingProxyType


_V1 = MappingProxyType({
    'version': 'RISK_CONSTITUTION_V1',
    'risk_per_trade': .0025,
    'max_asset_weight': .35,
    'max_gross_exposure': .70,
    'drawdown_kill': .05,
    'drawdown_warning': .03,
    'max_cvar95': .035,
    'min_cash_buffer': .05,
    'max_turnover_per_step': .70,
    'max_daily_loss': .02,
})


@dataclass(frozen=True)
class RiskConstitution:
    version: str = _V1['version']
    risk_per_trade: float = _V1['risk_per_trade']
    max_asset_weight: float = _V1['max_asset_weight']
    max_gross_exposure: float = _V1['max_gross_exposure']
    drawdown_kill: float = _V1['drawdown_kill']
    drawdown_warning: float = _V1['drawdown_warning']
    max_cvar95: float = _V1['max_cvar95']
    min_cash_buffer: float = _V1['min_cash_buffer']
    max_turnover_per_step: float = _V1['max_turnover_per_step']
    max_daily_loss: float = _V1['max_daily_loss']

    def __post_init__(self):
        values = asdict(self)
        if self.version != _V1['version']:
            raise ValueError('UNKNOWN_RISK_VERSION')
        for name, value in values.items():
            if name == 'version':
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value < 1:
                raise ValueError('INVALID_RISK_FRACTION')
            if (value < _V1[name] if name == 'min_cash_buffer' else value > _V1[name]):
                raise ValueError('RISK_CONSTITUTION_RELAXATION_FORBIDDEN')
            object.__setattr__(self, name, float(value))
        if self.drawdown_warning >= self.drawdown_kill:
            raise ValueError('WARNING_MUST_PRECEDE_KILL')

    def canonical_json(self):
        return json.dumps(asdict(self), sort_keys=True, separators=(',', ':'), allow_nan=False)

    @property
    def sha256(self):
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()

    @classmethod
    def from_json(cls, value):
        def unique(pairs):
            out = {}
            for key, item in pairs:
                if key in out:
                    raise ValueError('DUPLICATE_RISK_KEY')
                out[key] = item
            return out
        def nonfinite(value):
            raise ValueError('NONFINITE_RISK_VALUE')
        data = json.loads(value, object_pairs_hook=unique, parse_constant=nonfinite)
        if not isinstance(data, dict) or set(data)-set(_V1):
            raise ValueError('UNKNOWN_RISK_KEY')
        return cls(**data)


RISK_CONSTITUTION_V1 = RiskConstitution()


def assert_risk_hash(value, policy=RISK_CONSTITUTION_V1):
    if value != policy.sha256:
        raise ValueError('RISK_CONSTITUTION_HASH_MISMATCH')


def assert_risk_bindings(*, drawdown, daily_loss=None, asset_weight=None, cvar=None):
    expected = {'drawdown': RISK_CONSTITUTION_V1.drawdown_kill,
                'daily_loss': RISK_CONSTITUTION_V1.max_daily_loss,
                'asset_weight': RISK_CONSTITUTION_V1.max_asset_weight,
                'cvar': RISK_CONSTITUTION_V1.max_cvar95}
    observed = {'drawdown': drawdown, 'daily_loss': daily_loss, 'asset_weight': asset_weight, 'cvar': cvar}
    for key, value in observed.items():
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not math.isfinite(value) or not 0 < value <= expected[key]):
            raise ValueError('RISK_CONSTITUTION_RELAXATION_FORBIDDEN')
