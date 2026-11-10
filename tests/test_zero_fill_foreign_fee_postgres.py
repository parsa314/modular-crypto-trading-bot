"""Real PostgreSQL regression; all order input is synthetic and offline."""
from decimal import Decimal
import multiprocessing
import unittest

from p0_fixtures import ACCOUNT, VENUE, NOW, approve, drop_schema, gateway, governor, pg_schema, seed
from research_bot.execution import OrderSide
from research_bot.order_reconciliation_v52 import OrderLifecycle
from research_bot.testnet_execution import MarketPrecision, VenueOrderSnapshot


class Transport:
    sandbox = True
    venue = VENUE

    def submit_market_order(self, intent):
        return VenueOrderSnapshot(intent.client_order_id, 'SYNTHETIC', OrderLifecycle.ACKNOWLEDGED)


def reconcile_worker(dsn, order_id, barrier, result):
    try:
        barrier.wait(20)
        governor(dsn).reconcile_accounting(account_id=ACCOUNT, venue_id=VENUE,
                                             client_order_id=order_id, now=NOW)
        result.put('OK')
    except Exception as exc:
        result.put(type(exc).__name__ + ':' + str(exc))


class ZeroFillForeignFeePostgres(unittest.TestCase):
    def setUp(self):
        self.root, self.schema, self.dsn = pg_schema()
        self.addCleanup(drop_schema, self.root, self.schema)
        self.g = seed(self.dsn)
        approval = approve(self.g, cid='zero-fill-foreign')
        service = gateway(Transport(), dsn=self.dsn, clock=lambda: NOW)
        precision = MarketPrecision(Decimal('0.000000000000000001'),
                                    Decimal('0.000000000000000001'), Decimal('5'),
                                    Decimal('0.000000000000000001'))
        service.submit_market(client_order_id=approval.client_order_id, symbol=approval.symbol,
                              side=OrderSide(approval.side), quantity=approval.approved_quantity,
                              reference_price=approval.reference_price, precision=precision,
                              risk_decision=approval, created_at=NOW)
        with self.g._connect() as conn:
            conn.execute("UPDATE testnet_orders SET state='CANCELLED', fee_reported=%s, fee_currency=%s, state_version=state_version+1 WHERE client_order_id=%s",
                         (Decimal('.1'), 'BNB', approval.client_order_id))
        self.order_id = approval.client_order_id

    def state(self):
        with self.g._connect() as conn:
            cash = conn.execute('SELECT cash FROM risk_account_state WHERE account_id=%s AND venue_id=%s', (ACCOUNT, VENUE)).fetchone()[0]
            fee = conn.execute('SELECT state FROM risk_fee_liabilities WHERE client_order_id=%s', (self.order_id,)).fetchone()
            reservation = conn.execute('SELECT state FROM risk_reservations WHERE client_order_id=%s', (self.order_id,)).fetchone()[0]
            return cash, fee, reservation

    def test_concurrent_reconciliation_and_restart_keep_fee_unresolved(self):
        ctx = multiprocessing.get_context('spawn')
        barrier, result = ctx.Barrier(2), ctx.Queue()
        workers = [ctx.Process(target=reconcile_worker, args=(self.dsn, self.order_id, barrier, result)) for _ in range(2)]
        for worker in workers: worker.start()
        for worker in workers:
            worker.join(30)
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual(sorted(result.get(timeout=5) for _ in workers), ['OK', 'OK'])
        self.assertEqual(self.state(), (Decimal('1000'), ('UNRESOLVED',), 'UNRESOLVED_FEE'))
        governor(self.dsn).reconcile_accounting(account_id=ACCOUNT, venue_id=VENUE,
                                                client_order_id=self.order_id, now=NOW)
        self.assertEqual(self.state(), (Decimal('1000'), ('UNRESOLVED',), 'UNRESOLVED_FEE'))
