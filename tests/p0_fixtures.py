"""Explicit synthetic fixtures; PostgreSQL fixture never uses DATABASE_URL."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
from urllib.parse import urlparse
from uuid import uuid4

from research_bot.durable_risk import AccountObservation, PostgresRiskGovernor, RiskApproval, RiskAdmissionError
from research_bot.live_readiness import LiveReadinessChecklist, evaluate_live_readiness
from research_bot.testnet_execution import PostgresTestnetOrderStore, TestnetExecutionGateway

NOW = datetime(2026,10,30,0,0,tzinfo=timezone.utc)
ACCOUNT = 'SYNTHETIC_ACCOUNT'
VENUE = 'SYNTHETIC_NO_BROKER'
SOURCE = 'fixture:explicit-reconciliation'


def readiness():
    return evaluate_live_readiness(LiveReadinessChecklist(scientific_promotion_passed=True,
        forward_paper_reconciled=True,order_reconciliation_validated=True,risk_constitution_unified=True))


def pg_schema():
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    root = os.environ.get('TEST_DATABASE_URL','')
    parsed = urlparse(root)
    if os.environ.get('NEXT_GATE_ISOLATED_DATABASE') != '1' or parsed.hostname not in {'127.0.0.1','localhost'} or parsed.path != '/execution_v52':
        raise RuntimeError('Explicit disposable execution_v52 required; no skip')
    schema = 'p0_' + uuid4().hex
    with psycopg.connect(root,autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    return root, schema, make_conninfo(root,options='-c search_path='+schema)


def drop_schema(root, schema):
    import re
    import psycopg
    from psycopg import sql
    assert re.fullmatch(r'p0_[0-9a-f]{32}',schema)
    with psycopg.connect(root,autocommit=True) as conn:
        conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def governor(dsn):
    return PostgresRiskGovernor(dsn,accepted_fixture_sources=frozenset({SOURCE}))


def observation(*, now=NOW, equity='1000', cash='1000', held='0', account=ACCOUNT,
                venue=VENUE, source=SOURCE, net='0', flows=(), unresolved=False):
    gross = Decimal(equity)-Decimal(cash)
    return AccountObservation(account,venue,str(uuid4()),source,now,Decimal(equity),Decimal(cash),gross,
        {'BTC/USDT':gross},{'BTC/USDT':Decimal(held)},Decimal(net),True,unresolved,flows)


def seed(dsn, *, venue=VENUE, held='0', equity='1000', cash='1000', now=NOW, account=ACCOUNT):
    g=governor(dsn);g.ensure_schema()
    g.observe(observation(venue=venue,held=held,equity=equity,cash=cash,now=now,account=account),received_at=now)
    return g


def approve(g, *, cid='precision', quantity='1', side='BUY', venue=VENUE, now=NOW,
            price='100', stop='99.9', account=ACCOUNT):
    return g.approve(account_id=account,venue_id=venue,client_order_id=cid,symbol='BTC/USDT',side=side,
        quantity=Decimal(quantity),reference_price=Decimal(price),stop_price=Decimal(stop),cost_bps=Decimal('50'),now=now,quote_at=now)


def gateway(transport, *, dsn, clock=lambda:NOW):
    orders=PostgresTestnetOrderStore(dsn);orders.ensure_schema()
    return TestnetExecutionGateway(transport=transport,store=orders,readiness=readiness(),
        risk_governor=governor(dsn),account_id=ACCOUNT,clock=clock)


class UnitApprovalAuthority:
    """Test double for unit gateway tests; never claimed durable evidence."""
    def __init__(self): self.consumed=set()

    def consume_with_intent(self, a, *, store, intent, order, venue_id, account_id, now):
        if not isinstance(a,RiskApproval) or (a.account_id,a.venue_id,a.client_order_id,a.symbol,a.side,a.approved_quantity,a.reference_price,a.risk_profile_id)!=(account_id,venue_id,intent.client_order_id,intent.symbol,intent.side.value,intent.quantity,intent.reference_price,'RECOVERY_SPOT_H4_DEMO_V1'):
            raise RiskAdmissionError('APPROVAL_INTENT_BINDING_MISMATCH')
        # Keep duplicate-intent assertion exercised at the store boundary.
        if store.get(intent.client_order_id) is not None:
            store.create(venue=venue_id,intent=intent,order=order)
        if a.approval_id in self.consumed or not a.issued_at<=now<=a.expires_at:
            raise RiskAdmissionError('INVALID_OR_CONSUMED_APPROVAL')
        store.create(venue=venue_id,intent=intent,order=order)
        self.consumed.add(a.approval_id)

    def reconcile_accounting(self, **kwargs): pass


def unit_approval(*, cid='cid-1', qty='0.01', price='100000', venue='FAKE_DEMO', now=None):
    from research_bot.operational_risk import POLICY_ID
    now=now or datetime.now(timezone.utc)
    return RiskApproval(str(uuid4()),ACCOUNT,venue,'BTC/USDT','BUY',cid,Decimal(qty),Decimal(qty)*Decimal(price),
        Decimal(price),Decimal(price)*Decimal('.999'),Decimal('50'),POLICY_ID,'UNIT_FIXTURE','UNIT_FIXTURE',
        now,now+timedelta(seconds=30),str(uuid4()))
