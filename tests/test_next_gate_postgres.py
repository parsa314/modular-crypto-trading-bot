"""Real isolated V52 schemas; synthetic orders only, no broker/network transport."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import select
import subprocess
import sys
from threading import Barrier
import unittest
from urllib.parse import urlparse
from uuid import uuid4

from p0_fixtures import seed, approve, governor, ACCOUNT, NOW, observation
from research_bot.contracts import ExecutionMode
from research_bot.execution import ExecutionFill, OrderSide
from research_bot.execution_hardening_v52 import DuplicateSettlementError, commit_fill_atomic
from research_bot.order_reconciliation_v52 import OrderLifecycle, ReconciledOrder
from research_bot.persistence import PostgresPaperStore
from research_bot.testnet_execution import (
    AmbiguousTestnetSubmission, AmbiguousTransportOutcome, MarketPrecision,
    PostgresTestnetOrderStore, TestnetOrderIntent, TestnetExecutionGateway,
    TestnetSafetyError, VenueOrderSnapshot,
)
from research_bot.live_readiness import LiveReadinessChecklist, evaluate_live_readiness
from research_bot.operational_risk import BrokerRiskContext, OperationalRiskEngine
from research_bot.risk import RiskSnapshot


class NextGatePostgresValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg = psycopg
        cls.root_dsn = os.environ.get('TEST_DATABASE_URL', '')
        parsed = urlparse(cls.root_dsn)
        if os.environ.get('NEXT_GATE_ISOLATED_DATABASE') != '1' or parsed.hostname not in {'127.0.0.1','localhost'} or parsed.path != '/execution_v52':
            raise RuntimeError('Disposable local execution_v52 plus explicit isolation marker required; no skip')

    def record(self, event, **values):
        path = os.environ.get('NEXT_GATE_EVENT_LOG')
        if path:
            with Path(path).open('a') as f:
                f.write(json.dumps(dict(event=event, at=datetime.now(timezone.utc).isoformat(), **values))+'\n')

    def setUp(self):
        from psycopg import sql
        from psycopg.conninfo import make_conninfo
        self.schema = 'next_gate_' + uuid4().hex
        with self.psycopg.connect(self.root_dsn, autocommit=True) as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.dsn = make_conninfo(self.root_dsn, options='-c search_path='+self.schema)
        self.store = PostgresPaperStore(self.dsn, initial_cash=1000.0)
        self.store.ensure_schema()
        self.orders = PostgresTestnetOrderStore(self.dsn)
        self.orders.ensure_schema()
        self.now = NOW

    def tearDown(self):
        from psycopg import sql
        assert re.fullmatch(r'next_gate_[0-9a-f]{32}', self.schema)
        with self.psycopg.connect(self.root_dsn, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def fill(self, cid='SYNTHETIC_FILL'):
        return ExecutionFill(cid, 'BTC/USDT', OrderSide.BUY, 1.0, 1.0, 100.0, 0.0, 0.0,
                             ExecutionMode.PAPER, 'FILLED', self.now)

    def commit(self, *, version='NEXT_GATE_FIXTURE'):
        return commit_fill_atomic(self.store, symbol='BTC/USDT', fill=self.fill(), strategy_version=version,
                                  metadata={'fixture':True, 'evidence_class':'NON_PROSPECTIVE'})

    def assert_account(self, cash, quantity, fills):
        reopened = PostgresPaperStore(self.dsn, initial_cash=1000.0)
        self.assertEqual(Decimal(str(reopened.get_account().cash)), Decimal(cash))
        self.assertEqual(Decimal(str(reopened.get_position('BTC/USDT').quantity)), Decimal(quantity))
        self.assertEqual(reopened.summary()['fills'], fills)

    def test_atomic_rollback_after_position_and_cash_update(self):
        with self.assertRaises(self.psycopg.errors.NotNullViolation): self.commit(version=None)
        self.assert_account('1000','0',0)
        self.record('EXPECTED_NOT_NULL_FAILURE_FULL_ROLLBACK', verified=True)

    def test_successful_settlement_and_duplicate_apply_only_once(self):
        self.commit()
        with self.assertRaises(DuplicateSettlementError): self.commit()
        self.assert_account('900','1',1)
        self.record('SETTLEMENT_ONCE', verified=True)

    def test_two_workers_settle_one_order_once(self):
        barrier = Barrier(2, timeout=10)
        def worker(_):
            store = PostgresPaperStore(self.dsn, initial_cash=1000.0)
            barrier.wait()
            try:
                commit_fill_atomic(store, symbol='BTC/USDT', fill=self.fill(), strategy_version='NEXT_GATE_FIXTURE', metadata={'fixture':True})
                return 'COMMITTED'
            except (DuplicateSettlementError, self.psycopg.errors.UniqueViolation):
                return 'DUPLICATE_ROLLED_BACK'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, range(2)))
        self.assertCountEqual(results, ['COMMITTED','DUPLICATE_ROLLED_BACK'])
        self.assert_account('900','1',1)
        self.record('TWO_WORKER_SETTLEMENT', results=results)

    def test_sigkill_rolls_back_uncommitted_schema_transaction(self):
        script = """import os,sys,psycopg
with psycopg.connect(os.environ['NEXT_GATE_CRASH_DSN']) as conn:
    conn.execute('UPDATE paper_account SET cash=1 WHERE id=1')
    print('UNCOMMITTED',flush=True)
    sys.stdin.read()
"""
        proc = subprocess.Popen([sys.executable,'-c',script],env={**os.environ,'NEXT_GATE_CRASH_DSN':self.dsn},
                                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            ready,_,_ = select.select([proc.stdout],[],[],10)
            self.assertTrue(ready,'child transaction did not reach fault boundary')
            self.assertEqual(proc.stdout.readline().strip(),'UNCOMMITTED')
        finally:
            proc.kill();proc.wait(timeout=10)
            for stream in (proc.stdin,proc.stdout,proc.stderr): stream.close()
        self.assert_account('1000','0',0)
        self.record('SIGKILL_UNCOMMITTED_SQL_ROLLBACK', return_code=proc.returncode)

    def test_restart_reads_committed_fill_without_second_charge(self):
        self.commit()
        self.assert_account('900','1',1)
        self.store = PostgresPaperStore(self.dsn, initial_cash=1000.0)
        with self.assertRaises(DuplicateSettlementError): self.commit()
        self.assert_account('900','1',1)

    def create_partial(self, quantity=Decimal('1')):
        intent = TestnetOrderIntent('SYNTHETIC_ORDER','BTC/USDT',OrderSide.BUY,quantity,Decimal('100'),self.now)
        order = ReconciledOrder('SYNTHETIC_ORDER','BTC/USDT','buy',quantity,state=OrderLifecycle.PARTIAL,
                                venue_order_id='SYNTHETIC_VENUE',matched_quantity=0.2,updated_at=self.now)
        self.orders.create(venue='SYNTHETIC_NO_BROKER',intent=intent,order=order)
        return order

    def test_stale_worker_cannot_decrease_persisted_cumulative_fill(self):
        self.create_partial()
        a = PostgresTestnetOrderStore(self.dsn)
        b = PostgresTestnetOrderStore(self.dsn)
        loaded_a, loaded_b = a.get('SYNTHETIC_ORDER'), b.get('SYNTHETIC_ORDER')
        newer = TestnetExecutionGateway._merge_snapshot(loaded_a,VenueOrderSnapshot('SYNTHETIC_ORDER','SYNTHETIC_VENUE',OrderLifecycle.PARTIAL,Decimal('0.8')))
        a.update(newer)
        stale = TestnetExecutionGateway._merge_snapshot(loaded_b,VenueOrderSnapshot('SYNTHETIC_ORDER','SYNTHETIC_VENUE',OrderLifecycle.PARTIAL,Decimal('0.4')))
        try: b.update(stale)
        except RuntimeError: pass
        actual = Decimal(str(a.get('SYNTHETIC_ORDER').matched_quantity))
        self.record('STALE_ORDER_UPDATE', expected='0.8',actual=str(actual))
        self.assertEqual(actual,Decimal('0.8'))

    def test_numeric_order_roundtrip_preserves_exact_decimal(self):
        quantity = Decimal('1.000000000000000001')
        self.create_partial(quantity)
        with self.psycopg.connect(self.dsn) as conn:
            persisted = conn.execute('SELECT requested_quantity FROM testnet_orders').fetchone()[0]
        self.assertEqual(persisted,quantity)
        self.assertEqual(Decimal(str(self.orders.get('SYNTHETIC_ORDER').requested_quantity)),quantity)

    def gateway(self, transport, store=None):
        readiness = evaluate_live_readiness(LiveReadinessChecklist(
            scientific_promotion_passed=True,forward_paper_reconciled=True,
            order_reconciliation_validated=True,risk_constitution_unified=True))
        return TestnetExecutionGateway(transport=transport,store=store or self.orders,readiness=readiness,risk_governor=governor(self.dsn),account_id=ACCOUNT,clock=lambda:self.now)

    def test_ambiguous_submit_reopen_reconciles_without_resend(self):
        class Transport:
            sandbox = True
            venue = 'SYNTHETIC_NO_BROKER'
            calls = 0
            def submit_market_order(inner,intent):
                inner.calls += 1
                raise AmbiguousTransportOutcome('synthetic timeout after send')
            def fetch_order(inner,**kwargs):
                return VenueOrderSnapshot(kwargs['client_order_id'],'synthetic-venue',OrderLifecycle.PARTIAL,Decimal('0.5'))
        transport = Transport()
        context = BrokerRiskContext('SYNTHETIC_ACCOUNT','BTCUSDT',self.now,self.now,Decimal('0'),
            reconciled=True,unresolved_orders=False,day_start_equity=Decimal('1000'),day_start_date_utc=self.now.date().isoformat())
        g=seed(self.dsn)
        risk=approve(g,cid='SYNTHETIC_TIMEOUT')
        args = dict(client_order_id='SYNTHETIC_TIMEOUT',symbol='BTC/USDT',side=OrderSide.BUY,
            quantity=Decimal('1'),reference_price=Decimal('100'),precision=MarketPrecision(Decimal('0.001'),Decimal('0.001'),Decimal('5')),
            risk_decision=risk,created_at=self.now)
        with self.assertRaises(AmbiguousTestnetSubmission): self.gateway(transport).submit_market(**args)
        reopened = self.gateway(transport,PostgresTestnetOrderStore(self.dsn))
        with self.assertRaises(TestnetSafetyError): reopened.submit_market(**args)
        self.assertEqual(transport.calls,1)
        self.assertEqual(reopened.reconcile('SYNTHETIC_TIMEOUT').state,OrderLifecycle.PARTIAL)
        self.assertEqual(reopened.reconcile('SYNTHETIC_TIMEOUT').matched_quantity,0.5)
        self.record('AMBIGUOUS_SUBMIT_REOPEN_NO_RESEND',transport_calls=transport.calls)

    def test_reductions_use_same_pg_intent_and_identity_under_entry_halts(self):
        class Transport:
            sandbox = True
            venue = 'SYNTHETIC_NO_BROKER'
            calls = 0
            def submit_market_order(inner,intent):
                inner.calls += 1
                return VenueOrderSnapshot(intent.client_order_id,'synthetic-venue',OrderLifecycle.PARTIAL,Decimal('0.25'))
            def fetch_order(inner,**kwargs):
                return VenueOrderSnapshot(kwargs['client_order_id'],'synthetic-venue',OrderLifecycle.PARTIAL,Decimal('0.25'))
        transport = Transport()
        for scenario,equity,unknown in [('normal',1000,False),('daily',980,False),('drawdown',940,False),('recovery_unknown',940,True)]:
            with self.subTest(scenario=scenario):
                context = BrokerRiskContext('SYNTHETIC_ACCOUNT','BTCUSDT',self.now,self.now,Decimal('1'),
                    reconciled=True,unresolved_orders=unknown,day_start_equity=Decimal('1000'),day_start_date_utc=self.now.date().isoformat())
                snapshot = RiskSnapshot(equity,1000,0.1,0.1,0,1,1)
                engine = OperationalRiskEngine()
                common = dict(symbol='BTC/USDT',quantity=Decimal('0.5'),price=100,now=self.now,context=context,stop_price=98,roundtrip_cost_bps=50)
                buy = engine.evaluate_order(snapshot,side='BUY',**common)
                if scenario != 'normal': self.assertFalse(buy.approved)
                g=governor(self.dsn);g.ensure_schema()
                if scenario=='normal':
                    seed(self.dsn,held='1',cash='900')
                else:
                    from datetime import timedelta
                    self.now += timedelta(seconds=1)
                    g.observe(observation(now=self.now,equity=str(equity),cash=str(equity-100),held='1',unresolved=unknown),received_at=self.now)
                sell = engine.evaluate_order(snapshot,side='SELL',**common) if unknown else approve(g,cid='SYNTHETIC_EXIT_'+scenario,side='SELL',quantity='0.5',now=self.now)
                args = dict(client_order_id='SYNTHETIC_EXIT_'+scenario,symbol='BTC/USDT',side=OrderSide.SELL,
                    quantity=Decimal('0.5'),reference_price=Decimal('100'),precision=MarketPrecision(Decimal('0.001'),Decimal('0.001'),Decimal('5')),
                    risk_decision=sell,created_at=self.now)
                if unknown:
                    before = transport.calls
                    with self.assertRaises(TestnetSafetyError): self.gateway(transport).submit_market(**args)
                    self.assertEqual(transport.calls,before)
                    self.assertIsNone(self.orders.get(args['client_order_id']))
                else:
                    self.assertTrue(sell.approved)
                    self.gateway(transport).submit_market(**args)
                    stored = PostgresTestnetOrderStore(self.dsn).get(args['client_order_id'])
                    self.assertEqual(stored.side,'sell')
                    self.assertEqual(stored.state,OrderLifecycle.PARTIAL)
                    with self.assertRaises(TestnetSafetyError): self.gateway(transport).submit_market(**args)
                    self.assertEqual(self.gateway(transport).reconcile(args['client_order_id']).matched_quantity,0.25)
        self.record('REDUCTION_PG_INTENT_SCENARIOS',transport_calls=transport.calls,actual_broker=False)


if __name__ == '__main__': unittest.main()
