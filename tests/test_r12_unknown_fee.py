"""Synthetic unit checks for conservative handling of missing fee details."""
import unittest
from datetime import datetime, timezone
from decimal import Decimal

from research_bot.durable_risk import PostgresRiskGovernor
from research_bot.okx_demo_transport import _fee_from_order


class Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self):
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params):
        self.queries.append((sql, params))
        if sql.startswith("SELECT reservation_id"):
            return Cursor(("r", "BTC/USDT", "BUY", Decimal("1"), Decimal("0"),
                           Decimal("0"), Decimal("0"), "RESERVED"))
        if sql.startswith("SELECT state,matched_quantity"):
            return Cursor(("FILLED", Decimal("1"), Decimal("100"), Decimal("0"), None))
        return Cursor(None)


class FixtureGovernor(PostgresRiskGovernor):
    def __init__(self):
        self.connection = Connection()

    def _connect(self):
        return self.connection

    @staticmethod
    def _account(conn, account_id, venue_id):
        return {"positions": {}, "cash": Decimal("1000")}


class UnknownFeeTests(unittest.TestCase):
    def test_missing_fee_cost_does_not_inherit_reported_currency(self):
        self.assertEqual(_fee_from_order({"fee": {"currency": "USDT"}}),
                         (Decimal("0"), None))

    def test_filled_order_with_unknown_fee_does_not_release_reservation(self):
        governor = FixtureGovernor()
        governor.reconcile_accounting(account_id="synthetic", venue_id="synthetic",
                                      client_order_id="synthetic", now=datetime.now(timezone.utc))
        sql = [query for query, _ in governor.connection.queries]
        self.assertTrue(any("reconciled=FALSE" in query for query in sql))
        self.assertFalse(any(query.startswith("UPDATE risk_reservations") for query in sql))


if __name__ == "__main__":
    unittest.main()
