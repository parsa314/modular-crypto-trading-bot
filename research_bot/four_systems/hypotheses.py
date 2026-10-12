"""Pure calibrated-context hypotheses; canonical orchestrator still abstains."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import pandas as pd

from ..contracts import EvidenceStamp, ResearchStatus
from ..hypothesis import HypothesisCandidate, HypothesisFamily, HypothesisSide, canonical_json
from .experiment import CalibratedFoldModel


class FourSystemHypothesisGenerator:
    def __init__(self, model: CalibratedFoldModel, probability_threshold: Decimal = Decimal('.58')) -> None:
        if not isinstance(probability_threshold,Decimal) or not probability_threshold.is_finite() or not Decimal('.58')<=probability_threshold<=1:
            raise ValueError('threshold may not weaken canonical confidence gate')
        self.model,self.threshold=model,probability_threshold

    def generate(self, rows: pd.DataFrame, at: datetime) -> list[HypothesisCandidate]:
        if len(rows)!=1 or at.tzinfo is None or rows.available_at.iloc[0].to_pydatetime()!=at:
            raise ValueError('one current available closed-bar snapshot required')
        raw,probability,support=self.model.predict(rows)
        row=rows.iloc[0];p=Decimal(str(probability[0]));gross=p*Decimal(str(self.model.positive_mean))+(1-p)*Decimal(str(self.model.negative_mean))
        cost=Decimal(str(self.model.config.cost_fraction))
        # OOS calibration metrics and uncertainty are recorded separately. A
        # probability is never a calibrated monetary forecast/graduation grant.
        fire=bool(row.setup) and bool(support[0]) and p>=self.threshold and gross>cost
        inputs={name:str(row[name]) for name in self.model.columns}
        lineage=sha256(canonical_json([str(row.timestamp),str(row.available_at),inputs]).encode()).hexdigest()
        source=EvidenceStamp('CLOSED_OHLCV_FOUR_SYSTEMS_CONTEXT',row.timestamp.to_pydatetime(),at,
            lineage,'HISTORICAL_CONTEXT',ResearchStatus.HYPOTHESIS)
        fitted=EvidenceStamp('MODEL_CALIBRATION_COMPLETED',self.model.trained_at.to_pydatetime(),
            self.model.trained_at.to_pydatetime(),self.model.model_id,'OOS_UNGRADUATED',ResearchStatus.HYPOTHESIS)
        h=HypothesisCandidate('FOUR_SYSTEMS_CALIBRATED_CONTEXT_V1',HypothesisFamily.TECHNICAL_BASELINE,
            row.symbol,HypothesisSide.LONG if fire else HypothesisSide.NEUTRAL,at,at+timedelta(milliseconds=20),
            ('CALIBRATED_RESEARCH_SETUP' if fire else 'PROBABILITY_COST_SUPPORT_OR_SETUP_ABSTAIN',),
            (source,fitted),{'model_id':self.model.model_id,'threshold':str(self.threshold)},
            {'probability':str(p),'raw_probability':str(raw[0]),'in_calibration_support':bool(support[0]),
                'conditional_gross_return_proxy':str(gross),'cost_hurdle':str(cost),'features':inputs,
                'research_status':'HYPOTHESIS','calibration_status':'HISTORICAL_CONTEXT_NOT_GRADUATED',
                'prospective_evidence':False,'execution_authorized':False,'eligible_for_v51':False})
        return [h]
