"""Single-asset CVaR/exposure size ceiling; no LP portfolio or order authorization."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Sequence

from ..contracts import EvidenceStamp
from ..jamalian_data import available, decimal_value
from ..completion.models import signed_decimal


@dataclass(frozen=True)
class TailRiskConfig:
    max_position_weight: Decimal = Decimal('.10')
    cvar_budget: Decimal = Decimal('.035')
    alpha: Decimal = Decimal('.95')
    min_scenarios: int = 100

    def __post_init__(self) -> None:
        for name in ('max_position_weight','cvar_budget','alpha'):
            object.__setattr__(self,name,decimal_value(getattr(self,name),positive=True))
        if self.max_position_weight>Decimal('.10') or self.cvar_budget>Decimal('.035') or not Decimal('.95')<=self.alpha<1:
            raise ValueError('risk ceilings cannot be weakened')
        if type(self.min_scenarios) is not int or self.min_scenarios<100:
            raise ValueError('at least 100 trailing tail scenarios required')


@dataclass(frozen=True)
class TailRiskCeiling:
    max_notional_quote: Decimal
    unit_expected_shortfall: Decimal | None
    reason: str
    execution_authorized: bool = False


def tail_risk_ceiling(equity: Decimal, free_quote: Decimal, existing_asset_value: Decimal,
                      scenario_returns: Sequence[Decimal], cost_fraction: Decimal, stamp: EvidenceStamp,
                      at: datetime, config: TailRiskConfig = TailRiskConfig()) -> TailRiskCeiling:
    equity=decimal_value(equity,positive=True);cash=decimal_value(free_quote);held=decimal_value(existing_asset_value)
    cost=decimal_value(cost_fraction);available(stamp,at,timedelta(days=1))
    scenarios=tuple(signed_decimal(v) for v in scenario_returns)
    if len(scenarios)<config.min_scenarios:
        return TailRiskCeiling(Decimal(0),None,'INSUFFICIENT_TAIL_SCENARIOS')
    if any(v<Decimal(-1) for v in scenarios):raise ValueError('spot scenario return cannot be below -100%')
    # Exact empirical upper-tail mean with fractional mass at the quantile.
    losses=sorted((cost-v for v in scenarios),reverse=True)
    mass=(1-config.alpha)*len(losses)
    full=int(mass);fraction=mass-full
    total=sum(losses[:full],Decimal(0))+(losses[full]*fraction if fraction else Decimal(0))
    es=max(Decimal(0),total/mass)
    exposure_cap=max(Decimal(0),config.max_position_weight*equity-held)
    tail_cap=max(Decimal(0),config.cvar_budget*equity/es-held) if es else exposure_cap
    maximum=max(Decimal(0),min(cash,exposure_cap,tail_cap))
    return TailRiskCeiling(maximum,es,'SIZE_CEILING_ONLY' if maximum else 'NO_RISK_CAPACITY')
