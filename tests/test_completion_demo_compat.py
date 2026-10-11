from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

from research_bot.completion.demo_compat import (
    DemoExecutionAdmission, DemoJournalProjection, DemoLimits, convert_demo_hypothesis, _real,
)
from research_bot.completion.execution.cost_preflight import ExecutionCostPreflight
from research_bot.completion.execution.impact_estimator import SquareRootImpactEstimator
from research_bot.completion.execution.liquidity_gate import DepthAwareLiquidityGate
from research_bot.completion.execution.event_driven_simulator import EventDrivenExecutionSimulator, OrderState, OrderRequest
from research_bot.completion.models import CompletionPolicy, ScenarioEconomics
from research_bot.completion.portfolio.conflict_arbiter import PortfolioView
from research_bot.completion.journal import TradeJournal
from research_bot.hypothesis import HypothesisCandidate, HypothesisFamily, HypothesisSide
from scripts.run_demo_migration import run_migration
from scripts.run_completion_paper import build_bundle, synthetic_frames
from research_bot.completion.backtest.runner import BacktestRunner
from test_completion_strategies import T, stamp
from test_completion_microstructure import liquid
from test_completion_execution import request


def fixture(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).parent/'fixtures'/'complete_demo_bot'/(name+'.py'))
    module=importlib.util.module_from_spec(spec); sys.modules[name]=module; spec.loader.exec_module(module)
    return module


def candidate(identity="manual", at=T, **kwargs):
    return HypothesisCandidate(identity,HypothesisFamily.GRID,"BTC_USDT",HypothesisSide.BUY,
        at,at+timedelta(milliseconds=20),("synthetic_fixture",),(stamp(at),),{},
        {"economics":ScenarioEconomics(D("0.05"),D("0.005"),D("0.001"),D("0.001"),D("0.9")).to_dict()},**kwargs)


class DemoContractsTests(unittest.TestCase):
    def test_actual_uploaded_contract_roundtrip_without_mutation(self):
        legacy=fixture('original_contracts')
        hyp=legacy.HypothesisCandidate("legacy-one","grid_v1","BTC_USDT",legacy.Side.BUY,T,.01,.002,.001,.001,.8)
        converted=convert_demo_hypothesis(hyp,stamp())
        self.assertEqual(converted.side,HypothesisSide.BUY)
        self.assertEqual(converted.metadata['legacy_hypothesis_id'],'legacy-one')
        self.assertNotIn('economics',converted.metadata)
        self.assertNotIn('plan',converted.metadata)
        self.assertEqual(hyp.hypothesis_id,'legacy-one')
        self.assertEqual(convert_demo_hypothesis(replace(hyp,strategy_id='tri_arb_v1'),stamp()).side,HypothesisSide.MULTI_LEG)
        self.assertEqual(convert_demo_hypothesis(replace(hyp,strategy_id='rsi_ma',side=legacy.Side.SELL),stamp()).side,HypothesisSide.SELL)
        for value in (float('nan'),float('inf'),True):
            with self.assertRaises(ValueError):
                convert_demo_hypothesis(replace(hyp,expected_return=value),stamp())
        with self.assertRaises(ValueError):
            convert_demo_hypothesis(replace(hyp,side=type('Side',(),{'value':'short'})()),stamp())
        with self.assertRaises(ValueError):
            convert_demo_hypothesis(hyp,stamp(T+timedelta(seconds=1)))

    def test_limits_and_real_projection_reject_invalid_or_inflated_inputs(self):
        for kwargs in ({'max_position_pct':D('.11')},{'max_daily_loss_pct':D('.04')},
            {'max_open_positions':4},{'max_open_positions':True},{'cooldown':timedelta(seconds=29)}):
            with self.assertRaises(ValueError): DemoLimits(**kwargs)
        with self.assertRaises(ValueError): _real(D('1e10000'))
        with self.assertRaises(ValueError): CompletionPolicy(execution_revision='old')


class DemoAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.journal=TradeJournal(':memory:','demo_admission_tests')
        self.sim=EventDrivenExecutionSimulator({'USDT':D(1000),'BTC':D(2)},self.journal)
        self.admission=DemoExecutionAdmission(ExecutionCostPreflight(SquareRootImpactEstimator(D(1)),
            DepthAwareLiquidityGate(),self.journal),D('.02'),D(10000))

    def tearDown(self): self.journal.close()

    def view(self, at=T, equity=None):
        inventory=self.sim.inventory(at)
        v=PortfolioView(inventory,{'BTC':D(100),'USDT':D(1)},stamp(at),'USDT',D(1200))
        if equity is not None:
            from research_bot.completion.models import Inventory
            v=replace(v,inventory=Inventory({'USDT':D(equity)},{},stamp(at)))
        return v

    def assess(self, hyp=None, req=None, at=T, view=None):
        return self.admission.assess(req or request(),hyp or candidate(at=at),view or self.view(at),liquid(at),self.sim,at)

    def test_approved_impact_is_charged_in_actual_fill_without_resizing(self):
        assessed=self.assess()
        self.assertEqual(assessed.quantity,D(1))
        self.assertEqual(assessed.additional_impact_bps,D(20))
        self.sim.submit(assessed,True)
        fills=self.sim.on_book(liquid(T+timedelta(seconds=1)))
        self.assertEqual(D(fills[0]['price']),D('100.1')*D('1.0022'))
        self.assertEqual(D(fills[0]['additional_impact_quote']),D('.2002'))
        self.assertEqual(self.sim.balances()['USDT'],D(1000)-D(fills[0]['price']))
        self.assertEqual(self.sim.balances()['BTC'],D('2.999'))
        self.assertTrue(self.journal.events('DEMO_ADMISSION')[-1]['payload']['approved'])

    def test_negative_admissions_hpt_size_missing_economics_and_total_cost(self):
        self.assertIsNone(self.assess(candidate('hpt_v1')))
        self.assertIsNone(self.assess(req=replace(request(),quantity=D(2))))
        self.assertIsNone(self.assess(replace(candidate('missing'),metadata={})))
        tiny=replace(candidate('tiny'),metadata={'economics':ScenarioEconomics(D('.0024'),D(0),D(0),D(0),D('.9')).to_dict()})
        self.assertIsNone(self.assess(tiny))
        self.admission.volume=D(1000)
        self.assertIsNone(self.assess(candidate('reduce')))
        self.admission.volume=D(10000)
        self.sim.policy=replace(self.sim.policy,slippage_rate=D('.0025'))
        self.assertIsNone(self.assess(candidate('sum_cost')))
        self.assertEqual(self.journal.events('DEMO_ADMISSION')[-1]['payload']['reason'],'TOTAL_SLIPPAGE_EXCEEDS_UNCHANGED_LIMIT')
        self.assertEqual(self.sim.orders(),())

    def test_daily_loss_capacity_and_cooldown_use_journal_time(self):
        self.journal.append('start','REPLAY_FRAME',T,{'equity':'1500'})
        self.assertIsNone(self.assess(candidate('daily')))
        self.journal.connection.execute("DELETE FROM completion_events WHERE event_id='start'")
        for i in range(3): self.sim.submit(request(str(i)),True)
        self.assertIsNone(self.assess(candidate('capacity')))
        self.sim.on_book(liquid(T+timedelta(seconds=1)))
        self.assertIsNone(self.assess(candidate('cool',T+timedelta(seconds=2)),replace(request(),created_at=T+timedelta(seconds=2)),T+timedelta(seconds=2)))
        reasons={e['payload']['reason'] for e in self.journal.events('DEMO_ADMISSION')}
        self.assertTrue({'DEMO_DAILY_LOSS_LIMIT','DEMO_MAX_OPEN_POSITIONS','DEMO_COOLDOWN'} <= reasons)

    def test_research_stats_and_multileg_cannot_use_single_pair_fallback(self):
        sim=EventDrivenExecutionSimulator({'USDT':D(1000)},self.journal,
            CompletionPolicy(mode='BACKTEST',purpose='GRADUATED_RESEARCH'))
        self.assertIsNone(self.admission.assess(request(),candidate(),self.view(),liquid(),sim,T))
        self.admission.reject_multileg(candidate('triangle'),T)
        self.assertEqual(self.journal.events('DEMO_ADMISSION')[-1]['payload']['reason'],'MULTILEG_REQUIRES_PER_LEG_ADMISSION')

    def test_combined_slippage_ceiling_and_decimal_impact_input(self):
        req=replace(request(),additional_impact_bps=D(24))
        self.assertEqual(self.sim.submit(req,True).state,OrderState.REJECTED)
        for impact in (D(26),.1,D('NaN')):
            with self.assertRaises(ValueError): replace(request(),additional_impact_bps=impact)
        row=request().to_dict(); row.pop('additional_impact_bps')
        self.assertEqual(OrderRequest.from_dict(row).additional_impact_bps,D(0))


class DemoProjectionTests(unittest.TestCase):
    def test_runner_rejects_admission_resizing_or_redirection_atomically(self):
        class RedirectingAdmission:
            def __init__(self, change): self.change=change
            def configuration(self): return {'test_only':'invalid_redirect'}
            def reject_multileg(self, candidate, at): pass
            def assess(self, req, *args): return replace(req,**self.change(req))
        for change in (lambda req:{'quantity':req.quantity/2},
                lambda req:{'symbol':'ETH_USDT'},lambda req:{'not_before':req.not_before+timedelta(seconds=1)}):
            with self.subTest(change=change), closing(TradeJournal(':memory:','invalid_redirect')) as journal:
                runner=BacktestRunner(journal,build_bundle(),{'USDT':D(1000),'BTC':D(1)},
                    admission=RedirectingAdmission(change))
                before=journal.events()
                with self.assertRaisesRegex(ValueError,'silently resize or redirect'):
                    runner.step(synthetic_frames(1)[0])
                self.assertEqual(journal.events(),before)
                self.assertEqual(runner.simulator.orders(),())

    def test_namespaced_identity_collision_preserves_existing_row(self):
        with closing(TradeJournal(':memory:','identity_collision')) as journal:
            projection=DemoJournalProjection(journal)
            journal.connection.execute("INSERT INTO paper_orders VALUES(?,?,?,?,?,?,?,?,?,?)",
                ('COMP:identity_collision:one',T.isoformat(),'BTC_USDT','sell','limit',9,99,'FILLED','old','old'))
            before=journal.connection.execute('SELECT * FROM paper_orders').fetchone()
            sim=EventDrivenExecutionSimulator({'USDT':D(1000)},journal)
            sim.submit(request('one'),True)
            with self.assertRaises(ValueError): projection.sync()
            self.assertEqual(journal.connection.execute('SELECT * FROM paper_orders').fetchone(),before)
            self.assertIsNone(journal.restore('demo_projection'))

    def test_funded_synthetic_runner_charges_impact_and_projects_fills(self):
        with closing(TradeJournal(':memory:','funded_fixture_only')) as journal:
            admission=DemoExecutionAdmission(ExecutionCostPreflight(SquareRootImpactEstimator(D('.25')),
                DepthAwareLiquidityGate(),journal),D('.02'),D(1000000))
            runner=BacktestRunner(journal,build_bundle(),{'USDT':D(1000),'BTC':D(1)},
                CompletionPolicy(mode='PAPER'),admission=admission)
            projection=DemoJournalProjection(journal)
            for frame in synthetic_frames(20): runner.step(frame); projection.sync()
            fills=[e['payload']['fill'] for e in journal.events('ORDER_TRANSITION') if e['payload'].get('fill')]
            self.assertGreater(len(fills),0)
            self.assertTrue(all(D(fill['additional_impact_bps'])>0 for fill in fills))
            self.assertEqual(journal.connection.execute('SELECT COUNT(*) FROM paper_fills').fetchone()[0],len(fills))
            with self.assertRaises(ValueError):
                BacktestRunner(journal,build_bundle(),{'USDT':D(1000),'BTC':D(1)},CompletionPolicy(mode='PAPER'))

    def test_actual_original_journal_schema_preserves_history_and_projects_partial_fills(self):
        original=fixture('original_trade_journal')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'original.sqlite'
            legacy=original.TradeJournal(path)
            legacy.log_order('old','BTC_USDT','buy',.25)
            legacy.log_fill('old-fill','old',100,.25,.05)
            with closing(TradeJournal(path,'projection_test')) as journal:
                before=journal.connection.execute("SELECT * FROM paper_orders WHERE order_id='old'").fetchone()
                projection=DemoJournalProjection(journal)
                sim=EventDrivenExecutionSimulator({'USDT':D(1000)},journal,CompletionPolicy(slippage_rate=D(0),fee_rate=D('.002')))
                sim.submit(request(),True); projection.sync()
                sim.on_book(liquid(T+timedelta(seconds=1),ask_qty='1')); projection.sync()
                self.assertEqual(journal.connection.execute("SELECT status FROM paper_orders WHERE order_id!='old'").fetchone()[0],'PARTIALLY_FILLED')
                sim.on_book(liquid(T+timedelta(seconds=2),ask_qty='9')); projection.sync(); projection.sync()
                self.assertEqual(legacy.summary()['filled_orders'],2)
                self.assertEqual(legacy.summary()['total_fills'],3)
                self.assertEqual(journal.connection.execute("SELECT * FROM paper_orders WHERE order_id='old'").fetchone(),before)
                self.assertEqual(journal.restore('demo_projection')['interpretation'],'LOSSY_REAL_PROJECTION_FEES_QUOTE_EQUIVALENT')
                self.assertEqual(sim.balances()['BTC'],D('.998'))
                journal.append('curve','REPLAY_FRAME',T,{'equity':'1000','balances':{'USDT':'999'},'quote_currency':'USDT'})
                projection.sync()
                self.assertEqual(journal.connection.execute("SELECT cash,pos_val FROM equity_curve WHERE ts=?",(T.isoformat(),)).fetchone(),(999,1))
                with self.assertRaises(ValueError): DemoJournalProjection(journal,'IRT')
                with self.assertRaises(ValueError): DemoJournalProjection(journal,'usd')

    def test_projection_foreign_quote_skip_sell_fees_and_failed_write_rollback(self):
        with closing(TradeJournal(':memory:','projection_atomic')) as journal:
            projection=DemoJournalProjection(journal)
            sim=EventDrivenExecutionSimulator({'BTC':D(2),'USDT':D(1000)},journal,CompletionPolicy(slippage_rate=D(0)))
            sim.submit(replace(request(),side='SELL',protection_price=D(99)),True)
            sim.submit(replace(request('foreign'),symbol='USDT_IRT',side='SELL',protection_price=D(1)),True)
            sim.on_book(liquid(T+timedelta(seconds=1)))
            projection.sync()
            self.assertEqual(journal.connection.execute('SELECT COUNT(*) FROM paper_orders').fetchone()[0],1)
            old_cursor=journal.restore('demo_projection')
            journal.append('bad_curve','REPLAY_FRAME',T,{'equity':'1000','balances':{},'quote_currency':'IRT'})
            with self.assertRaises(ValueError): projection.sync()
            self.assertEqual(journal.restore('demo_projection'),old_cursor)
            self.assertEqual(journal.connection.execute('SELECT COUNT(*) FROM equity_curve').fetchone()[0],0)

    def test_unknown_schema_old_transition_and_missing_source_fail_closed(self):
        with closing(TradeJournal(':memory:','wrong_schema')) as journal:
            journal.connection.execute('CREATE TABLE paper_orders (id TEXT)')
            with self.assertRaises(ValueError): DemoJournalProjection(journal)
        with closing(TradeJournal(':memory:','missing_request')) as journal:
            projection=DemoJournalProjection(journal)
            journal.append('old','ORDER_TRANSITION',T,{'state':'FILLED'})
            with self.assertRaises(ValueError): projection.sync()
            journal.checkpoint('demo_projection',{'count':2,'quote':'USDT'})
            with self.assertRaises(ValueError): projection.sync()

    def test_demo_runtime_resume_and_legacy_projection_match_cold_run(self):
        with tempfile.TemporaryDirectory() as directory:
            directory=Path(directory)
            first=run_migration(directory/'resume.sqlite',35)
            resumed=run_migration(directory/'resume.sqlite',40)
            cold=run_migration(directory/'cold.sqlite',40)
            self.assertEqual(resumed,cold)
            self.assertEqual(first['hpt_status'],'DISABLED_UNVERIFIED_CHECKPOINT')
            self.assertEqual(resumed['legacy_fills'],resumed['fills'])
            with sqlite3.connect(directory/'cold.sqlite') as connection:
                rows=connection.execute('SELECT equity,cash,pos_val FROM equity_curve').fetchall()
                self.assertTrue(all(abs(e-c-p)<1e-8 for e,c,p in rows))


if __name__ == '__main__': unittest.main()
