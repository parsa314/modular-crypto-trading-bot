"""Offline four-family ablation, calibration and hypothesis-ledger integration."""
from __future__ import annotations

import argparse
from contextlib import closing
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
import pandas as pd

from research_bot.four_systems.experiment import ExperimentConfig,run_ablation
from research_bot.four_systems.hypotheses import FourSystemHypothesisGenerator
from research_bot.four_systems.risk import tail_risk_ceiling
from research_bot.contracts import EvidenceStamp
from research_bot.hypothesis import canonical_json
from research_bot.orchestrator import ResearchTradingOrchestrator
from research_bot.completion.journal import TradeJournal


def synthetic_bars(count: int = 3200) -> pd.DataFrame:
    if type(count) is not int or count<400:raise ValueError('at least 400 synthetic closed bars required')
    rng=np.random.default_rng(2026);i=np.arange(count)
    close=100*np.exp(.00002*i+.035*np.sin(i/13)+.025*np.sin(i/47)+rng.normal(0,.005,count))
    opened=np.r_[close[0],close[:-1]];spread=rng.uniform(.002,.007,count)*close
    times=pd.date_range('2025-01-01',periods=count,freq='h',tz='UTC')
    return pd.DataFrame({'timestamp':times,'available_at':times+pd.Timedelta(seconds=2),'symbol':'BTC_USDT',
        'open':opened,'high':np.maximum(opened,close)+spread,'low':np.minimum(opened,close)-spread,
        'close':close,'volume':rng.uniform(100,500,count)})


def run(journal_path: Path, frame: pd.DataFrame | None = None) -> dict:
    synthetic=frame is None
    config=ExperimentConfig(purpose='ENGINEERING_SYNTHETIC',min_train=150,min_calibration=100,test_size=80,min_class=10) if synthetic else ExperimentConfig()
    report,dataset,models=run_ablation(synthetic_bars() if synthetic else frame,config)
    report['runner_sha256']=sha256(Path(__file__).read_bytes()).hexdigest()
    journal_path.parent.mkdir(parents=True,exist_ok=True)
    experiment='four_systems:'+sha256(canonical_json([report['dataset_sha256'],report['protocol_sha256'],
        report['implementation_sha256'],report['runner_sha256']]).encode()).hexdigest()
    with closing(TradeJournal(journal_path,experiment)) as journal:
        orchestrator=ResearchTradingOrchestrator()
        emitted=[]
        for name,fold,model in models:
            if name!='ALL_FOUR':continue
            generator=FourSystemHypothesisGenerator(model)
            for index in fold.test:
                rows=dataset.iloc[[index]];at=rows.available_at.iloc[0].to_pydatetime()
                candidate=generator.generate(rows,at)[0]
                outcome=orchestrator.process_candidate(candidate)
                assert outcome.fill is None
                journal.append('four_hypothesis:'+candidate.hypothesis_id,'FOUR_SYSTEM_HYPOTHESIS',at,
                    {'candidate':candidate.to_dict(),'pipeline_status':outcome.status,'decision':outcome.signal.decision.value,
                        'execution_authorized':False,'prospective_evidence':False})
                emitted.append(candidate.hypothesis_id)
            at=dataset.available_at.iloc[fold.test[0]].to_pydatetime()
            stamp=EvidenceStamp('TRAIN_ONLY_TAIL_SCENARIOS',model.trained_at.to_pydatetime(),model.trained_at.to_pydatetime(),model.model_id)
            ceiling=tail_risk_ceiling(Decimal(10000),Decimal(10000),Decimal(0),
                tuple(Decimal(str(v)) for v in model.training_tail_returns),Decimal(str(config.cost_fraction)),stamp,at)
            journal.append('tail_ceiling:'+model.model_id,'FOUR_SYSTEM_TAIL_CEILING',at,
                {'notional_ceiling':str(ceiling.max_notional_quote),'unit_es':str(ceiling.unit_expected_shortfall),
                    'reason':ceiling.reason,'equity_interpretation':'DECLARED_10000_QUOTE_SCENARIO','execution_authorized':False})
        # Frozen report can be inserted idempotently; no wall-clock identity.
        at=dataset.available_at.iloc[-1].to_pydatetime()
        journal.append('four_system_report:'+report['protocol_sha256'],'FOUR_SYSTEM_RESEARCH_REPORT',at,report)
        report={**report,'experiment_id':experiment,'hypotheses_logged':len(emitted),
            'journal_events':len(journal.events()),'execution_authorized':False,'scope':'RESEARCH_ONLY / PAPER_OFF / LIVE_OFF'}
    return report


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv',type=Path)
    parser.add_argument('--journal',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    # Missing real data never silently becomes synthetic.
    frame=pd.read_csv(args.csv) if args.csv else None
    report=run(args.journal,frame)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:report[k] for k in ('experiment','trials','hypotheses_logged','economic_validation','live_execution')},allow_nan=False))


if __name__=='__main__':main()
