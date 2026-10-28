"""Explicit integration job: requires an isolated local recovery_test database.

Not part of default unit discovery. Missing setup is an error, never a skip.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import os
import unittest
from urllib.parse import urlparse
from uuid import uuid4

from research_bot.execution import OrderSide
from research_bot.order_reconciliation_v52 import OrderLifecycle, ReconciledOrder
from research_bot.testnet_execution import (
    PostgresTestnetOrderStore, TestnetExecutionGateway, TestnetOrderIntent,
    TestnetSafetyError, VenueOrderSnapshot,
)


class PostgresRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        dsn = os.environ.get('RECOVERY_TEST_DATABASE_URL', '')
        parsed = urlparse(dsn)
        if parsed.hostname not in {'localhost', '127.0.0.1'} or parsed.path != '/recovery_test':
            raise RuntimeError('isolated local recovery_test database is required')
        cls.dsn = dsn
        cls.store = PostgresTestnetOrderStore(dsn)
        cls.store.ensure_schema()

    def setUp(self) -> None:
        self.cid = 'SYNTHETIC_UNIT_' + uuid4().hex
        now = datetime.now(timezone.utc)
        self.intent = TestnetOrderIntent(self.cid, 'BTC/USDT', OrderSide.BUY, Decimal('1'), Decimal('100'), now)
        self.order = ReconciledOrder(self.cid, 'BTC/USDT', 'buy', 1.0, state=OrderLifecycle.PARTIAL,
                                     venue_order_id='SYNTHETIC_VENUE', matched_quantity=0.8, updated_at=now)
        self.store.create(venue='SYNTHETIC_TESTNET', intent=self.intent, order=self.order)

    def snapshot(self, quantity: str, state: OrderLifecycle) -> VenueOrderSnapshot:
        return VenueOrderSnapshot(self.cid, 'SYNTHETIC_VENUE', state, Decimal(quantity))

    def test_reopen_preserves_partial_and_rejects_regression(self) -> None:
        reopened = PostgresTestnetOrderStore(self.dsn)
        loaded = reopened.get(self.cid)
        self.assertEqual(loaded.state, OrderLifecycle.PARTIAL)
        with self.assertRaises(TestnetSafetyError):
            TestnetExecutionGateway._merge_snapshot(loaded, self.snapshot('0.2', OrderLifecycle.PARTIAL))
        self.assertEqual(reopened.get(self.cid).matched_quantity, 0.8)

    def test_invalid_complete_fill_cannot_change_persisted_order(self) -> None:
        with self.assertRaises(TestnetSafetyError):
            TestnetExecutionGateway._merge_snapshot(self.store.get(self.cid), self.snapshot('0.9', OrderLifecycle.FILLED))
        self.assertEqual(self.store.get(self.cid).state, OrderLifecycle.PARTIAL)
        valid = TestnetExecutionGateway._merge_snapshot(self.store.get(self.cid), self.snapshot('1', OrderLifecycle.FILLED))
        self.store.update(valid)
        self.assertEqual(PostgresTestnetOrderStore(self.dsn).get(self.cid).state, OrderLifecycle.FILLED)

    def test_duplicate_reservation_is_rejected(self) -> None:
        import psycopg
        with self.assertRaises(psycopg.errors.UniqueViolation):
            PostgresTestnetOrderStore(self.dsn).create(venue='SYNTHETIC_TESTNET', intent=self.intent, order=self.order)


if __name__ == '__main__':
    unittest.main()
