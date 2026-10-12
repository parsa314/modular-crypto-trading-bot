"""Prepare checksummed BTC_USDT hourly backfill from official public archives."""
from __future__ import annotations
import argparse,json
from pathlib import Path
from research_bot.ten_strategies.history import collect_archives
from research_bot.four_systems.binance_history import save

def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start-month",default="2023-10");p.add_argument("--end-month",default="2026-09")
    p.add_argument("--out",type=Path,default=Path("data/historical/BTC_USDT_1h_archive_closed.csv"))
    a=p.parse_args()
    print(json.dumps({"RESEARCH_ONLY":True,"PAPER_EXECUTION":False,"LIVE_EXECUTION":False}),flush=True)
    try:
        x,m=collect_archives(a.start_month,a.end_month);x=x.rename(columns={"timestamp":"timestamp_close"})
        x["availability_basis"]=m["availability_basis"];x["data_class"]="HISTORICAL_BACKFILL"
        m=save(x,m,a.out)
        print(json.dumps({"status":"VALIDATED_HISTORICAL_BACKFILL","rows":len(x),"csv_sha256":m["csv_sha256"],"prospective_evidence":False}),flush=True)
    except Exception as e:
        print(json.dumps({"status":"FAILED_DATA_PREPARATION","reason":str(e),"synthetic_fallback":False}),flush=True);return 1
    return 0

if __name__=="__main__":raise SystemExit(main())
