from __future__ import annotations

from contextlib import closing
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from research_bot.contracts import Decision, EvidenceStamp
from research_bot.completion.journal import TradeJournal
from research_bot.four_systems.features import (
    FeatureConfig, build_features, feature_families, validate_bars, _one_timeframe,
)
from research_bot.four_systems.experiment import (
    CalibratedFoldModel, ExperimentConfig, Fold, calibration_metrics,
    chronological_folds, prepare_dataset, run_ablation,
)
from research_bot.four_systems.hypotheses import FourSystemHypothesisGenerator
from research_bot.four_systems.risk import TailRiskConfig, tail_risk_ceiling
from research_bot.orchestrator import ResearchTradingOrchestrator
from scripts.run_four_systems_research import run, synthetic_bars


class FeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = synthetic_bars(800)
        cls.features = build_features(cls.bars)

    def test_future_changes_leave_every_past_feature_unchanged(self):
        changed = self.bars.copy()
        changed.loc[501:, ['open','high','low','close']] *= 3
        changed.loc[501:, 'volume'] *= 4
        result = build_features(changed)
        pd.testing.assert_frame_equal(self.features.iloc[:501], result.iloc[:501])
        self.assertNotEqual(self.features.attrs['dataset_sha256'], result.attrs['dataset_sha256'])

    def test_prefix_and_incomplete_higher_bar_are_causal(self):
        prefix = build_features(self.bars.iloc[:502])
        pd.testing.assert_frame_equal(self.features.iloc[:502], prefix)
        # A 04:00 higher-timeframe close needs closes at 01,02,03,04.
        self.assertTrue(pd.isna(self.features.htf_closed_at.iloc[3]))
        self.assertEqual(self.features.htf_closed_at.iloc[4], self.bars.timestamp.iloc[4])
        self.assertTrue((self.features.htf_available_at.dropna() <= self.features.available_at.loc[self.features.htf_available_at.notna()]).all())
        self.assertEqual(self.features.htf_closed_at.iloc[7], self.bars.timestamp.iloc[4])

    def test_cloud_and_causal_lag_match_trailing_definitions(self):
        x = self.bars; i = 120
        tenkan = (x.high.rolling(9).max()+x.low.rolling(9).min())/2
        kijun = (x.high.rolling(26).max()+x.low.rolling(26).min())/2
        b = (x.high.rolling(52).max()+x.low.rolling(52).min())/2
        expected = (x.close.iloc[i]-max(((tenkan+kijun)/2).iloc[i-26],b.iloc[i-26]))/x.close.iloc[i]
        self.assertAlmostEqual(self.features.ichi_visible_distance.iloc[i], expected)
        self.assertAlmostEqual(self.features.ichi_causal_lag.iloc[i], x.close.iloc[i]/x.close.iloc[i-26]-1)
        self.assertEqual(sum(map(len, feature_families().values())),38)

    def test_timestamp_resolution_does_not_change_alignment_or_features(self):
        microseconds=self.bars.copy()
        for column in ('timestamp','available_at'):
            microseconds[column]=microseconds[column].dt.as_unit('us')
        result=build_features(microseconds)
        columns=list(sum(feature_families().values(),()))
        pd.testing.assert_frame_equal(self.features[columns],result[columns])

    def test_swing_is_not_backdated_and_patterns_are_operational(self):
        for stop in (9,12,18,21):
            expected = _one_timeframe(self.bars.iloc[:stop], FeatureConfig())
            actual = _one_timeframe(self.bars.iloc[:30], FeatureConfig()).iloc[:stop]
            pd.testing.assert_frame_equal(expected, actual)
        f = self.features
        self.assertTrue(f.smc_bos.isin([-1,0,1]).all())
        self.assertTrue(f.brooks_second_entry.isin([-1,0,1]).all())
        self.assertTrue((f.ict_fvg != 0).any())
        self.assertTrue((f.smc_ob_side != 0).any())
        self.assertTrue((f.brooks_second_entry != 0).any())

    def test_invalid_source_is_rejected_instead_of_repaired(self):
        invalid=[]
        for col,value in [('close',np.nan),('volume',-1),('high',.01),('open',True),('symbol','OTHER_USDT')]:
            x=self.bars.copy();x[col]=x[col].astype(object);x.loc[10,col]=value;invalid.append(x)
        invalid.extend([self.bars.drop(index=10),self.bars.iloc[::-1],self.bars.drop(columns='available_at'),self.bars.iloc[:0]])
        x=self.bars.copy();x.loc[10,'timestamp']=x.timestamp.iloc[9];invalid.append(x)
        x=self.bars.copy();x['timestamp']=x.timestamp.dt.tz_localize(None);invalid.append(x)
        x=self.bars.copy();x['timestamp']+=pd.Timedelta(minutes=1);invalid.append(x)
        x=self.bars.copy();x['available_at']=x.timestamp-pd.Timedelta(seconds=1);invalid.append(x)
        x=self.bars.copy();x['available_at']=x.timestamp+pd.Timedelta(hours=1);invalid.append(x)
        x=self.bars.copy();x['symbol']='BTC';invalid.append(x)
        for x in invalid:
            with self.subTest(shape=x.shape),self.assertRaises(ValueError):validate_bars(x)
        with self.assertRaises(ValueError):build_features(self.bars.iloc[:3])
        for args in ({'higher_multiple':1},{'swing_radius':True},{'interval':pd.Timedelta(0)}):
            with self.assertRaises(ValueError):FeatureConfig(**args)
        with self.assertRaises(ValueError):synthetic_bars(20)


class CalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config=ExperimentConfig(purpose='ENGINEERING_SYNTHETIC',min_train=150,min_calibration=100,test_size=80,min_class=10)
        cls.bars=synthetic_bars()
        cls.dataset=prepare_dataset(cls.bars,cls.config)
        cls.fold=chronological_folds(cls.dataset,cls.config)[0]
        cls.columns=sum(feature_families().values(),())
        cls.model=CalibratedFoldModel(cls.columns,cls.config)
        cls.model.fit(cls.dataset,cls.fold)

    def new_model(self, pca=False):
        return CalibratedFoldModel(self.columns,self.config,pca=pca)

    def test_purge_uses_label_availability_at_both_boundaries(self):
        f=self.fold;x=self.dataset;gap=pd.Timedelta(hours=1)
        self.assertLess(x.label_end_available_at.iloc[list(f.train)].max(),x.available_at.iloc[f.calibration[0]]-gap)
        self.assertLess(x.label_end_available_at.iloc[list(f.calibration)].max(),x.available_at.iloc[f.test[0]]-gap)
        self.assertFalse(set(f.train)&set(f.calibration))
        self.assertFalse(set(f.calibration)&set(f.test))
        with self.assertRaises(ValueError):chronological_folds(x.iloc[:400],self.config)

    def test_model_identity_scaler_and_pca_ignore_test_outcomes(self):
        first=self.new_model(True);first.fit(self.dataset,self.fold)
        changed=self.dataset.copy()
        changed.loc[list(self.fold.test),'favorable']=1-changed.favorable.iloc[list(self.fold.test)]
        changed.loc[list(self.fold.test),'gross_forward_return']=.99
        changed.loc[list(self.fold.test),list(self.columns)]=123
        second=self.new_model(True);second.fit(changed,self.fold)
        self.assertEqual(first.model_id,second.model_id)
        np.testing.assert_array_equal(first.pipeline.named_steps['scale'].mean_,second.pipeline.named_steps['scale'].mean_)
        np.testing.assert_array_equal(first.pipeline.named_steps['reduce'].components_,second.pipeline.named_steps['reduce'].components_)
        self.assertLess(first.pipeline.named_steps['reduce'].n_components_,len(self.columns))

    def test_fit_and_prediction_fail_closed(self):
        with self.assertRaises(ValueError):self.new_model().predict(self.dataset.iloc[[self.fold.test[0]]])
        with self.assertRaises(ValueError):self.model.fit(self.dataset,self.fold)
        with self.assertRaises(ValueError):CalibratedFoldModel(('gross_forward_return',),self.config)
        with self.assertRaises(ValueError):CalibratedFoldModel((),self.config)
        with self.assertRaises(ValueError):self.new_model().fit(self.dataset,Fold(self.fold.train,self.fold.train,self.fold.test))
        with self.assertRaises(ValueError):self.new_model().fit(self.dataset,Fold((1,),self.fold.calibration,self.fold.test))
        with self.assertRaises(ValueError):self.new_model().fit(self.dataset,Fold((-1,)+self.fold.train,self.fold.calibration,self.fold.test))
        changed=self.dataset.copy();changed.loc[self.fold.train[-1],'label_end_available_at']=changed.available_at.iloc[self.fold.calibration[0]]
        with self.assertRaises(ValueError):self.new_model().fit(changed,self.fold)
        changed=self.dataset.copy();changed.loc[list(self.fold.calibration),'favorable']=0
        with self.assertRaises(ValueError):self.new_model().fit(changed,self.fold)
        changed=self.dataset.copy();changed.loc[self.fold.train[0],self.columns[0]]=np.nan
        with self.assertRaises(ValueError):self.new_model().fit(changed,self.fold)
        with self.assertRaises(ValueError):self.model.predict(self.dataset.iloc[[self.fold.train[0]]])
        changed=self.dataset.iloc[[self.fold.test[0]]].copy();changed[self.columns[0]]=np.nan
        with self.assertRaises(ValueError):self.model.predict(changed)

    def test_probabilities_metrics_and_protocol_validation(self):
        raw,p,support=self.model.predict(self.dataset.iloc[list(self.fold.test)])
        self.assertTrue(np.isfinite(p).all());self.assertTrue(((p>=0)&(p<=1)).all())
        self.assertEqual(support.dtype,np.dtype(bool))
        m=calibration_metrics([0,1],[.5,.5]);self.assertEqual(m['brier'],.25);self.assertEqual(m['ece'],0)
        m=calibration_metrics([0,1],[0,1]);self.assertEqual(m['brier'],0);self.assertEqual(sum(v['n'] for v in m['reliability']),2)
        for labels,prob in [([],[]),([0],[np.nan]),([2],[.5]),([0],[1.1]),([0,1],[.5])]:
            with self.assertRaises(ValueError):calibration_metrics(labels,prob)
        for kwargs in ({'purpose':'LIVE'},{'min_calibration':50},{'horizon':0},{'cost_fraction':np.nan},{'cost_fraction':0}):
            with self.assertRaises(ValueError):ExperimentConfig(**kwargs)

    def test_ablation_retains_failure_results_and_does_not_choose_winner(self):
        impossible=ExperimentConfig(purpose='ENGINEERING_SYNTHETIC',min_train=150,min_calibration=100,test_size=80,min_class=101)
        report,_,models=run_ablation(self.bars,impossible)
        self.assertFalse(models);self.assertTrue(all(r['status']=='INSUFFICIENT_SUPPORT' for r in report['results']))
        self.assertFalse(report['paper_graduated']);self.assertFalse(report['best_variant_selected'])

    def test_hypothesis_is_not_execution_or_prospective_evidence(self):
        rows=self.dataset.iloc[[self.fold.test[0]]];at=rows.available_at.iloc[0].to_pydatetime()
        generator=FourSystemHypothesisGenerator(self.model)
        h=generator.generate(rows,at)[0]
        result=ResearchTradingOrchestrator().process_candidate(h)
        self.assertEqual(result.signal.decision,Decision.NO_TRADE);self.assertEqual(result.signal.confidence,0)
        self.assertIsNone(result.fill)
        self.assertFalse(h.metadata['prospective_evidence']);self.assertFalse(h.metadata['execution_authorized'])
        self.assertFalse(h.metadata['eligible_for_v51'])
        self.assertEqual(h.hypothesis_id,generator.generate(rows,at)[0].hypothesis_id)
        with self.assertRaises(ValueError):generator.generate(rows,at+timedelta(seconds=1))
        with self.assertRaises(ValueError):generator.generate(pd.concat([rows,rows]),at)
        with self.assertRaises(ValueError):FourSystemHypothesisGenerator(self.model,D('.57'))
        with self.assertRaises(ValueError):FourSystemHypothesisGenerator(self.model,.58)


class RiskTests(unittest.TestCase):
    def setUp(self):
        self.at=pd.Timestamp('2025-01-01',tz='UTC').to_pydatetime()
        self.stamp=EvidenceStamp('TRAILING',self.at,self.at,'test')

    def check(self,returns,**kw):
        return tail_risk_ceiling(kw.get('equity',D(10000)),kw.get('cash',D(10000)),kw.get('held',D(0)),
            returns,D('.0024'),kw.get('stamp',self.stamp),self.at,kw.get('config',TailRiskConfig()))

    def test_exact_fractional_tail_and_funding_exposure_ceiling(self):
        r=[D('-.1')]+[D('0')]*100
        result=self.check(r)
        self.assertEqual(result.unit_expected_shortfall,D('.0024')+D('.1')/D('5.05'))
        self.assertEqual(result.max_notional_quote,D(1000));self.assertFalse(result.execution_authorized)
        self.assertEqual(self.check(r,cash=D(20)).max_notional_quote,D(20))
        self.assertEqual(self.check(r,held=D(995)).max_notional_quote,D(5))
        self.assertEqual(self.check(r,held=D(1001)).reason,'NO_RISK_CAPACITY')

    def test_extreme_tail_limits_size_and_insufficient_scenarios_abstain(self):
        bad=self.check([D('-.8')]*100)
        self.assertEqual(bad.max_notional_quote,D(350)/D('.8024'))
        self.assertEqual(self.check([D('.1')]*100).unit_expected_shortfall,D(0))
        self.assertEqual(self.check([D(0)]*99).reason,'INSUFFICIENT_TAIL_SCENARIOS')

    def test_no_floats_stale_future_or_weaker_limits(self):
        for kw in ({'max_position_weight':D('.11')},{'cvar_budget':D('.04')},{'alpha':D('.9')},{'min_scenarios':99}):
            with self.assertRaises(ValueError):TailRiskConfig(**kw)
        for vals in ([0.0]*100,[D('-1.1')]*100,[D('NaN')]*100):
            with self.assertRaises((TypeError,ValueError)):self.check(vals)
        for delta in (timedelta(seconds=1),-timedelta(days=2)):
            stamp=EvidenceStamp('BAD',self.at+delta,self.at+delta)
            with self.assertRaises(ValueError):self.check([D(0)]*100,stamp=stamp)


class JournalIntegrationTests(unittest.TestCase):
    def test_full_replay_is_idempotent_and_non_executing(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'research.sqlite'
            first=run(path);second=run(path)
            self.assertEqual(first,second)
            self.assertEqual(first['trials'],12);self.assertEqual(first['hypotheses_logged'],160)
            self.assertFalse(first['economic_validation']);self.assertFalse(first['prospective_evidence'])
            with closing(TradeJournal(path,first['experiment_id'])) as journal:
                events=journal.events()
            self.assertEqual(len(events),163)
            self.assertEqual({e['kind'] for e in events},{'FOUR_SYSTEM_HYPOTHESIS','FOUR_SYSTEM_TAIL_CEILING','FOUR_SYSTEM_RESEARCH_REPORT'})
            self.assertTrue(all(e['payload']['decision']=='NO_TRADE' for e in events if e['kind']=='FOUR_SYSTEM_HYPOTHESIS'))
            self.assertTrue(all(not e['payload']['execution_authorized'] for e in events if e['kind']!='FOUR_SYSTEM_RESEARCH_REPORT'))


if __name__=='__main__':unittest.main()
