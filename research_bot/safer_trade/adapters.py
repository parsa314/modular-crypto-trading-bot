"""Offline interfaces to external frameworks; no broker or network access."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import importlib.metadata
from typing import Any, Mapping

import pandas as pd

from research_bot.contracts import EvidenceStamp
from .contracts import TargetAllocation


def finrl_weights(result: Any, *, symbol_mapping: Mapping[str,str], decision_id: str,
                  as_of: datetime, valid_until: datetime, evidence: tuple[EvidenceStamp,...],
                  risk_budget: Decimal=Decimal('.0025')) -> TargetAllocation:
    """Consume the pinned FinRL-X StrategyResult.weights table, never its broker."""
    weights=getattr(result,'weights',None)
    name=getattr(result,'strategy_name',None)
    if not isinstance(weights,pd.DataFrame) or not {'gvkey','weight'}.issubset(weights.columns) or not name:
        raise ValueError("invalid FinRL-X StrategyResult")
    if weights.gvkey.duplicated().any():raise ValueError("duplicate asset weights")
    mapped={}
    for row in weights.itertuples(index=False):
        key=str(getattr(row,'gvkey'))
        if key not in symbol_mapping:raise ValueError("unmapped FinRL-X instrument")
        symbol=symbol_mapping[key]
        if symbol in mapped:raise ValueError("multiple source IDs map to same asset")
        mapped[symbol]=Decimal(str(getattr(row,'weight')))
    return TargetAllocation(decision_id,as_of,str(name),mapped,risk_budget,valid_until,
                            'FINRL_WEIGHT_PROPOSAL',evidence)


def cached_agent_signal(*, rating: str, published_at: datetime, available_at: datetime,
                        as_of: datetime, source: str, cost_usd: Decimal) -> dict[str,Any]:
    """An agent self-rating is a category; it is not a calibrated probability."""
    stamp=EvidenceStamp(source,published_at,available_at)
    if not stamp.is_available(as_of):raise ValueError("future agent evidence")
    if rating not in {'BUY','OVERWEIGHT','HOLD','UNDERWEIGHT','SELL','REVIEW'}:
        raise ValueError("invalid agent rating")
    if not cost_usd.is_finite() or cost_usd<0:raise ValueError("invalid inference cost")
    return {'rating':rating,'source':source,'available_at':available_at.isoformat(),
            'cost_usd':str(cost_usd),'calibrated_probability':None,'execution_authorized':False}


def nautilus_availability() -> dict[str,str]:
    try:version=importlib.metadata.version('nautilus_trader')
    except importlib.metadata.PackageNotFoundError:
        return {'status':'BLOCKED','reason':'NAUTILUS_DEPENDENCY_UNAVAILABLE'}
    if version!='1.231.0':return {'status':'BLOCKED','reason':'UNVALIDATED_NAUTILUS_API_VERSION','version':version}
    return {'status':'AVAILABLE_NOT_YET_INTEGRATED','version':version}
