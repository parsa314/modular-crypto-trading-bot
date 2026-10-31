"""Engineering regression: synthetic venue response, no broker access."""
import unittest
from decimal import Decimal

from research_bot.execution import OrderSide
from research_bot.nobitex_testnet_transport import snapshot_from_nobitex_order
from research_bot.order_reconciliation_v52 import OrderLifecycle


class NobitexDecimalBoundaryTests(unittest.TestCase):
    def test_requested_quantity_survives_reconciliation_at_18_decimal_places(self):
        quantity = Decimal("0.123456789012345678")
        snapshot = snapshot_from_nobitex_order(
            {"id": "SYNTHETIC", "amount": str(quantity), "matchedAmount": str(quantity),
             "status": "done", "averagePrice": "100", "fee": "0"},
            fallback_client_order_id="SYNTHETIC", requested_quantity=quantity,
            symbol="BTC/USDT", side=OrderSide.BUY,
        )
        self.assertEqual(snapshot.state, OrderLifecycle.FILLED)
        self.assertIsInstance(snapshot.matched_quantity, Decimal)
        self.assertEqual(snapshot.matched_quantity, quantity)


if __name__ == "__main__":
    unittest.main()
