from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from hashlib import sha256
from typing import Mapping

from ..contracts import EvidenceStamp
from ..hypothesis import canonical_json
from ..jamalian_data import aware, decimal_value, available

ZERO, ONE = Decimal("0"), Decimal("1")


def signed_decimal(value: Decimal | str | int) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, str, int)):
        raise ValueError("Decimal, string or integer required")
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError("finite Decimal required")
    return result


def seconds(value: timedelta) -> Decimal:
    return Decimal(value.days * 86400 + value.seconds) + Decimal(value.microseconds) / 1_000_000


def floor_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


@dataclass(frozen=True)
class Inventory:
    balances: Mapping[str, Decimal]
    reserved: Mapping[str, Decimal]
    stamp: EvidenceStamp

    def __post_init__(self) -> None:
        balances = {k: decimal_value(v) for k, v in self.balances.items()}
        reserved = {k: decimal_value(v) for k, v in self.reserved.items()}
        if any(not k.isalnum() or k.upper() != k for k in {*balances, *reserved}):
            raise ValueError("uppercase currency codes required")
        if any(v > balances.get(k, ZERO) for k, v in reserved.items()):
            raise ValueError("reservation exceeds balance")
        # MappingProxy of Decimal values, not JSON freeze (which stringifies).
        from types import MappingProxyType
        object.__setattr__(self, "balances", MappingProxyType(balances))
        object.__setattr__(self, "reserved", MappingProxyType(reserved))

    def free(self, currency: str) -> Decimal:
        return self.balances.get(currency, ZERO) - self.reserved.get(currency, ZERO)


@dataclass(frozen=True)
class ScenarioEconomics:
    expected_return: Decimal
    expected_cost: Decimal
    risk_penalty: Decimal
    uncertainty_penalty: Decimal
    confidence: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "expected_return", signed_decimal(self.expected_return))
        for name in ("expected_cost", "risk_penalty", "uncertainty_penalty", "confidence"):
            object.__setattr__(self, name, decimal_value(getattr(self, name)))
        if self.confidence > ONE:
            raise ValueError("confidence must be in [0,1]")

    @property
    def net_edge(self) -> Decimal:
        return self.expected_return - self.expected_cost - self.risk_penalty - self.uncertainty_penalty

    def to_dict(self) -> dict[str, str]:
        return {name: str(getattr(self, name)) for name in self.__dataclass_fields__} | {
            "net_edge": str(self.net_edge), "interpretation": "MODEL_SCENARIO_NOT_CALIBRATED_FORECAST"}

    @classmethod
    def from_metadata(cls, metadata: Mapping) -> ScenarioEconomics | None:
        values = metadata.get("economics")
        if not isinstance(values, Mapping):
            return None
        return cls(**{name: values[name] for name in cls.__dataclass_fields__})


@dataclass(frozen=True)
class CompletionPolicy:
    research_only: bool = True
    paper_execution: bool = True
    live_execution: bool = False
    mode: str = "PAPER"
    purpose: str = "SYNTHETIC_ENGINEERING"
    fee_rate: Decimal = Decimal("0.001")  # Paid in received currency.
    slippage_rate: Decimal = Decimal("0.0002")
    latency_ms: int = 10
    ack_latency_ms: int = 10
    cancel_latency_ms: int = 10
    jitter_ms: int = 0
    seed: int = 314
    max_order_notional: Decimal = Decimal("5000")
    max_book_age: timedelta = timedelta(seconds=30)

    def __post_init__(self) -> None:
        if self.research_only is not True or self.paper_execution is not True or self.live_execution is not False:
            raise ValueError("RESEARCH_ONLY / PAPER_EXECUTION / LIVE_OFF required")
        if self.mode not in {"PAPER", "BACKTEST"} or self.purpose not in {"SYNTHETIC_ENGINEERING", "GRADUATED_RESEARCH"}:
            raise ValueError("only offline BACKTEST/PAPER purposes are supported")
        for name in ("fee_rate", "slippage_rate", "max_order_notional"):
            object.__setattr__(self, name, decimal_value(getattr(self, name), positive=name == "max_order_notional"))
        if self.fee_rate >= ONE or self.slippage_rate >= ONE or self.max_book_age <= timedelta(0):
            raise ValueError("invalid simulator rates or age")
        for name in ("latency_ms", "ack_latency_ms", "cancel_latency_ms", "jitter_ms", "seed"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError("nonnegative integer time/seed required")

    def to_dict(self) -> dict:
        return {k: str(v) if isinstance(v, Decimal) else seconds(v).__str__()
                if isinstance(v, timedelta) else v for k, v in self.__dict__.items()}

    def execution_model_sha256(self) -> str:
        values = {k: v for k, v in self.to_dict().items() if k not in {"mode", "purpose"}}
        return sha256(canonical_json(values).encode()).hexdigest()


@dataclass(frozen=True)
class MarketRules:
    quantity_step: Decimal = Decimal("0.00000001")
    price_tick: Decimal = Decimal("0.01")
    min_notional: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            object.__setattr__(self, name, decimal_value(getattr(self, name), positive=name != "min_notional"))


def check_time(stamp: EvidenceStamp, at: datetime, age: timedelta) -> None:
    aware(at)
    available(stamp, at, age)
