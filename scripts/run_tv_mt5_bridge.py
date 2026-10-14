from __future__ import annotations

"""Run the dedicated TradingView -> MT5 DEMO bridge service.

This service should run on the Windows host/VPS where the MetaTrader 5 terminal
is installed. Expose it to TradingView through HTTPS on public port 443 (or 80)
using a reverse proxy/tunnel.
"""

import os

import uvicorn


def main() -> None:
    host = os.getenv("TV_MT5_BIND_HOST", "127.0.0.1")
    port = int(os.getenv("TV_MT5_BIND_PORT", "8000"))
    uvicorn.run(
        "research_bot.tradingview_mt5_service:app",
        host=host,
        port=port,
        log_level=os.getenv("TV_MT5_LOG_LEVEL", "info"),
        reload=False,
    )


if __name__ == "__main__":
    main()
