"""Smoke: 100 actual COMPLETED EURUSD H1 MT5 bars. DEMO only; no order calls.

Requires a LOCAL desktop MT5 DEMO terminal, already logged in.
Usage: python scripts/test_mt5_connection.py
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from adapters.mt5_broker import MT5Broker, MT5DemoError


def main()->int:
    p=argparse.ArgumentParser(description="100 real EURUSD H1 completed MT5 Demo bars")
    p.add_argument("--terminal-path",type=Path)
    p.add_argument("--symbol",default="EURUSD")
    p.add_argument("--output",type=Path,default=Path("results/mt5_demo_connection.json"))
    opts=p.parse_args()
    result={"at_utc":datetime.now(timezone.utc).isoformat(),
            "venue":"MT5_DEMO","status":"FAIL","symbol":opts.symbol,
            "timeframe":"H1","count_requested":100,"count_received":0,
            "real_account_permitted":False,"order_attempted":False}
    try:
        with MT5Broker(terminal_path=opts.terminal_path) as broker:
            bars=broker.get_historical_data(opts.symbol,"H1",100)
            print(bars.to_string(index=False))
            digest=hashlib.sha256(bars.to_csv(index=False).encode()).hexdigest()
            result.update({"status":"PASS","count_received":len(bars),
                           "first_closed_bar_utc":bars.timestamp.iloc[0].isoformat(),
                           "last_closed_bar_utc":bars.timestamp.iloc[-1].isoformat(),
                           "candles_sha256":digest})
    except (MT5DemoError,ImportError,OSError,RuntimeError,ValueError) as exc:
        result["failure_code"]=str(exc).split(":",1)[0][:100]
    opts.output.parent.mkdir(parents=True,exist_ok=True)
    opts.output.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result["status"]=="PASS" else 2


if __name__=="__main__":
    raise SystemExit(main())
