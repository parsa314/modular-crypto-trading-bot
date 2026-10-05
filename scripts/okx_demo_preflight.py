from __future__ import annotations

"""Read-only OKX Demo credential preflight.

This command never places, amends, or cancels an order.
"""

import argparse
import json
from decimal import Decimal

from research_bot.okx_demo_transport import OKXDemoTransport


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only OKX Demo Trading preflight")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--reference-price", default="1")
    args = parser.parse_args()

    transport = OKXDemoTransport.from_env()
    result = transport.private_preflight(args.symbol)
    precision = transport.market_precision(
        args.symbol,
        reference_price=Decimal(str(args.reference_price)),
    )
    result["precision"] = {
        "quantity_step": str(precision.quantity_step),
        "min_quantity": str(precision.min_quantity),
        "min_notional": str(precision.min_notional),
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
