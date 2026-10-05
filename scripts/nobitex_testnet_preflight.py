from __future__ import annotations

"""Read-only Nobitex TESTNET connectivity and credential preflight."""

import argparse
import json

from research_bot.nobitex_testnet_transport import NobitexTestnetTransport


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Nobitex TESTNET preflight")
    parser.add_argument("--symbol", default="BTC/USDT")
    args = parser.parse_args()

    transport = NobitexTestnetTransport.from_env()
    result = transport.private_preflight(args.symbol)
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
