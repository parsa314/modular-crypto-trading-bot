"""Required PG service suite: actual PostgreSQL, Fake exchange only.

Missing DSN skips local offline runs; the dedicated CI job requires this DSN
and records zero skips. Never set it to an existing production database.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import select
import sqlite3
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
import threading

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from research_bot.execution.ledger import LiveLedger
from research_bot.execution.oms import OrderManagementSystem
from research_bot.execution.postgres_ledger import PostgresLedger
from research_bot.execution.postgres_migration import migrate_sqlite
from test_convergence_oms import setup


DSN = os.environ.get('CONVERGENCE_TEST_DATABASE_URL', '')
pytestmark = pytest.mark.skipif(not DSN, reason='isolated CONVERGENCE_TEST_DATABASE_URL required')


@pytest.fixture
def journal():
    # These tests deliberately create/clean fixtures. Prevent accidental remote
    # DB use; deployment acceptance is a separate, still-unverified phase.
    info = conninfo_to_dict(DSN)
    assert info.get('dbname') == 'convergence_test'
    assert info.get('host') in {'127.0.0.1', 'localhost'}
    name = 'test-' + uuid.uuid4().hex
    identity = {'mode': 'ENGINEERING_REPLAY', 'account_id': name, 'schema': 'CONVERGENCE_OMS_V1'}
    yield name, identity
    with psycopg.connect(DSN) as conn:
        for table in ('convergence_fill_revisions', 'convergence_orders', 'convergence_decisions',
                      'convergence_positions', 'convergence_heartbeats', 'convergence_candles', 'convergence_account_state'):
            conn.execute(f'DELETE FROM {table} WHERE journal_id=%s', (name,))
        conn.execute('DELETE FROM convergence_journals WHERE journal_id=%s', (name,))


def open_pg(journal):
    return PostgresLedger(DSN, *journal)


def test_atomic_receipt_account_positions_and_duplicate_revision(journal):
    with open_pg(journal) as db:
        assert db.claim_order('entry', {'side': 'buy'})
        assert not db.claim_order('entry', {'side': 'buy'})
        initial, next_state = {'cash':100.}, {'cash':49.,'positions':{'BTC/USDT':1.}}
        db.set_state(initial)
        with pytest.raises(RuntimeError, match='state changed'):
            db.update_order('entry', 'FILLED', {'filled':1.}, account_state=next_state, expected_account_state={})
        assert db.get_order('entry')['state'] == 'SUBMITTING'
        assert db.get_state() == initial
        db.update_order('entry', 'FILLED', {'filled':1.,'cost':50.,'fee_quote':1.}, account_state=next_state, expected_account_state=initial)
        db.update_order('entry', 'FILLED', {'filled':1.,'cost':50.,'fee_quote':1.})
        assert db.get_state() == next_state
        with psycopg.connect(DSN) as conn:
            assert conn.execute('SELECT quantity FROM convergence_positions WHERE journal_id=%s', (journal[0],)).fetchone()[0] == 1
            assert conn.execute('SELECT COUNT(*) FROM convergence_fill_revisions WHERE journal_id=%s', (journal[0],)).fetchone()[0] == 1
        with pytest.raises(ValueError, match='cannot decrease'):
            db.update_order('entry', 'FILLED', {'filled':.5})
        assert db.get_order('entry')['result']['filled'] == 1.


def test_transaction_rollback_after_order_update(journal):
    with open_pg(journal) as db:
        db.claim_order('entry', {})
        # _write_state rejects after the order and fill revision INSERT ran.
        with pytest.raises(ValueError, match='position quantity'):
            db.update_order('entry', 'FILLED', {'filled':1.}, account_state={'positions':{'BTC/USDT':-1.}}, expected_account_state={})
        assert db.get_order('entry')['state'] == 'SUBMITTING'
        assert db.get_state() == {}
        with psycopg.connect(DSN) as conn:
            assert conn.execute('SELECT COUNT(*) FROM convergence_fill_revisions WHERE journal_id=%s', (journal[0],)).fetchone()[0] == 0


def test_identity_and_account_pinning(journal):
    with open_pg(journal) as db:
        with pytest.raises(ValueError, match='identity'):
            PostgresLedger(DSN, journal[0], {**journal[1], 'model':'changed'})
        with pytest.raises(psycopg.errors.UniqueViolation):
            PostgresLedger(DSN, journal[0]+'other', journal[1])
        assert db.identity == journal[1]


def test_changed_schema_source_is_rejected_and_original_remains_usable(tmp_path, journal, monkeypatch):
    from research_bot.execution import postgres_ledger
    with open_pg(journal):
        pass
    changed = tmp_path/'changed.sql'
    changed.write_text(postgres_ledger.SCHEMA_PATH.read_text()+'\n-- unreviewed schema change\n')
    with monkeypatch.context() as patch:
        patch.setattr(postgres_ledger,'SCHEMA_PATH',changed)
        with pytest.raises(ValueError, match='schema version/hash'):
            open_pg(journal)
    with open_pg(journal) as db:
        assert db.get_state() == {}


def test_advisory_lock_blocks_second_connection_and_is_released(journal):
    with open_pg(journal) as a, open_pg(journal) as b:
        with a.session_lock():
            with pytest.raises(RuntimeError, match='another runner'):
                with b.session_lock():
                    pytest.fail('second executor acquired the lock')
            assert a.claim_decision('decision', {'public':True})
        with b.session_lock():
            assert not b.claim_decision('decision', {'public':True})


def test_two_connections_race_for_one_durable_reservation(journal):
    with open_pg(journal):
        pass
    barrier = threading.Barrier(2)
    def claim():
        with open_pg(journal) as db:
            barrier.wait(timeout=5)
            return db.claim_order('same-event', {'public':True})
    with ThreadPoolExecutor(max_workers=2) as workers:
        a, b = workers.submit(claim), workers.submit(claim)
        assert sorted([a.result(timeout=10), b.result(timeout=10)]) == [False, True]


@pytest.mark.parametrize('payload', [{'secret':'bad'}, {'nested':{'api_key':'bad'}}, {'value':float('nan')}, {'value':float('inf')}])
def test_nonfinite_and_credentials_rejected_before_write(journal, payload):
    with open_pg(journal) as db:
        with pytest.raises(ValueError):
            db.set_state(payload)
        assert db.get_state() == {}


def test_closed_or_lost_database_connection_does_not_reconnect(journal):
    with open_pg(journal) as db:
        pid = db._connection.info.backend_pid
        with psycopg.connect(DSN, autocommit=True) as killer:
            assert killer.execute('SELECT pg_terminate_backend(%s)', (pid,)).fetchone()[0]
        with pytest.raises((RuntimeError, psycopg.Error)):
            db.claim_order('must-not-be-reserved', {})
    with open_pg(journal) as restored:
        assert restored.get_order('must-not-be-reserved') is None


def test_sigkill_preserves_commit_rolls_back_inflight_and_releases_lock(journal):
    with open_pg(journal):
        pass
    script = '''
import os, sys, time, json
from research_bot.execution.postgres_ledger import PostgresLedger
db=PostgresLedger(os.environ['CONVERGENCE_TEST_DATABASE_URL'], sys.argv[1], json.loads(sys.argv[2]))
with db.session_lock():
 db.claim_order('durable-reservation', {})
 db.set_state({'cash':100.})
 with db._transaction() as conn:
  conn.execute('UPDATE convergence_account_state SET state_json=%s WHERE journal_id=%s', ('{"cash":0.0}', db.journal_id))
  print('UNCOMMITTED_READY', flush=True)
  time.sleep(30)
'''
    child = subprocess.Popen([sys.executable, '-c', script, journal[0], json.dumps(journal[1])],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([child.stdout], [], [], 10)[0], 'child did not reach transaction'
        assert child.stdout.readline().strip() == 'UNCOMMITTED_READY'
        child.kill()
        child.wait(timeout=10)
        assert child.returncode < 0
        with open_pg(journal) as restored, restored.session_lock():
            assert restored.get_state() == {'cash':100.}
            assert restored.get_order('durable-reservation')['state'] == 'SUBMITTING'
            assert not restored.claim_order('durable-reservation', {})
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)


def pg_oms(tmp_path, journal):
    *_, sealed, gateway, sqlite, kwargs = setup(tmp_path)
    # Construct the bridge with the account identity it signed; use a distinct
    # PG journal resource but retain the fixture account from setup.
    sqlite.close()
    # Every test PG fixture has a distinct account; re-sign the immutable intent
    # rather than changing its MAC/identity behind the OMS.
    from dataclasses import replace
    intent = replace(sealed.intent, account_id=journal[0])
    sealed = kwargs['authority'].seal(intent)
    kwargs['account_id'] = journal[0]
    identity = OrderManagementSystem.identity(journal[0], kwargs['manifest'])
    return sealed, gateway, kwargs, (journal[0], identity)


@pytest.mark.parametrize('scenario', ['normal', 'partial', 'entry-ack-lost', 'protection-ack-lost', 'protection-absent'])
def test_v59_bridge_generic_oms_pg_reopen_and_protection(tmp_path, journal, scenario):
    sealed, gateway, kwargs, spec = pg_oms(tmp_path, journal)
    gateway.fill_fraction = .3 if scenario == 'partial' else 1.
    gateway.fail_after_submit = scenario == 'entry-ack-lost'
    gateway.fail_protection_after = scenario == 'protection-ack-lost'
    gateway.fail_protection_before = scenario == 'protection-absent'
    with open_pg(spec) as db:
        OrderManagementSystem(ledger=db, **kwargs).process(sealed)
    gateway.fail_after_submit = gateway.fail_protection_after = gateway.fail_protection_before = False
    with open_pg(spec) as restored:
        OrderManagementSystem(ledger=restored, **kwargs).process(sealed)
        if scenario == 'protection-absent':
            assert restored.get_state()['halt_reason'] == 'POSITION_UNPROTECTED'
            kwargs['clock'] = lambda: sealed.intent.valid_from+timedelta(seconds=6)
            OrderManagementSystem(ledger=restored, **kwargs).process(sealed)
            assert restored.get_state()['positions'] == gateway.positions == {}
            assert restored.get_state()['halt_reason'] == 'PROTECTION_EMERGENCY_HALT'
            assert gateway.flatten_calls == 1
        else:
            record = restored.get_state()['protection'][sealed.intent.client_order_id]
            assert record['state'] == 'POSITION_PROTECTED'
            assert record['quantity'] == gateway.positions[sealed.intent.symbol]
            assert restored.get_state()['cash'] == pytest.approx(gateway.cash)
        assert gateway.submit_calls == 1
        assert gateway.protection_submit_calls == 1


def test_heartbeat_aware_monotone_and_causal_candle_parity(journal):
    now = datetime(2026,10,5,tzinfo=timezone.utc)
    with open_pg(journal) as db:
        db.heartbeat(now, {'status':'ENGINEERING_REPLAY'})
        with pytest.raises(ValueError, match='advance'):
            db.heartbeat(now, {})
        with pytest.raises(ValueError, match='aware'):
            db.heartbeat(now.replace(tzinfo=None), {})
        candle = {'timestamp_ms':0,'open':10.,'high':12.,'low':9.,'close':11.,'volume':3.}
        db.append_candles([candle], 60_000)
        db.append_candles([candle], 60_000)
        with pytest.raises(ValueError, match='revised'):
            db.append_candles([{**candle, 'volume':4.}], 60_000)
        with pytest.raises(ValueError, match='gaps'):
            db.append_candles([{**candle, 'timestamp_ms':120_000}], 60_000)
        assert db.candles() == [candle]


def test_migration_preserves_pending_state_history_and_source(tmp_path, journal):
    path = tmp_path/'source.sqlite'
    with LiveLedger(path, journal[1]) as source:
        source.claim_decision('decision', {'event':'event-a'})
        source.claim_order('pending', {'symbol':'BTC/USDT'})
        source.update_order('pending', 'UNKNOWN', {'filled':.2})
        source.set_state({'cash':80.,'positions':{'BTC/USDT':.2},'halt_reason':'ORDER_AMBIGUOUS'})
        source.append_candles([{'timestamp_ms':0,'open':10.,'high':12.,'low':9.,'close':11.,'volume':3.}], 60_000)
        expected_state, expected_orders = source.get_state(), source.pending_orders()
    before = path.read_bytes()
    with open_pg(journal) as target:
        report = migrate_sqlite(path, target)
        assert report['status'] == 'IMPORTED'
        assert target.get_state() == expected_state
        assert target.pending_orders() == expected_orders
        assert migrate_sqlite(path, target)['status'] == 'ALREADY_IMPORTED'
        assert not target.claim_decision('decision', {})
    assert path.read_bytes() == before
    with LiveLedger(path, journal[1]) as source:
        source.set_state({'cash':79.})
    with open_pg(journal) as target:
        with pytest.raises(ValueError, match='source changed'):
            migrate_sqlite(path, target)
        assert target.get_state() == expected_state


def test_migration_failure_rolls_back_every_target_record(tmp_path, journal):
    path = tmp_path/'source.sqlite'
    with LiveLedger(path, journal[1]) as source:
        source.claim_decision('decision', {})
        source.claim_order('entry', {})
        # Source allows public arbitrary state; PG projection must reject this.
        source.set_state({'positions':{'BTC/USDT':-1.}})
    with open_pg(journal) as target:
        with pytest.raises(ValueError, match='position quantity'):
            migrate_sqlite(path, target)
        assert target.get_order('entry') is None
        assert target.get_state() == {}
        assert target.claim_decision('decision', {})


def test_migration_refuses_running_source_nonempty_target_and_wrong_identity(tmp_path, journal):
    path = tmp_path/'source.sqlite'
    with LiveLedger(path, journal[1]) as source, open_pg(journal) as target:
        with source.session_lock(), pytest.raises(RuntimeError, match='stop the SQLite executor'):
            migrate_sqlite(path, target)
        target.set_state({'cash':1.})
        with pytest.raises(ValueError, match='empty'):
            migrate_sqlite(path, target)


def test_migration_wrong_identity_and_duplicate_source_json_fail_closed(tmp_path, journal):
    path = tmp_path/'wrong.sqlite'
    with LiveLedger(path, {**journal[1], 'model':'wrong'}):
        pass
    with open_pg(journal) as target:
        with pytest.raises(ValueError, match='identity mismatch'):
            migrate_sqlite(path, target)
        assert target.get_state() == {}
    path = tmp_path/'corrupt.sqlite'
    with LiveLedger(path, journal[1]) as source:
        source.claim_order('entry', {})
    with sqlite3.connect(path) as corrupt:
        corrupt.execute('UPDATE live_orders SET intent_json=?', ('{"event":1,"event":2}',))
    with open_pg(journal) as target:
        with pytest.raises(ValueError, match='duplicate JSON'):
            migrate_sqlite(path, target)
        assert target.get_order('entry') is None


def test_causal_market_features_v59_to_pg_protected_position(tmp_path, journal):
    from dataclasses import replace
    import pandas as pd
    from test_phase1_end_to_end import FakeMarketData
    from research_bot.execution.intent import IntentAuthority, digest
    from research_bot.execution.replay_exchange import EngineeringReplayExchange
    from research_bot.v59.native_features import add_native_features
    from research_bot.v59.native_strategy import MODEL_FEATURES
    from research_bot.v59.contracts import PortfolioState, UncertaintyAssessment
    from research_bot.v59.orchestrator import V59DecisionOrchestrator
    from research_bot.v59.convergence_replay import fixture_decision, fixture_manifest, fixture_promotion_gate
    from research_bot.v59.production_adapter import adapt_research_decision
    bars = FakeMarketData().closed_bars()
    features = add_native_features(bars)
    snapshot = {name:float(features.iloc[-1][name]) for name in MODEL_FEATURES}
    available_at = bars.timestamp.iloc[-1]+pd.Timedelta(hours=1)
    _, _, candidate, prediction = fixture_decision()
    candidate = replace(candidate, decision_at=available_at.to_pydatetime(),
        entry_time=(available_at+pd.Timedelta(hours=1)).to_pydatetime(),
        event_id=digest({'engineering_market_event':available_at.isoformat(),'snapshot':snapshot}),
        feature_snapshot_id=digest(snapshot))
    prediction = replace(prediction,event_id=candidate.event_id,available_at=candidate.decision_at)
    engine = V59DecisionOrchestrator()
    final = engine.evaluate(candidate=candidate,prediction=prediction,
        uncertainty=UncertaintyAssessment(candidate.event_id,('TP',),.9,.9,False,'FIXTURE_NOT_SCIENTIFIC'),
        portfolio=PortfolioState(candidate.entry_time,10000.,10000.,10000.,0.,{},(.001,)*30))
    manifest, authority = fixture_manifest(), IntentAuthority(b'engineering-fixture-only-no-live'*2)
    gate = fixture_promotion_gate(manifest)
    sealed = adapt_research_decision(engine=engine,final=final,candidate=candidate,prediction=prediction,
        manifest=manifest,authority=authority,account_id=journal[0],timeframe='1h',now=candidate.entry_time,promotion_gate=gate)
    gateway = EngineeringReplayExchange(venue='fixture',quotes={candidate.symbol:
        {'bid':100.,'ask':100.,'timestamp':candidate.entry_time}})
    spec = journal[0], OrderManagementSystem.identity(journal[0],manifest)
    with open_pg(spec) as db:
        result = OrderManagementSystem(gateway=gateway,ledger=db,manifest=manifest,authority=authority,
            account_id=journal[0],clock=lambda:candidate.entry_time,recent_returns=(.001,)*30,promotion_gate=gate).process(sealed)
        assert result['status']=='FILLED' and not result['execution_authorized']
        assert db.get_state()['protection'][sealed.intent.client_order_id]['state']=='POSITION_PROTECTED'
        assert db.identity['risk_hash']==final.risk_constitution_hash==sealed.intent.risk_hash
    engine.ledger.write_immutable(tmp_path/'pg-causal-decision-evidence.json')
    assert engine.ledger.verify()
