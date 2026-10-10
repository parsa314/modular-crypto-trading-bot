from __future__ import annotations

"""Start the local MT5/TradingView control panel and open it in a browser."""

import os
import threading
import webbrowser

import uvicorn


def _open(url: str) -> None:
    try:
        webbrowser.open(url, new=2)
    except Exception:
        pass


def main() -> None:
    host = "127.0.0.1"
    port = int(os.getenv("TV_MT5_BIND_PORT", "8000"))
    url = f"http://{host}:{port}/ui"
    threading.Timer(1.2, _open, args=(url,)).start()
    print()
    print("MT5 / TradingView control panel:")
    print(url)
    print("Real-money MT5 accounts are refused by the application.")
    print()
    uvicorn.run(
        "research_bot.tradingview_mt5_service:app",
        host=host,
        port=port,
        log_level=os.getenv("TV_MT5_LOG_LEVEL", "info"),
        reload=False,
    )


if __name__ == "__main__":
    main()
