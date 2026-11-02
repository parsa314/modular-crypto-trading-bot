"""P0 integration oracles: real PostgreSQL, explicit synthetic provenance."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json
import multiprocessing
import os
import select
import subprocess
import sys
from threading import Barrier
import unittest

from p0_fixtures import (ACCOUNT,VENUE,NOW,SOURCE,approve,governor,seed,observation,
                         pg_schema,drop_schema,gateway)
from research_bot.durable_risk import RiskAdmissionError,encoded
from research_bot.execution import OrderSide
from research_bot.order_reconciliation_v52 import OrderLifecycle,ReconciledOrder
from research_bot.testnet_execution import (MarketPrecision,PostgresTestnetOrderStore,
    TestnetOrderIntent,TestnetSafetyError,VenueOrderSnapshot,TestnetExecutionGateway,
    AmbiguousTransportOutcome,AmbiguousTestnetSubmission)


def process_reserve(dsn, start, result, index):
    start.wait(10)
    try:
        approve(governor(dsn),cid='process-'+str(index),quantity='3.5')
        result.put('ACCEPTED')
    except RiskAdmissionError:
        result.put('REJECTED')


def process_post_principal(dsn, start, result, client_order_id):
    start.wait(10)
    try:
        governor(dsn).reconcile_accounting(account_id=ACCOUNT,venue_id=VENUE,
            client_order_id=client_order_id,now=NOW)
        result.put('OK')
    except Exception as exc:
        result.put(type(exc).__name__ + ':' + str(exc))


def process_direct_partial_fill(dsn, client_order_id, result):
    """Independent process/connection, synthetic snapshot, no broker I/O."""
    try:
        store = PostgresTestnetOrderStore(dsn)
        old = store.get(client_order_id)
        store.update(replace(old, state=OrderLifecycle.PARTIAL,
                             matched_quantity=Decimal('0.5'), average_price=Decimal('100')))
        result.put('COMMITTED')
    except Exception as exc:
        result.put(type(exc).__name__ + ':' + str(exc))


def process_uncommitted_order_update(dsn, client_order_id, ready, release):
    """Hold a SQL write open so the parent can kill the isolated worker."""
    import psycopg
    with psycopg.connect(dsn) as conn:
        conn.execute("""UPDATE testnet_orders SET state='PARTIAL',
          matched_quantity=0.5,average_price=100,state_version=state_version+1
          WHERE client_order_id=%s""", (client_order_id,))
        ready.put('UPDATED_BEFORE_COMMIT')
        release.wait(30)


def process_blocking_order_update(dsn, client_order_id, application_name, ready, result):
    """Separate connection used to demonstrate the database lock boundary."""
    import psycopg
    try:
        with psycopg.connect(dsn, application_name=application_name) as conn:
            ready.put('ATTEMPTING')
            conn.execute("""UPDATE testnet_orders SET state='PARTIAL',
              matched_quantity=0.5,average_price=100,state_version=state_version+1
              WHERE client_order_id=%s""", (client_order_id,))
        result.put('COMMITTED')
    except Exception as exc:
        result.put(type(exc).__name__ + ':' + str(exc))


def process_crash_after_snapshot_before_post(dsn, client_order_id):
    store=PostgresTestnetOrderStore(dsn)
    local=store.get(client_order_id)
    filled=replace(local,state=OrderLifecycle.FILLED,matched_quantity=Decimal('1'),average_price=Decimal('100'))
    g=governor(dsn)
    g.reconcile_accounting=lambda **kwargs: os._exit(77)
    g.persist_snapshot_and_account(store=store,order=filled,account_id=ACCOUNT,venue_id=VENUE,now=NOW)
    os._exit(78)


class Transport:
    sandbox=True
    venue=VENUE
    def __init__(self):
        self.calls=0
        self.snapshot=None
        self.timeout=False
    def submit_market_order(self,intent):
        self.calls+=1
        if self.timeout: raise AmbiguousTransportOutcome('synthetic post-send timeout')
        return self.snapshot or VenueOrderSnapshot(intent.client_order_id,'V',OrderLifecycle.ACKNOWLEDGED)
    def fetch_order(self,**kwargs): return self.snapshot


class P0PostgresTests(unittest.TestCase):
    def setUp(self):
        self.root,self.schema,self.dsn=pg_schema()
        self.addCleanup(drop_schema,self.root,self.schema)
        self.g=seed(self.dsn)
        self.now=NOW
        self.transport=Transport()
        self.gateway=gateway(self.transport,dsn=self.dsn,clock=lambda:self.now)
        self.orders=self.gateway.store
        self.precision=MarketPrecision(Decimal('0.000000000000000001'),Decimal('0.000000000000000001'),Decimal('5'),Decimal('0.000000000000000001'))

    def submit(self,a,**changes):
        args=dict(client_order_id=a.client_order_id,symbol=a.symbol,side=OrderSide(a.side),
            quantity=a.approved_quantity,reference_price=a.reference_price,
            precision=self.precision,risk_decision=a,created_at=self.now)
        args.update(changes)
        return self.gateway.submit_market(**args)

    def fetch(self,sql,args=()):
        with self.g._connect() as conn: return conn.execute(sql,args).fetchall()

    def observe(self,**changes):
        self.now+=timedelta(seconds=1)
        o=observation(now=self.now,**changes)
        self.g.observe(o,received_at=self.now)
        return o

    def test_decimal_create_reconcile_pg_and_json_exact(self):
        q=Decimal('0.123456789012345678')
        a=approve(self.g,quantity=str(q))
        saved=self.submit(a)
        self.assertEqual(saved.requested_quantity,q)
        fee=Decimal('0.000000000000000001')
        self.transport.snapshot=VenueOrderSnapshot(a.client_order_id,'V',OrderLifecycle.FILLED,q,Decimal('100'),fee,'USDT')
        done=self.gateway.reconcile(a.client_order_id)
        reopened=PostgresTestnetOrderStore(self.dsn).get(a.client_order_id)
        for item in (done,reopened):
            self.assertEqual(item.requested_quantity,q)
            self.assertEqual(item.matched_quantity,q)
            self.assertEqual(item.fee_reported,fee)
        self.assertEqual(json.loads(encoded({'quantity':reopened.matched_quantity}))['quantity'],str(q))
        self.assertEqual(self.fetch('SELECT cash FROM risk_account_state')[0][0],Decimal('987.654321098765432199'))

    def test_worker_cas_race_preserves_newer_fill(self):
        self.submit(approve(self.g))
        a=PostgresTestnetOrderStore(self.dsn);b=PostgresTestnetOrderStore(self.dsn)
        old_a,old_b=a.get('precision'),b.get('precision')
        barrier=Barrier(2,timeout=10)
        import threading
        written=threading.Event()
        def newer():
            barrier.wait()
            a.update(TestnetExecutionGateway._merge_snapshot(old_a,VenueOrderSnapshot('precision','V',OrderLifecycle.PARTIAL,Decimal('.8'))))
            written.set()
        def stale():
            barrier.wait();self.assertTrue(written.wait(10))
            with self.assertRaises(TestnetSafetyError):
                b.update(TestnetExecutionGateway._merge_snapshot(old_b,VenueOrderSnapshot('precision','V',OrderLifecycle.PARTIAL,Decimal('.4'))))
        with self.assertLogs('research_bot.testnet_execution',level='ERROR'),ThreadPoolExecutor(2) as pool:
            futures=[pool.submit(newer),pool.submit(stale)]
            for f in futures: f.result(timeout=15)
        self.assertEqual(a.get('precision').matched_quantity,Decimal('.8'))

    def test_legacy_unversioned_sql_writer_is_rejected_by_database(self):
        import psycopg
        self.submit(approve(self.g))
        with self.assertRaisesRegex(psycopg.errors.RaiseException,'STALE_ORDER_VERSION'):
            with self.g._connect() as conn:
                conn.execute("UPDATE testnet_orders SET matched_quantity=0.4 WHERE client_order_id='precision'")
        self.assertEqual(self.orders.get('precision').matched_quantity,Decimal('0'))

    def test_binding_fields_and_payload_tampering_are_rejected(self):
        a=approve(self.g)
        for changes in [dict(quantity=Decimal('10')),dict(client_order_id='OTHER'),dict(symbol='ETH/USDT'),
                        dict(side=OrderSide.SELL),dict(reference_price=Decimal('101'))]:
            with self.subTest(changes=changes),self.assertRaises(TestnetSafetyError): self.submit(a,**changes)
        for changed in [replace(a,account_id='OTHER'),replace(a,venue_id='OTHER'),
                        replace(a,risk_profile_id='RESEARCH'),replace(a,risk_snapshot_hash='fake'),
                        replace(a,stop_price=Decimal('1')),replace(a,cost_assumptions=Decimal('0'))]:
            with self.subTest(approval=changed),self.assertRaises(TestnetSafetyError): self.submit(changed)
        self.assertEqual(self.transport.calls,0)
        self.assertEqual(self.fetch('SELECT count(*) FROM testnet_orders')[0][0],0)

    def test_expired_approval_uses_clock_not_forged_intent_timestamp(self):
        a=approve(self.g)
        self.now+=timedelta(seconds=31)
        with self.assertRaises(TestnetSafetyError): self.submit(a,created_at=NOW)
        with self.assertRaises(TestnetSafetyError): self.submit(a)
        self.assertEqual(self.transport.calls,0)

    def test_intent_failure_rolls_back_consumption_and_keeps_reservation(self):
        a=approve(self.g)
        with self.g._connect() as conn:
            conn.execute("ALTER TABLE testnet_orders ADD CONSTRAINT force_insert_failure CHECK (symbol='FORBIDDEN')")
        import psycopg
        with self.assertRaises(psycopg.errors.CheckViolation): self.submit(a)
        self.assertEqual(self.fetch('SELECT state FROM risk_approvals')[0][0],'ISSUED')
        self.assertEqual(self.fetch('SELECT state FROM risk_reservations')[0][0],'RESERVED')
        self.assertEqual(self.transport.calls,0)

    def test_two_processes_share_atomic_risk_budget(self):
        ctx=multiprocessing.get_context('spawn')
        event=ctx.Event();queue=ctx.Queue()
        workers=[ctx.Process(target=process_reserve,args=(self.dsn,event,queue,i)) for i in range(2)]
        for p in workers:p.start()
        try:
            event.set();results=[queue.get(timeout=20) for _ in workers]
            for p in workers:p.join(10);self.assertEqual(p.exitcode,0)
            self.assertCountEqual(results,['ACCEPTED','REJECTED'])
        finally:
            for p in workers:
                if p.is_alive():p.kill();p.join(10)
            queue.close();queue.join_thread()
        self.assertEqual(self.fetch('SELECT sum(notional) FROM risk_reservations')[0][0],Decimal('350'))

    def test_crash_preserves_committed_reservation(self):
        script="""import os,sys
from p0_fixtures import approve,governor
approve(governor(os.environ['P0_DSN']),cid='crash',quantity='3.5')
print('COMMITTED',flush=True)
sys.stdin.read()
"""
        proc=subprocess.Popen([sys.executable,'-c',script],env={**os.environ,'P0_DSN':self.dsn,
            'PYTHONPATH':os.pathsep.join(['tests',os.getcwd()])},stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            ready,_,_=select.select([proc.stdout],[],[],15)
            self.assertTrue(ready)
            self.assertEqual(proc.stdout.readline().strip(),'COMMITTED')
        finally:
            proc.kill();proc.wait(10)
            for f in (proc.stdin,proc.stdout,proc.stderr):f.close()
        with self.assertRaises(RiskAdmissionError): approve(governor(self.dsn),cid='post-crash',quantity='3.5')
        self.assertEqual(self.fetch('SELECT state FROM risk_reservations')[0][0],'RESERVED')

    def test_hard_latch_persists_across_process_restart_and_equity_recovery(self):
        self.observe(equity='940',cash='940')
        self.observe()
        script="""import os
from datetime import timedelta
from p0_fixtures import approve,governor,NOW
from research_bot.durable_risk import RiskAdmissionError
try: approve(governor(os.environ['P0_DSN']),cid='restart',now=NOW+timedelta(seconds=2))
except RiskAdmissionError as exc:
    assert 'DURABLE_NEW_ENTRY_HALT' in str(exc)
    print('HALT_PERSISTED')
else: raise AssertionError('restart cleared halt')
"""
        proc=subprocess.run([sys.executable,'-c',script],env={**os.environ,'P0_DSN':self.dsn,
            'PYTHONPATH':os.pathsep.join(['tests',os.getcwd()])},capture_output=True,text=True,timeout=20)
        self.assertEqual(proc.returncode,0,proc.stderr)
        self.assertIn('HALT_PERSISTED',proc.stdout)
        with self.assertRaises(RiskAdmissionError):
            self.g.release_hard_halt(account_id=ACCOUNT,venue_id=VENUE,authorization='',expected_version=0,now=self.now)
        self.g.release_hard_halt(account_id=ACCOUNT,venue_id=VENUE,authorization='fixture:operator-release',expected_version=0,now=self.now)
        # Daily latch also persists until valid UTC rollover.
        with self.assertRaises(RiskAdmissionError): approve(governor(self.dsn),now=self.now)

    def test_daily_latch_rollover_requires_fresh_reconciled_utc_open(self):
        self.observe(equity='980',cash='980')
        self.observe()
        with self.assertRaises(RiskAdmissionError): approve(self.g,now=self.now)
        self.now=NOW+timedelta(days=1)
        self.g.observe(observation(now=self.now),received_at=self.now)
        self.assertTrue(approve(self.g,now=self.now).approved)

    def test_daily_latch_rollover_stays_active_for_unresolved_submission(self):
        approval=approve(self.g)
        self.transport.timeout=True
        with self.assertRaises(AmbiguousTestnetSubmission): self.submit(approval)
        self.observe(equity='980',cash='980')
        self.assertEqual(self.fetch("SELECT state FROM risk_halts WHERE halt_type='DAILY'")[0][0],'ACTIVE')
        self.now=NOW+timedelta(days=1)
        self.g.observe(observation(now=self.now),received_at=self.now)
        self.assertEqual(self.fetch("SELECT state FROM risk_halts WHERE halt_type='DAILY'")[0][0],'ACTIVE')
        self.assertEqual(self.fetch('SELECT anchor_status,reconciled,unresolved_orders FROM risk_account_state')[0],
                         ('ANCHOR_UNVERIFIED',False,True))
        with self.assertRaisesRegex(RiskAdmissionError,'ANCHOR_UNVERIFIED'):
            approve(governor(self.dsn),cid='unsafe-entry',now=self.now)

    def test_daily_latch_rollover_stays_active_for_unresolved_fee(self):
        approval=approve(self.g)
        self.transport.snapshot=VenueOrderSnapshot(approval.client_order_id,'V',OrderLifecycle.FILLED,
                                                   Decimal('1'),Decimal('100'))
        self.submit(approval)
        self.observe(equity='980',cash='900',held='1')
        self.assertEqual(self.fetch("SELECT state FROM risk_halts WHERE halt_type='DAILY'")[0][0],'ACTIVE')
        self.now=NOW+timedelta(days=1)
        self.g.observe(observation(now=self.now,cash='900',held='1'),received_at=self.now)
        self.assertEqual(self.fetch("SELECT state FROM risk_halts WHERE halt_type='DAILY'")[0][0],'ACTIVE')
        self.assertEqual(self.fetch('SELECT anchor_status,reconciled,unresolved_orders FROM risk_account_state')[0],
                         ('ANCHOR_UNVERIFIED',False,True))
        with self.assertRaisesRegex(RiskAdmissionError,'ANCHOR_UNVERIFIED'):
            approve(governor(self.dsn),cid='unsafe-entry',now=self.now)

    def test_daily_latch_rollover_stays_active_when_order_store_missing(self):
        self.observe(equity='980',cash='980')
        with self.g._connect() as conn:
            conn.execute('DROP TABLE testnet_orders')
        self.now=NOW+timedelta(days=1)
        self.g.observe(observation(now=self.now),received_at=self.now)
        self.assertEqual(self.fetch("SELECT state FROM risk_halts WHERE halt_type='DAILY'")[0][0],'ACTIVE')
        self.assertEqual(self.fetch('SELECT anchor_status,reconciled,unresolved_orders FROM risk_account_state')[0],
                         ('ANCHOR_UNVERIFIED',False,True))

    def test_daily_rollover_cannot_revalidate_prior_unverified_anchor(self):
        self.observe(equity='980',cash='980',source='unverified:external')
        self.now=NOW+timedelta(days=1)
        self.g.observe(observation(now=self.now),received_at=self.now)
        self.assertEqual(self.fetch("SELECT state FROM risk_halts WHERE halt_type='DAILY'")[0][0],'ACTIVE')
        self.assertEqual(self.fetch('SELECT anchor_status FROM risk_account_state')[0][0],'ANCHOR_UNVERIFIED')

    def test_daily_rollover_stays_halted_with_venue_orphan_order(self):
        self.observe(equity='980',cash='980')
        intent=TestnetOrderIntent('orphan','BTC/USDT',OrderSide.BUY,Decimal('1'),Decimal('100'),self.now)
        order=ReconciledOrder('orphan','BTC/USDT','buy',Decimal('1'),updated_at=self.now)
        self.orders.create(venue=VENUE,intent=intent,order=order)
        self.now=NOW+timedelta(days=1)
        self.g.observe(observation(now=self.now),received_at=self.now)
        self.assertEqual(self.fetch("SELECT state FROM risk_halts WHERE halt_type='DAILY'")[0][0],'ACTIVE')
        self.assertEqual(self.fetch('SELECT anchor_status,reconciled,unresolved_orders FROM risk_account_state')[0],
                         ('ANCHOR_UNVERIFIED',False,True))

    def test_daily_rollover_stays_halted_with_account_orphan_reservation(self):
        approve(self.g,cid='reserved-before-halt')
        self.observe(equity='980',cash='980')
        self.now=NOW+timedelta(days=1)
        self.g.observe(observation(now=self.now),received_at=self.now)
        self.assertEqual(self.fetch("SELECT state FROM risk_halts WHERE halt_type='DAILY'")[0][0],'ACTIVE')
        self.assertEqual(self.fetch('SELECT anchor_status,reconciled,unresolved_orders FROM risk_account_state')[0],
                         ('ANCHOR_UNVERIFIED',False,True))

    def test_resolved_unfilled_venue_orphan_does_not_block_clean_rollover(self):
        self.observe(equity='980',cash='980')
        intent=TestnetOrderIntent('cancelled-orphan','BTC/USDT',OrderSide.BUY,Decimal('1'),Decimal('100'),self.now)
        order=ReconciledOrder('cancelled-orphan','BTC/USDT','buy',Decimal('1'),updated_at=self.now)
        self.orders.create(venue=VENUE,intent=intent,order=order)
        order=self.orders.update(replace(order,state=OrderLifecycle.SUBMITTING))
        self.orders.update(replace(order,state=OrderLifecycle.CANCELLED))
        self.now=NOW+timedelta(days=1)
        self.g.observe(observation(now=self.now),received_at=self.now)
        self.assertEqual(self.fetch("SELECT state FROM risk_halts WHERE halt_type='DAILY'")[0][0],'RELEASED')
        self.assertEqual(self.fetch('SELECT anchor_status,reconciled,unresolved_orders FROM risk_account_state')[0],
                         ('RECONCILED_TEST_FIXTURE',True,False))

    def test_anchor_unknown_source_stale_duplicate_hash_and_day_open(self):
        self.observe(source='unverified:external')
        with self.assertRaisesRegex(RiskAdmissionError,'ANCHOR_UNVERIFIED'):approve(self.g,now=self.now)
        o=observation(now=self.now+timedelta(seconds=1))
        digest=self.g.observe(o,received_at=o.observed_at)
        self.assertEqual(self.g.observe(o,received_at=o.observed_at),digest)
        with self.assertRaisesRegex(RiskAdmissionError,'HASH_CONFLICT'):
            self.g.observe(replace(o,equity=Decimal('1001'),cash=Decimal('1001')),received_at=o.observed_at)
        with self.assertRaises(RiskAdmissionError):
            self.g.observe(observation(now=NOW),received_at=NOW+timedelta(seconds=121))
        # A later apparently trusted observation cannot rescue an unverified day anchor.
        with self.assertRaisesRegex(RiskAdmissionError,'ANCHOR_UNVERIFIED'):approve(self.g,now=o.observed_at)

    def test_deposit_adjusts_anchors_without_fake_gain_and_receipt_reuse_rejected(self):
        self.observe(equity='1100',cash='1100',net='100',flows=('receipt-1',))
        self.assertEqual(self.fetch('SELECT day_equity,peak_equity FROM risk_account_state')[0],(Decimal('1100'),Decimal('1100')))
        with self.assertRaises(RiskAdmissionError):
            self.observe(equity='1200',cash='1200',net='200',flows=('receipt-1',))
        with self.assertRaises(RiskAdmissionError): self.observe(equity='1200',cash='1200',net='200')

    def test_timeout_retains_reservation_and_prevents_approval_reuse(self):
        a=approve(self.g)
        self.transport.timeout=True
        with self.assertRaises(AmbiguousTestnetSubmission):self.submit(a)
        with self.assertRaises(TestnetSafetyError):self.submit(a)
        self.assertEqual(self.transport.calls,1)
        self.assertEqual(self.fetch('SELECT state FROM risk_reservations')[0][0],'SUBMITTED')

    def test_account_observation_cannot_hide_an_unknown_submitted_order(self):
        a=approve(self.g)
        self.transport.timeout=True
        with self.assertRaises(AmbiguousTestnetSubmission):self.submit(a)
        # Even an otherwise trusted account snapshot cannot clear an OMS unknown.
        self.observe()
        with self.assertRaisesRegex(RiskAdmissionError,'ACCOUNT_NOT_RECONCILED'):
            approve(self.g,cid='unsafe-new',now=self.now)
        self.assertTrue(self.fetch('SELECT unresolved_orders FROM risk_account_state')[0][0])

    def test_two_workers_apply_persisted_fill_financially_once(self):
        a=approve(self.g)
        self.submit(a)
        local=self.orders.get(a.client_order_id)
        self.orders.update(replace(local,state=OrderLifecycle.FILLED,matched_quantity=Decimal('1'),average_price=Decimal('100')))
        barrier=Barrier(2,timeout=10)
        def settle(_):
            independent=governor(self.dsn)
            barrier.wait()
            independent.reconcile_accounting(account_id=ACCOUNT,venue_id=VENUE,client_order_id=a.client_order_id,now=self.now)
        with ThreadPoolExecutor(2) as pool:list(pool.map(settle,range(2)))
        self.assertEqual(self.fetch('SELECT cash FROM risk_account_state')[0][0],Decimal('900'))
        self.assertEqual(self.fetch("SELECT count(*) FROM risk_audit WHERE event='CUMULATIVE_FILL_APPLIED'")[0][0],1)

    def test_direct_store_fill_invalidates_previously_issued_approval(self):
        first = approve(self.g, cid='first')
        self.submit(first)
        # Respect the existing 15-minute entry cooldown; the regression is
        # about an intervening fill, not the independent cooldown veto.
        self.now += timedelta(minutes=16)
        self.observe()
        second = approve(self.g, cid='second', now=self.now)
        ctx = multiprocessing.get_context('spawn')
        result = ctx.Queue()
        worker = ctx.Process(target=process_direct_partial_fill, args=(self.dsn, first.client_order_id, result))
        worker.start()
        try:
            self.assertEqual(result.get(timeout=20), 'COMMITTED')
            worker.join(10)
            self.assertEqual(worker.exitcode, 0)
        finally:
            if worker.is_alive(): worker.kill(); worker.join(10)
            result.close(); result.join_thread()
        self.assertEqual(self.orders.get(first.client_order_id).matched_quantity, Decimal('0.5'))
        with self.assertRaises(TestnetSafetyError):
            self.submit(second)
        self.assertEqual(self.transport.calls, 1, 'stale approval must not reach even the fake transport')
        self.assertIsNone(self.orders.get(second.client_order_id))

    def test_concurrent_direct_order_mutation_waits_for_account_lock(self):
        from time import monotonic, sleep
        from uuid import uuid4
        first=approve(self.g,cid='first-lock')
        self.submit(first)
        self.now += timedelta(minutes=16)
        self.observe()
        second=approve(self.g,cid='second-lock',now=self.now)
        ctx=multiprocessing.get_context('spawn')
        ready=ctx.Queue();result=ctx.Queue()
        app='p0_order_lock_'+uuid4().hex
        worker=ctx.Process(target=process_blocking_order_update,
                           args=(self.dsn,first.client_order_id,app,ready,result))
        try:
            with self.g._connect() as conn:
                conn.execute('SELECT 1 FROM risk_account_state WHERE account_id=%s AND venue_id=%s FOR UPDATE',
                             (ACCOUNT,VENUE))
                worker.start()
                self.assertEqual(ready.get(timeout=20),'ATTEMPTING')
                deadline=monotonic()+15
                waited=False
                while monotonic()<deadline:
                    rows=self.fetch('SELECT wait_event_type FROM pg_stat_activity WHERE application_name=%s',
                                    (app,))
                    if rows and rows[0][0]=='Lock':
                        waited=True
                        break
                    sleep(.05)
                self.assertTrue(waited,'independent writer never waited on the locked account')
                self.assertEqual(self.orders.get(first.client_order_id).matched_quantity,Decimal('0'))
            self.assertEqual(result.get(timeout=20),'COMMITTED')
            worker.join(10)
            self.assertEqual(worker.exitcode,0)
            self.assertEqual(self.fetch('SELECT reconciled FROM risk_account_state')[0][0],False)
            with self.assertRaises(TestnetSafetyError):
                self.submit(second)
            self.assertEqual(self.transport.calls,1)
        finally:
            if worker.is_alive(): worker.kill();worker.join(10)
            ready.close();ready.join_thread();result.close();result.join_thread()

    def test_account_version_change_invalidates_approval_even_if_snapshot_hash_is_unchanged(self):
        approval=approve(self.g)
        before=self.fetch('SELECT snapshot_hash,state_version FROM risk_account_state')[0]
        with self.g._connect() as conn:
            # A direct writer omits a version bump; the DB must advance it.
            conn.execute('UPDATE risk_account_state SET cash=cash WHERE account_id=%s AND venue_id=%s',
                         (ACCOUNT,VENUE))
        after=self.fetch('SELECT snapshot_hash,state_version FROM risk_account_state')[0]
        self.assertEqual(before[0],after[0])
        self.assertEqual(after[1],before[1]+1)
        with self.assertRaisesRegex(TestnetSafetyError,'STALE_ACCOUNT_VERSION_REAPPROVAL_REQUIRED'):
            self.submit(approval)
        self.assertEqual(self.transport.calls,0)
        self.assertEqual(self.fetch('SELECT state FROM risk_approvals')[0][0],'ISSUED')

    def test_direct_reservation_update_invalidates_approval_and_account(self):
        approval=approve(self.g)
        with self.g._connect() as conn:
            conn.execute("UPDATE risk_reservations SET notional=notional WHERE reservation_id=%s",
                         (approval.reservation_id,))
        self.assertEqual(self.fetch('SELECT reconciled,unresolved_orders FROM risk_account_state')[0],(False,True))
        with self.assertRaises(TestnetSafetyError):
            self.submit(approval)
        self.assertEqual(self.transport.calls,0)

    def test_disabled_order_invalidation_trigger_blocks_approval_consumption(self):
        approval=approve(self.g)
        with self.g._connect() as conn:
            conn.execute('ALTER TABLE testnet_orders DISABLE TRIGGER testnet_order_risk_invalidation')
        with self.assertRaisesRegex(TestnetSafetyError,'FINANCIAL_MUTATION_GUARDS_UNAVAILABLE'):
            self.submit(approval)
        self.assertEqual(self.transport.calls,0)
        self.assertEqual(self.fetch('SELECT state FROM risk_approvals')[0][0],'ISSUED')

    def test_crash_rolls_back_order_and_transactional_account_invalidation(self):
        approval=approve(self.g)
        self.submit(approval)
        self.observe()
        before_order=self.orders.get(approval.client_order_id)
        account_sql='SELECT reconciled,unresolved_orders,state_version FROM risk_account_state'
        before_account=self.fetch(account_sql)
        ctx=multiprocessing.get_context('spawn')
        ready=ctx.Queue(); release=ctx.Event()
        worker=ctx.Process(target=process_uncommitted_order_update,
                           args=(self.dsn,approval.client_order_id,ready,release))
        worker.start()
        try:
            self.assertEqual(ready.get(timeout=20),'UPDATED_BEFORE_COMMIT')
            worker.kill(); worker.join(10)
            self.assertIsNotNone(worker.exitcode)
        finally:
            if worker.is_alive(): worker.kill(); worker.join(10)
            ready.close(); ready.join_thread()
        self.assertEqual(self.orders.get(approval.client_order_id),before_order)
        self.assertEqual(self.fetch(account_sql),before_account)

    def test_duplicate_unresolved_fee_reconciliation_keeps_persisted_state_unchanged(self):
        approval=approve(self.g)
        self.submit(approval)
        local=self.orders.get(approval.client_order_id)
        filled=replace(local,state=OrderLifecycle.FILLED,matched_quantity=Decimal('1'),average_price=Decimal('100'))
        self.g.persist_snapshot_and_account(store=self.orders,order=filled,account_id=ACCOUNT,venue_id=VENUE,now=self.now)
        account_sql='SELECT cash,positions,reconciled,unresolved_orders,state_version FROM risk_account_state'
        fee_sql='SELECT state,updated_at FROM risk_fee_liabilities'
        reservation_sql='SELECT matched_quantity,cumulative_fee,cumulative_quote,state FROM risk_reservations'
        audit_sql="SELECT count(*) FROM risk_audit WHERE event='CUMULATIVE_FILL_APPLIED'"
        before=(self.fetch(account_sql),self.fetch(fee_sql),self.fetch(reservation_sql),self.fetch(audit_sql))
        restarted=governor(self.dsn)
        restarted.reconcile_accounting(account_id=ACCOUNT,venue_id=VENUE,client_order_id=approval.client_order_id,
            now=self.now+timedelta(minutes=1))
        after=(self.fetch(account_sql),self.fetch(fee_sql),self.fetch(reservation_sql),self.fetch(audit_sql))
        self.assertEqual(before,after)
        self.assertEqual(before[0][0][:4],(Decimal('900'),{'BTC/USDT':'1'},False,True))
        self.assertEqual(before[1][0][0],'UNRESOLVED')
        self.assertEqual(before[3][0][0],1)

    def test_unknown_fee_principal_posted_once_by_independent_processes(self):
        a=approve(self.g)
        self.submit(a)
        local=self.orders.get(a.client_order_id)
        self.orders.update(replace(local,state=OrderLifecycle.FILLED,matched_quantity=Decimal('1'),average_price=Decimal('100')))
        ctx=multiprocessing.get_context('spawn')
        start=ctx.Barrier(2,timeout=20)
        result=ctx.Queue()
        workers=[ctx.Process(target=process_post_principal,args=(self.dsn,start,result,a.client_order_id)) for _ in range(2)]
        for worker in workers: worker.start()
        for worker in workers:
            worker.join(30)
            self.assertEqual(worker.exitcode,0)
        self.assertEqual(sorted(result.get(timeout=5) for _ in workers),['OK','OK'])
        self.assertEqual(self.fetch('SELECT cash,positions,reconciled FROM risk_account_state')[0],
                         (Decimal('900'),{'BTC/USDT':'1'},False))
        self.assertEqual(self.fetch('SELECT count(*) FROM risk_principal_postings')[0][0],1)
        self.assertEqual(self.fetch('SELECT state FROM risk_fee_liabilities')[0][0],'UNRESOLVED')
        with self.assertRaisesRegex(RiskAdmissionError,'UNRESOLVED_FEE_ACCOUNT_MISMATCH'):
            self.observe(held='1',cash='899')
        self.observe(held='1',cash='900')
        self.assertEqual(self.fetch('SELECT reconciled,unresolved_orders FROM risk_account_state')[0],(False,True))
        with self.assertRaisesRegex(RiskAdmissionError,'ACCOUNT_NOT_RECONCILED'):
            approve(self.g,cid='unresolved-fee-entry',now=self.now)

    def test_crash_after_snapshot_before_post_rolls_back_both(self):
        a=approve(self.g)
        self.submit(a)
        ctx=multiprocessing.get_context('spawn')
        worker=ctx.Process(target=process_crash_after_snapshot_before_post,args=(self.dsn,a.client_order_id))
        worker.start()
        worker.join(30)
        self.assertEqual(worker.exitcode,77)
        local=self.orders.get(a.client_order_id)
        self.assertEqual(local.matched_quantity,Decimal('0'))
        self.assertEqual(self.fetch('SELECT cash FROM risk_account_state')[0][0],Decimal('1000'))
        self.assertEqual(self.fetch('SELECT count(*) FROM risk_principal_postings')[0][0],0)
        filled=replace(local,state=OrderLifecycle.FILLED,matched_quantity=Decimal('1'),average_price=Decimal('100'))
        self.g.persist_snapshot_and_account(store=self.orders,order=filled,account_id=ACCOUNT,venue_id=VENUE,now=self.now)
        self.assertEqual(self.fetch('SELECT cash FROM risk_account_state')[0][0],Decimal('900'))
        self.assertEqual(self.fetch('SELECT count(*) FROM risk_principal_postings')[0][0],1)

    def test_account_snapshot_cannot_double_count_an_unapplied_fill(self):
        a=approve(self.g)
        self.submit(a)
        local=self.orders.get(a.client_order_id)
        self.orders.update(replace(local,state=OrderLifecycle.FILLED,matched_quantity=Decimal('1'),average_price=Decimal('100')))
        with self.assertRaisesRegex(RiskAdmissionError,'PENDING_FILL_ACCOUNTING_REQUIRED'):
            self.observe(held='1',cash='900')
        self.gateway.reconcile(a.client_order_id)
        self.observe(held='1',cash='900')
        self.gateway.reconcile(a.client_order_id)
        self.assertEqual(self.fetch('SELECT cash,positions FROM risk_account_state')[0],(Decimal('900'),{'BTC/USDT':'1'}))

    def test_partial_fill_settlement_is_exact_idempotent_and_terminal_recoverable(self):
        a=approve(self.g)
        self.submit(a)
        self.transport.snapshot=VenueOrderSnapshot(a.client_order_id,'V',OrderLifecycle.PARTIAL,Decimal('.25'),Decimal('100'),Decimal('.01'),'USDT')
        self.gateway.reconcile(a.client_order_id)
        self.gateway.reconcile(a.client_order_id)
        self.assertEqual(self.fetch('SELECT cash FROM risk_account_state')[0][0],Decimal('974.99'))
        self.assertEqual(self.fetch('SELECT matched_quantity,state FROM risk_reservations')[0],(Decimal('.25'),'PARTIAL'))
        with self.assertRaises(RiskAdmissionError):approve(self.g,cid='unsafe-entry')
        # Simulate crash after OMS persisted final fill but before financial apply.
        local=self.orders.get(a.client_order_id)
        final=replace(local,state=OrderLifecycle.FILLED,matched_quantity=Decimal('1'),average_price=Decimal('100'),fee_reported=Decimal('.04'))
        self.orders.update(final)
        restarted=gateway(self.transport,dsn=self.dsn)
        restarted.reconcile(a.client_order_id);restarted.reconcile(a.client_order_id)
        self.assertEqual(self.fetch('SELECT cash FROM risk_account_state')[0][0],Decimal('899.96'))
        self.assertEqual(self.fetch('SELECT state FROM risk_reservations')[0][0],'CLOSED')

    def test_reduction_under_halts_and_shared_sell_reservations(self):
        self.observe(held='1',cash='900')
        self.observe(equity='940',cash='840',held='1')
        with self.assertRaises(RiskAdmissionError):approve(self.g,cid='BUY-HALTED',now=self.now)
        a=approve(self.g,cid='EXIT',side='SELL',quantity='.75',now=self.now)
        with self.assertRaises(RiskAdmissionError):approve(self.g,cid='OVER-EXIT',side='SELL',quantity='.3',now=self.now)
        self.transport.snapshot=VenueOrderSnapshot('EXIT','V',OrderLifecycle.FILLED,Decimal('.75'),Decimal('100'))
        self.submit(a)
        self.assertEqual(self.fetch('SELECT positions FROM risk_account_state')[0][0]['BTC/USDT'],'0.25')

    def test_migration_adds_version_without_rewriting_numeric_or_legacy_rows(self):
        q=Decimal('0.123456789012345678')
        intent=TestnetOrderIntent('legacy','BTC/USDT',OrderSide.BUY,q,Decimal('100'),self.now)
        self.orders.create(venue=VENUE,intent=intent,order=ReconciledOrder('legacy','BTC/USDT','buy',q,state=OrderLifecycle.ACKNOWLEDGED,updated_at=self.now))
        with self.g._connect() as conn:
            conn.execute('ALTER TABLE testnet_orders DROP COLUMN state_version')
        # Additive migration may run repeatedly; no reset of account or halt state.
        self.orders.ensure_schema();self.orders.ensure_schema();self.g.ensure_schema()
        self.assertEqual(self.orders.get('legacy').requested_quantity,q)
        self.assertEqual(self.orders.get('legacy').state_version,0)
        # The legacy order has no account/reservation identity. Migration must
        # preserve it AND prevent admission until its state is reconciled.
        with self.assertRaisesRegex(RiskAdmissionError,'ACCOUNT_NOT_RECONCILED'):
            approve(self.g,quantity=str(q))
        legacy=self.orders.get('legacy')
        self.orders.update(replace(legacy,state=OrderLifecycle.CANCELLED))
        self.observe()
        self.submit(approve(self.g,quantity=str(q),now=self.now))
        self.assertEqual(self.orders.get('precision').requested_quantity,q)
        self.assertEqual(self.fetch('SELECT peak_equity FROM risk_account_state')[0][0],Decimal('1000'))

    def test_boolean_risk_decision_cannot_bypass_gateway(self):
        from research_bot.risk import RiskDecision
        with self.assertRaisesRegex(TestnetSafetyError,'DURABLE_BOUND_APPROVAL_REQUIRED'):
            self.gateway.submit_market(client_order_id='bypass',symbol='BTC/USDT',side=OrderSide.BUY,
                quantity=Decimal('10'),reference_price=Decimal('100'),precision=self.precision,
                risk_decision=RiskDecision(True,False,(),None),created_at=self.now)
        self.assertEqual(self.transport.calls,0)



