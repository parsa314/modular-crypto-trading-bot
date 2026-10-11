#!/usr/bin/env python3
"""Prepare Binance BTC_USDT closed 1h historical context; no auth or orders."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
# Also support the user's documented direct script invocation.
if __package__ in (None,""):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
from research_bot.four_systems.binance_history import collect,save

def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol",default="BTCUSDT",choices=["BTCUSDT"])
    p.add_argument("--years",type=int,default=3)
    p.add_argument("--start",help="inclusive UTC open date YYYY-MM-DD")
    p.add_argument("--end",help="inclusive UTC open date YYYY-MM-DD; future bars are excluded")
    p.add_argument("--out",type=Path,default=Path("data/historical/BTC_USDT_1h_closed.csv"))
    p.add_argument("--available-delay-s",type=float,default=0,
        help="declared modeled lag, not measured historical first-seen evidence")
    p.add_argument("--sleep",type=float,default=.15)
    a=p.parse_args();now=datetime.now(timezone.utc)
    print(json.dumps({"event":"RESEARCH_START","RESEARCH_ONLY":True,"PAPER_EXECUTION":False,
        "LIVE_EXECUTION":False,"symbol":"BTC_USDT","timeframe":"1h"}),flush=True)
    try:
        if not 1<=a.years<=10:raise ValueError("years must be between 1 and 10")
        end=pd.Timestamp(datetime.strptime(a.end,"%Y-%m-%d"),tz="UTC")+pd.Timedelta(days=1) if a.end else pd.Timestamp(now)
        start=pd.Timestamp(datetime.strptime(a.start,"%Y-%m-%d"),tz="UTC") if a.start else end-pd.DateOffset(years=a.years)
        frame,meta=collect(start,end,as_of=now,delay_s=a.available_delay_s,sleep_s=a.sleep)
        meta=save(frame,meta,a.out)
    except Exception as e:
        print(json.dumps({"status":"FAILED_DATA_PREPARATION","reason_code":type(e).__name__,
            "reason":str(e),"synthetic_fallback":False,"execution_authorized":False}),flush=True)
        return 1
    print(json.dumps({k:meta[k] for k in ("status","rows","first_close","last_close",
        "csv_sha256","availability_basis","prospective_evidence")}),flush=True)
    return 0

if __name__=="__main__":raise SystemExit(main())
