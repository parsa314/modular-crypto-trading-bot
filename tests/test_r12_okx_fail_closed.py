"""Synthetic checks: an unverified OKX serializer cannot submit an order."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from research_bot.okx_demo_transport import OKXDemoTransport
from research_bot.testnet_execution import TestnetExecutionGateway, TestnetSafetyError


class OKXPrecisionBlockTests(unittest.TestCase):
    def test_adapter_blocks_before_exchange_call(self):
        adapter = object.__new__(OKXDemoTransport)
        adapter._exchange = Mock()
        with self.assertRaisesRegex(TestnetSafetyError, "OKX_PRECISION_UNVERIFIED"):
            adapter.submit_market_order(SimpleNamespace())
        adapter._exchange.create_order.assert_not_called()

    def test_gateway_blocks_before_consuming_risk_or_creating_intent(self):
        gateway = object.__new__(TestnetExecutionGateway)
        gateway.transport = object.__new__(OKXDemoTransport)
        gateway.risk_governor = Mock()
        gateway.store = Mock()
        with self.assertRaisesRegex(TestnetSafetyError, "OKX_PRECISION_UNVERIFIED"):
            gateway.submit_market(client_order_id="synthetic", symbol="BTC/USDT", side=None,
                                  quantity=None, reference_price=None, precision=None, risk_decision=None)
        gateway.risk_governor.consume_with_intent.assert_not_called()
        gateway.store.create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
