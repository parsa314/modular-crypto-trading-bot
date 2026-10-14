"""Read-only cloud MT5 demo preflight; never places an order."""
from __future__ import annotations

import asyncio
import json

from research_bot.execution_adapters.metaapi_demo_readonly import MetaApiDemoReadOnly


async def main() -> int:
    adapter = MetaApiDemoReadOnly()
    try:
        await adapter.connect_async()
        account = await adapter.account_summary_async()
        positions = await adapter.get_positions_async()
        print(json.dumps({
            "status": "READ_ONLY_DEMO_CONNECTED",
            "account": account,
            "open_position_count": len(positions),
            "order_submission": "DISABLED",
        }, sort_keys=True))
        return 0
    except Exception as exc:
        # Avoid printing provider exception text: it may contain sensitive values.
        print(json.dumps({"status": "PREFLIGHT_FAILED", "error_type": type(exc).__name__,
                          "order_submission": "DISABLED"}))
        return 1
    finally:
        await adapter.shutdown_async()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
