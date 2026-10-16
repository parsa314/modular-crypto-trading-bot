"""v6 numeric execution on public Binance spot daily candles; NO private keys/orders.

Single unchanged v6 rule: previous close breaks prior 20 highs +0.25 ATR14;
exit next open on previous close below highest-close since entry -2 ATR14.
All-in portfolio is SPOT long-only with equal per-asset cap and 15bp
COMBINED fee-plus-slippage per side. No ML, RL, or parameter search.
"""
from __future__ import annotations
import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, stdev
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import time

DAY_MS = 86_400_000
COST_SIDE = 0.0015
START = "2021-12-01"
TRAIN = "2022-01-01"
BEAR_END = "2023-01-01"
VAL_START = "2024-01-01"
VAL_END = "2025-01-01"
TEST_END = "2026-10-01"
FIVE = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
TWENTY = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
          "ADAUSDT", "DOGEUSDT", "DOTUSDT", "LINKUSDT", "LTCUSDT",
          "AVAXUSDT", "SHIBUSDT", "ATOMUSDT", "UNIUSDT", "ETCUSDT",
          "XLMUSDT", "BCHUSDT", "TRXUSDT", "FILUSDT", "AAVEUSDT"]


def epoch(d):
    return int(datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp()*1000)


def fetch(symbol, start, end):
    """Get all CLOSED daily spot candles; missing bars are a hard error."""
    first, stop = epoch(start), epoch(end)
    cursor = first
    found = {}
    sources_used = []
    while cursor < stop:
        query = urlencode({"symbol":symbol,"interval":"1d","startTime":cursor,
                           "endTime":stop-1,"limit":1000})
        payload = None
        last_error = None
        for host in ("https://data-api.binance.vision", "https://api.binance.com"):
            url = host + "/api/v3/klines?" + query
            for retry in range(2):
                try:
                    with urlopen(Request(url,headers={"User-Agent":"noncommercial-academic-backtest/1.0"}),timeout=35) as response:
                        payload=json.loads(response.read())
                    if not isinstance(payload,list) or not payload:
                        raise RuntimeError("EMPTY_DAILY_KLINES")
                    sources_used.append(host)
                    break
                except Exception as exc:
                    last_error=repr(exc)
                    time.sleep(1+retry)
            if payload is not None:
                break
        if payload is None:
            raise RuntimeError(f"MARKET_SOURCE_UNAVAILABLE {symbol} {start} {last_error}")
        for row in payload:
            t=int(row[0])
            if t < first or t >= stop:
                continue
            if (t-first) % DAY_MS != 0:
                raise ValueError(f"OFF_GRID {symbol} {t}")
            p={"o":float(row[1]),"h":float(row[2]),
               "l":float(row[3]),"c":float(row[4]),"q":float(row[7])}
            if not all(math.isfinite(v) for v in p.values()) or min(p.values()) <= 0:
                raise ValueError(f"BAD_OHLCV {symbol} {t}")
            if p["h"] < max(p["o"],p["c"],p["l"]) or p["l"] > min(p["o"],p["c"],p["h"]):
                raise ValueError(f"INCONSISTENT_OHLC {symbol} {t}")
            if t in found and found[t]!=p:
                raise ValueError("DUPLICATE_CONFLICT")
            found[t]=p
        nxt=int(payload[-1][0])+DAY_MS
        if nxt<=cursor:
            raise RuntimeError("STALLED_PAGINATION")
        cursor=nxt
        time.sleep(.12)
    days=list(range(first,stop,DAY_MS))
    gaps=[d for d in days if d not in found]
    if gaps:
        raise ValueError(f"MISSING_DAILY_BARS {symbol} {len(gaps)} first={gaps[0]}")
    rows=[found[t] for t in days]
    checksum=hashlib.sha256(json.dumps(rows,separators=(",",":"),sort_keys=True).encode()).hexdigest()
    print("VERIFIED_REAL_DAILY",symbol,start,end,len(rows),checksum,flush=True)
    return rows,checksum,list(sorted(set(sources_used)))


def compute_atr(bars):
    tr=[]
    for i,p in enumerate(bars):
        prev=bars[i-1]["c"] if i else p["c"]
        tr.append(max(p["h"]-p["l"],abs(p["h"]-prev),abs(p["l"]-prev)))
    atr=[]
    value=None
    for i,t in enumerate(tr):
        if i==13:
            value=sum(tr[:14])/14
        elif i>13:
            value=(13*value+t)/14
        atr.append(value)
    return atr


def metrics(nav,trades):
    step=[nav[i]/nav[i-1]-1 for i in range(1,len(nav))]
    avg=mean(step)
    sd=stdev(step) if len(step)>1 else 0.
    downside=math.sqrt(mean(min(0,x)**2 for x in step))
    top=nav[0]
    drawdown=0.
    for v in nav:
        top=max(top,v)
        drawdown=max(drawdown,1-v/top)
    wins=[x for x in trades if x>0]
    losses=[-x for x in trades if x<0]
    return {"sharpe":math.sqrt(365)*avg/sd if sd>1e-12 else None,
            "sortino":math.sqrt(365)*avg/downside if downside>1e-12 else None,
            "maxdd":drawdown,"net_return":nav[-1]-1,
            "profit_factor":sum(wins)/sum(losses) if losses else None,
            "win_rate":len(wins)/len(trades) if trades else None,
            "num_trades":len(trades)}


def backtest(panel,assets,first,last):
    origin=epoch(START)
    a=(epoch(first)-origin)//DAY_MS
    b=(epoch(last)-origin)//DAY_MS
    if a<22 or b<=a:
        raise ValueError("NEED_WARMUP_AND_EVALUATION_DAYS")
    atr={s:compute_atr(panel[s]) for s in assets}
    cash=1.
    qty={s:0. for s in assets}
    trail={s:0. for s in assets}
    cost_basis={s:0. for s in assets}
    nav=[1.]
    trades=[]
    for i in range(a,b):
        d=i-1
        for s in assets:
            if qty[s]<=0:
                continue
            bars=panel[s]
            if bars[d]["c"] < trail[s]-2*atr[s][d]:
                amount=qty[s]*bars[i]["o"]*(1-COST_SIDE)
                cash+=amount
                trades.append(amount/cost_basis[s]-1)
                qty[s]=0.
                cost_basis[s]=0.
                trail[s]=0.
        portfolio_value=cash+sum(qty[s]*panel[s][i]["o"] for s in assets)
        equal_slot=portfolio_value/len(assets)
        for s in assets:
            if qty[s]>0:
                continue
            bars=panel[s]
            high=max(p["h"] for p in bars[d-20:d])
            if bars[d]["c"] <= high+0.25*atr[s][d]:
                continue
            amount=min(equal_slot,cash)
            if amount <= 1e-10:
                continue
            qty[s]=amount/(bars[i]["o"]*(1+COST_SIDE))
            cash-=amount
            cost_basis[s]=amount
            trail[s]=bars[d]["c"]
        for s in assets:
            if qty[s]>0:
                trail[s]=max(trail[s],panel[s][i]["c"])
        nav.append(cash+sum(qty[s]*panel[s][i]["c"] for s in assets))
    for s in assets:
        if qty[s]>0:
            proceeds=qty[s]*panel[s][b-1]["c"]*(1-COST_SIDE)
            cash+=proceeds
            trades.append(proceeds/cost_basis[s]-1)
    nav[-1]=cash
    return metrics(nav,trades)


def benchmark(panel,assets,first,last):
    start=(epoch(first)-epoch(START))//DAY_MS
    stop=(epoch(last)-epoch(START))//DAY_MS
    shares={s:(1/len(assets))/(panel[s][start]["o"]*(1+COST_SIDE)) for s in assets}
    nav=[1.]
    for i in range(start,stop):
        nav.append(sum(shares[s]*panel[s][i]["c"] for s in assets))
    nav[-1]=sum(shares[s]*panel[s][stop-1]["c"]*(1-COST_SIDE) for s in assets)
    return metrics(nav,[])


def output(path,data):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,indent=2,allow_nan=False)+"\n")
    print("RESULT_FILE",str(path),data.get("verdict",""),flush=True)


def main():
    # Deliberately start from the fixed 20 symbols; this cannot truthfully
    # be called the top-20 *historical PIT* capitalisation universe.
    panel={}
    checksums={}
    for sym in TWENTY:
        rows,hashvalue,hosts=fetch(sym,START,VAL_END)
        panel[sym]=rows
        checksums[sym]={"sha256":hashvalue,"rows":len(rows),"api_hosts":hosts}
    y2022=backtest(panel,FIVE,TRAIN,BEAR_END)
    bearbtc=benchmark(panel,["BTCUSDT"],TRAIN,BEAR_END)
    outcome2022={"strategy":"v6_5_coins_2022_bear","strategy_metrics":y2022,
                 "btc_metrics":bearbtc,
                 "verdict":"PASS_BEAR_RELATIVE_TO_BTC" if y2022["net_return"]>bearbtc["net_return"] else "FAIL_BEAR",
                 "test3":"not opened"}
    output("results/v6_2022.json",outcome2022)
    bear20=backtest(panel,TWENTY,TRAIN,BEAR_END)
    val20=backtest(panel,TWENTY,VAL_START,VAL_END)
    btcval=benchmark(panel,["BTCUSDT"],VAL_START,VAL_END)
    equal20=benchmark(panel,TWENTY,VAL_START,VAL_END)
    # The user has explicitly replaced the gate AFTER seeing 2024 v6
    # five-asset outcomes. Do not claim preregistered or clean OOS.
    pass_bear=bear20["net_return"]>bearbtc["net_return"]
    pass_val=(val20["sharpe"] is not None and
              (val20["sharpe"]>0.8 or
               (val20["sharpe"]>0.5 and val20["maxdd"]<.25)))
    gate=pass_bear and pass_val
    report={"strategy":"v6_fixed_20_coins","2022":bear20,"2022_btc":bearbtc,
            "2024_validation":val20,"2024_btc":btcval,"2024_equal_weight":equal20,
            "gate":{"bear_beats_btc":pass_bear,
                    "val_sharpe_above_0_8_or_above_0_5_with_dd_below_25pct":pass_val,
                    "pass":gate},
            "verdict":"PASS_EXPLORATORY" if gate else "FAIL",
            "universe":TWENTY,"data_from":"Binance PUBLIC spot 1d klines",
            "source_checksums":checksums,"fees_plus_slippage_bps_per_side":15,
            "warning":"20 fixed retrospectively selected assets, NOT historical PIT top-20; 2024 rules modified after earlier 2024 test; exploratory only",
            "test3":"conditional_only"}
    output("results/v6_20coins.json",report)
    print("GATE_RESULTS",json.dumps({"bear":pass_bear,"bull":pass_val,"train2022_sharpe":bear20["sharpe"],"val2024_sharpe":val20["sharpe"],"verdict":report["verdict"]}),flush=True)
    if not gate:
        print("TEST3_SEALED_BECAUSE_DEVELOPMENT_GATE_FAILED",flush=True)
        return
    testpath=Path("results/v6_test3.json")
    if testpath.exists():
        raise RuntimeError("TEST3_ALREADY_OPENED_REFUSE_SECOND_LOOK")
    for sym in TWENTY:
        extra,h,hosts=fetch(sym,VAL_END,TEST_END)
        panel[sym].extend(extra)
    tested=backtest(panel,TWENTY,VAL_END,TEST_END)
    btc=benchmark(panel,["BTCUSDT"],VAL_END,TEST_END)
    passed=(tested["sharpe"] is not None and tested["sharpe"]>0.5 and
            tested["net_return"]>btc["net_return"])
    output(testpath,{"strategy":"v6_fixed_20_coins","start":"2025-01-01",
                     "end_inclusive":"2026-09-30","v6":tested,"btc":btc,
                     "verdict":"LIVE_READY_RESEARCH_ONLY" if passed else "REJECTED_FINAL",
                     "first_look_only":True,
                     "caveat":"Not a real-money execution authorization. This is a retrospectively selected 20-asset proxy and 2024 thresholds changed post hoc."})


if __name__=="__main__":
    main()
