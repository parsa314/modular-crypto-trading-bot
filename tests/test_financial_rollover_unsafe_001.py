"""Synthetic database fixture: no broker, orders, or real account data."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from research_bot.durable_risk import AccountObservation, PostgresRiskGovernor


class Cursor:
    def __init__(self, row=None): self.row = row
    def fetchone(self): return self.row


class RecordingConnection:
    def __init__(self, *, pending_order, pending_fee, order_store=True):
        self.pending_order=pending_order; self.pending_fee=pending_fee
        self.order_store=order_store
        self.halt_releases=0; self.account_values=None

    def __enter__(self): return self
    def __exit__(self, *args): return False

    def execute(self, sql, args=()):
        if "SELECT snapshot_hash FROM risk_account_observations" in sql: return Cursor()
        if "SELECT to_regclass('testnet_orders')" in sql: return Cursor(("testnet_orders" if self.order_store else None,))
        if "AND (t.matched_quantity != r.matched_quantity" in sql: return Cursor()
        if "FROM risk_fee_liabilities" in sql: return Cursor((1,) if self.pending_fee else None)
        if "t.state IN ('CREATED','SUBMITTING','UNKNOWN_PENDING_RECONCILIATION')" in sql:
            return Cursor((1,) if self.pending_order else None)
        if "UPDATE risk_halts SET state='RELEASED'" in sql: self.halt_releases+=1
        if "INSERT INTO risk_account_state" in sql: self.account_values=args
        return Cursor()


class SyntheticGovernor(PostgresRiskGovernor):
    def __init__(self, connection, old):
        self.connection=connection; self.old=old
        self.accepted_fixture_sources=frozenset({"fixture:explicit"})
    def _connect(self): return self.connection
    def _account(self, conn, account_id, venue_id): return self.old
    @staticmethod
    def _latch(conn, account, now): pass
    @staticmethod
    def _audit(conn, account, venue, event, now, payload): pass


class DailyRolloverSafetyTests(unittest.TestCase):
    def scenario(self, *, pending_order=False, pending_fee=False, order_store=True):
        now=datetime(2026,10,31,0,0,tzinfo=timezone.utc)
        old={"net_deposits":Decimal(0),"observed_at":now-timedelta(minutes=1),
             "anchor_status":"RECONCILED_TEST_FIXTURE","peak_equity":Decimal(1000),
             "day_equity":Decimal(1000),"day_utc":(now-timedelta(days=1)).date(),
             "cash":Decimal(1000),"positions":{}}
        conn=RecordingConnection(pending_order=pending_order,pending_fee=pending_fee,order_store=order_store)
        g=SyntheticGovernor(conn,old)
        obs=AccountObservation("synthetic","venue","snapshot","fixture:explicit",now,
            Decimal(1000),Decimal(1000),Decimal(0),{}, {},reconciled=True,unresolved_orders=False)
        g.observe(obs,received_at=now)
        return conn

    def test_unknown_submitted_order_cannot_release_daily_halt(self):
        c=self.scenario(pending_order=True)
        self.assertEqual(c.halt_releases,0)
        self.assertEqual(c.account_values[5],"ANCHOR_UNVERIFIED")
        self.assertEqual(c.account_values[-2:],(False,True))

    def test_unresolved_fee_cannot_release_daily_halt(self):
        c=self.scenario(pending_fee=True)
        self.assertEqual(c.halt_releases,0)
        self.assertEqual(c.account_values[5],"ANCHOR_UNVERIFIED")
        self.assertEqual(c.account_values[-2:],(False,True))

    def test_reconciled_rollover_still_permits_scheduled_release(self):
        c=self.scenario()
        self.assertEqual(c.halt_releases,1)
        self.assertEqual(c.account_values[5],"RECONCILED_TEST_FIXTURE")
        self.assertEqual(c.account_values[-2:],(True,False))

    def test_missing_order_store_cannot_release_daily_halt(self):
        c=self.scenario(order_store=False)
        self.assertEqual(c.halt_releases,0)
        self.assertEqual(c.account_values[5],"ANCHOR_UNVERIFIED")
        self.assertEqual(c.account_values[-2:],(False,True))


if __name__=="__main__": unittest.main()
