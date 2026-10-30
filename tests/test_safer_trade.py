from __future__ import annotations

from dataclasses import replace
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from research_bot.contracts import Decision,EvidenceStamp
from research_bot.safer_trade.contracts import InstrumentSpec,TargetAllocation
from research_bot.safer_trade.allocation import plan_allocation
from research_bot.safer_trade.data import closed_frame,causal_features,purged_walk_forward,join_higher_timeframe
from research_bot.safer_trade.evaluation import CostBreakdown,holm_adjust,paired_block_test,selection_diagnostics,validate_matrix
from research_bot.safer_trade.protocol import StudyProtocol,arm_readiness
from research_bot.safer_trade.registry import StudyRegistry
from research_bot.trial_registry_v17 import TrialRecord


class SaferTradeTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,1,10,tzinfo=timezone.utc)
        self.evidence=(EvidenceStamp('fixture',self.now-timedelta(hours=1),self.now),)
        self.target=TargetAllocation('d1',self.now,'baseline',{'BTC/USDT':Decimal('.20')},
            Decimal('.0025'),self.now+timedelta(minutes=5),'TEST',self.evidence)
        self.spec=InstrumentSpec('BTC/USDT','FIXTURE','SPOT',Decimal('.001'),Decimal('.01'),Decimal('5'))

    def test_weight_is_not_economic_forecast_or_permission(self):
        signal=self.target.to_signal('BTC/USDT')
        self.assertEqual(signal.decision,Decision.NO_TRADE)
        self.assertFalse(signal.metadata['execution_authorized'])
        with self.assertRaises(ValueError):replace(self.target,evidence=(EvidenceStamp('x',self.now,self.now+timedelta(seconds=1)),))

    def test_allocation_rounding_halt_and_no_short(self):
        args=dict(equity=Decimal('1000'),current_quantities={'BTC/USDT':Decimal('1')},
            prices={'BTC/USDT':Decimal('100')},instruments={'BTC/USDT':self.spec},now=self.now,account_reconciled=True)
        self.assertEqual(plan_allocation(self.target,entry_halted=True,**args),())
        close=replace(self.target,target_weights={'BTC/USDT':Decimal('0')})
        proposal=plan_allocation(close,entry_halted=True,**args)[0]
        self.assertTrue(proposal.reduce_only);self.assertEqual(proposal.quantity,Decimal('1'))
        self.assertGreater(proposal.eligible_after,self.now)
        short=replace(self.target,target_weights={'BTC/USDT':Decimal('-.1')})
        with self.assertRaises(ValueError):plan_allocation(short,entry_halted=False,**args)

    def test_causal_bars_and_future_mutation(self):
        times=pd.date_range('2026-01-01',periods=120,freq='1h',tz='UTC')
        frame=pd.DataFrame({'timestamp':times,'available_at':times+pd.Timedelta('1h'),'symbol':'BTC/USDT','venue':'FIXTURE',
            'open':100.,'high':102.,'low':99.,'close':101.,'volume':10.})
        cutoff=times[100].to_pydatetime()+timedelta(hours=1)
        before=causal_features(frame,as_of=cutoff,timeframe='1h')
        frame.loc[110,'close']=101.5
        after=causal_features(frame,as_of=cutoff,timeframe='1h')
        pd.testing.assert_frame_equal(before,after)
        frame.loc[0,'available_at']=times[0]
        with self.assertRaises(ValueError):closed_frame(frame,as_of=cutoff,timeframe='1h')

    def test_htf_only_available_at_or_before_decision(self):
        d=pd.DataFrame({'decision_time':[self.now],'venue':['V'],'symbol':['BTC']})
        h=pd.DataFrame({'timestamp':[self.now-timedelta(hours=4)+timedelta(seconds=1)],'available_at':[self.now+timedelta(seconds=1)],'venue':['V'],'symbol':['BTC'],'feature':[1.]})
        self.assertTrue(pd.isna(join_higher_timeframe(d,h).htf_feature.iloc[0]))

    def test_purge_uses_label_end_and_embargo(self):
        times=pd.date_range('2026-01-01',periods=20,freq='1h',tz='UTC')
        events=pd.DataFrame({'decision_time':times,'feature_available_at':times,'label_end':times+pd.Timedelta('2h')})
        for train,test in purged_walk_forward(events,min_train_times=10,test_times=3,embargo=pd.Timedelta('1h')):
            self.assertLess(events.label_end.iloc[train].max(),events.decision_time.iloc[test].min()-pd.Timedelta('1h'))

    def test_cost_double_count_and_holm(self):
        with self.assertRaises(ValueError):CostBreakdown(0,.001,0,0,0,True).net(.01)
        self.assertAlmostEqual(CostBreakdown(.001,0,.002,-.0005,0).net(.01),.0075)
        self.assertEqual(holm_adjust({'a':.01,'b':.04,'c':.03}),{'a':.03,'c':.06,'b':.06})

    def test_bootstrap_reproducible_and_matrix_complete(self):
        idx=pd.date_range('2024-01-01',periods=140,freq='1D',tz='UTC')
        rng=np.random.default_rng(9);b=pd.Series(rng.normal(0,.01,140),index=idx);c=b+rng.normal(.001,.005,140)
        a=paired_block_test(c,b,block=5,samples=500,seed=3)
        self.assertEqual(a,paired_block_test(c,b,block=5,samples=500,seed=3))
        self.assertEqual(a['status'],'COMPUTED')
        matrix=pd.DataFrame({'B0':b,'B1':c})
        validate_matrix(matrix,('B0','B1'))
        with self.assertRaises(ValueError):validate_matrix(matrix,('B0','B1','LOSER'))
        self.assertEqual(selection_diagnostics(matrix,selected_trial='B1')['dsr']['status'],'NOT_RUN')

    def test_registry_preserves_negative_and_blocks_rewriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry=StudyRegistry(Path(tmp)/'trials.sqlite')
            record=TrialRecord('lost','a'*40,'b'*64,'feature','logistic',{},42,'1h','rule',{},'train','valid',None,'return.csv','REJECTED')
            head=registry.register_trial(record)
            self.assertEqual(registry.register_trial(record),head)
            with self.assertRaises(ValueError):registry.register_trial(replace(record,status='HYPOTHESIS'))
            self.assertEqual(registry.trials()[0].status,'REJECTED')
            self.assertEqual(registry.verify(head)['n_events'],1)

    def test_protocol_does_not_call_old_holdout_untouched(self):
        protocol=StudyProtocol('draft','a'*40)
        self.assertEqual(protocol.readiness()['status'],'BLOCKED')
        self.assertIn('UNCONSUMED_EVALUATION_NOT_ESTABLISHED',protocol.readiness()['reasons'])
        self.assertEqual(arm_readiness('B2_PPO',baseline_economic_gate='FAIL')['status'],'BLOCKED')


if __name__=='__main__':unittest.main()
