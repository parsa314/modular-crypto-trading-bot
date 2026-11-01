"""Non-prospective L1 scenario replay. Historical results are not broker evidence."""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import json
import os
from pathlib import Path
import sys

import pandas as pd
from research_bot.quant_001_ichimoku import DataIdentity, evaluate_kijun_pullback
from research_bot.quant_002_data import complete_bars
from research_bot.quant_002_l1 import CostScenario, L1Bar, L1Simulator


DATA_SHA="f7bf84ccaff343dbb627c17e6245ae6f2488f940a3ed6867efd262115ea9d401"
SOURCE_ID="BINANCE_VISION_CDA9E5FF47D0F40CB62B6734C784317D2F15E405"
SCENARIOS=(CostScenario("ZERO",Decimal(0),Decimal(0),Decimal(0)),
           CostScenario("24_BPS",Decimal(10),Decimal(6),Decimal(8)),
           CostScenario("36_BPS",Decimal(10),Decimal(12),Decimal(14)),
           CostScenario("50_BPS",Decimal(10),Decimal(18),Decimal(22)))


def d(value): return Decimal(str(value))


def benchmark(first_open,last_open,scenario):
    # Fully invested comparator, not risk matched to the 35% position cap.
    f=scenario.fees_rt_bps/Decimal(20000)
    p=(scenario.spread_rt_bps+scenario.slippage_rt_bps)/Decimal(20000)
    entry=d(first_open)*(1+p); exit=d(last_open)*(1-p)
    q=Decimal(10000)/(entry*(1+f))
    return {"cash_baseline_final":"10000", "buy_hold_final":str(q*exit*(1-f)),
            "buy_hold_allocation":"100_PERCENT_NOT_RISK_MATCHED"}


def summarize(sim):
    state=sim.state; hist=state["nav_history"]; trades=state["trades"]
    peak=Decimal(10000); maxdd=Decimal(0)
    for h in hist:
        nav=d(h["nav"]); peak=max(peak,nav)
        maxdd=max(maxdd,(peak-nav)/peak)
    pnls=[d(t["pnl"]) for t in trades]
    events=Counter(e["kind"] for e in state["events"])
    return {"scenario":sim.config["scenario"],"execution_assumption":sim.config["mode"],
        "signals":events["SIGNAL"],"submitted":events["SUBMITTED_SIMULATED"],
        "fills":events["FILLED"],"expired":events["EXPIRED"],"cancelled":events["CANCELLED"],
        "exits":events["EXIT"],"ambiguous":events["AMBIGUOUS"],"trades_closed":len(trades),
        "open_position_unresolved":state["position"] is not None,"pending_order_unresolved":state["pending"] is not None,
        "win_rate":str(Decimal(sum(p>0 for p in pnls))/Decimal(len(pnls))) if pnls else None,
        "mean_net_pnl_usdt":str(sum(pnls,Decimal(0))/Decimal(len(pnls))) if pnls else None,
        "final_nav_marked":hist[-1]["nav"],"max_close_mark_drawdown":str(maxdd),
        "max_close_mark_gross_exposure":str(max((d(x["gross_exposure"]) for x in hist),default=Decimal(0))),
        "cumulative_fees":state["total_fees"],"daily_halt":state["daily_halt"],"hard_halt":state["hard_halt"],
        "fees_rt_bps":sim.config["fees_rt_bps"],"spread_rt_bps":sim.config["spread_rt_bps"],
        "slippage_rt_bps":sim.config["slippage_rt_bps"],
        "metrics_status":"DESCRIPTIVE_PREVIOUSLY_OBSERVED_SAMPLE_NOT_OOS"}


def main():
    with localcontext() as ctx:
        ctx.prec=80
        path,manifest_path,out=map(Path,sys.argv[1:4])
        source_sha=hashlib.sha256(path.read_bytes()).hexdigest()
        manifest=json.loads(manifest_path.read_text())
        if source_sha != DATA_SHA or source_sha != manifest["output_sha256"]:
            raise ValueError("DATA_DIGEST_MISMATCH")
        raw=pd.read_parquet(path)
        source_dtypes={name:str(raw[name].dtype) for name in ("timestamp","open","high","low","close","volume")}
        raw["timestamp"]=pd.to_datetime(raw.timestamp,utc=True)
        if raw.timestamp.duplicated().any() or not raw.timestamp.is_monotonic_increasing:
            raise ValueError("DUPLICATE_OR_UNSORTED_SOURCE")
        expected=pd.date_range("2022-01-01T00:00:00Z","2025-01-01T00:00:00Z",freq="15min",inclusive="left")
        missing=expected.difference(pd.DatetimeIndex(raw.timestamp))
        if len(missing) != manifest["missing_bars"] or len(raw) != manifest["rows"]:
            raise ValueError("DATA_MANIFEST_COUNTS_MISMATCH")
        warm=raw[(raw.timestamp >= "2022-12-01T00:00:00Z") & (raw.timestamp < "2023-02-01T00:00:00Z")]
        h1,rejected1=complete_bars(warm,hours=1)
        h4,rejected4=complete_bars(warm,hours=4)
        period=h1[(h1.timestamp >= "2023-01-01T00:00:00Z") & (h1.timestamp < "2023-02-01T00:00:00Z")].reset_index(drop=True)
        if len(period)!=31*24 or not period.timestamp.diff().dropna().eq(pd.Timedelta(hours=1)).all():
            raise ValueError("INCOMPLETE_EXECUTION_PERIOD")
        s1=DataIdentity("BINANCE_SPOT","BTC/USDT","H1",SOURCE_ID,source_sha,"HISTORICAL_CLOSE_ASSUMED")
        s4=DataIdentity("BINANCE_SPOT","BTC/USDT","H4",SOURCE_ID,source_sha,"HISTORICAL_CLOSE_ASSUMED")
        candidates=[]
        for index,row in enumerate(period.itertuples(index=False)):
            # Exclude the terminal close because no later bar is inside the window.
            if index == len(period)-1:
                candidates.append(None);continue
            setup=evaluate_kijun_pullback(h1=h1,h4=h4,h1_source=s1,h4_source=s4,as_of=row.available_at.to_pydatetime())
            candidates.append(None if setup is None else setup.as_dict())
        out.mkdir(parents=True,exist_ok=True)
        code_sha=os.environ.get("QUANT_002_SOURCE_SHA","")
        if len(code_sha)!=40:
            raise ValueError("CODE_SHA_REQUIRED")
        reports=[]
        for scenario in SCENARIOS:
            for mode in ("stop_first","target_touch_assumed"):
                sim=L1Simulator(scenario,source_id=SOURCE_ID,source_sha256=source_sha,mode=mode)
                restarted=None
                for index,row in enumerate(period.itertuples(index=False)):
                    b=L1Bar(row.timestamp.to_pydatetime(),row.available_at.to_pydatetime(),
                            d(row.open),d(row.high),d(row.low),d(row.close),source_sha)
                    sim.bar(b)
                    if restarted is not None: restarted.bar(b)
                    if candidates[index] is not None:
                        sim.signal(candidates[index])
                        if restarted is not None: restarted.signal(candidates[index])
                    if index==len(period)//2:
                        restarted=L1Simulator.restore(sim.snapshot())
                if restarted is None or restarted.snapshot()!=sim.snapshot():
                    raise ValueError("RESTART_REPLAY_MISMATCH")
                name=scenario.name+"-"+mode
                (out/(name+"-events.json")).write_text(json.dumps(sim.state["events"],indent=2,sort_keys=True)+"\n")
                (out/(name+"-trades.json")).write_text(json.dumps(sim.state["trades"],indent=2,sort_keys=True)+"\n")
                result=summarize(sim)
                result["benchmark"]=benchmark(period.open.iloc[0],period.open.iloc[-1],scenario)
                result["run_id"]=hashlib.sha256(json.dumps({"code_sha":code_sha,"data_sha":source_sha,
                    "scenario":sim.config,"window":"2023-01-01/2023-02-01"},sort_keys=True).encode()).hexdigest()
                reports.append(result)
        report={"status":"INSUFFICIENT_EVIDENCE","evidence":"HISTORICAL_NON_PROSPECTIVE_DEVELOPMENT",
            "source_sha256":source_sha,"code_sha":code_sha,"source_commit":"cda9e5ff47d0f40cb62b6734c784317d2f15e405",
            "source_rows":len(raw),"source_dtypes":source_dtypes,"missing_m15":[x.isoformat() for x in missing],
            "rejected_h1":list(rejected1),"rejected_h4":list(rejected4),
            "start":"2023-01-01T00:00:00Z","end_exclusive":"2023-02-01T00:00:00Z","bars":len(period),
            "candidates":sum(x is not None for x in candidates),"costs":"ALL_COMPONENTS_ASSUMED",
            "limitations":["PREVIOUSLY_OBSERVED_NOT_INDEPENDENT_OOS","FIRST_SEEN_UNVERIFIED",
                "NO_REAL_BID_ASK_OR_QUEUE","L1_EXECUTION_IS_HYPOTHETICAL","TARGET_TOUCH_NOT_GUARANTEED",
                "SCENARIOS_ARE_NOT_GLOBAL_MATHEMATICAL_BOUNDS","EXCHANGE_PRECISION_UNVERIFIED",
                "DECIMAL_STRING_CONVERSION_CANNOT_RECOVER_FLOAT_SOURCE_PRECISION",
                "INTRABAR_HALT_IS_CONSERVATIVE","STOP_GAPS_CAN_EXCEED_RISK_TARGET",
                "BINANCE_VISION_DATA_TERMS_APPLY"],"results":reports}
        (out/"report.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
        print(json.dumps(report,sort_keys=True))


if __name__=="__main__":main()
