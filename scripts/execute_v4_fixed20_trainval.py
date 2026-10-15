"""Execute a frozen v4 20-symbol Donchian challenger, train 2022-23, val 2024.

Source: Binance market-only public daily klines. NO data past 2024-12-31.
Records exact 20 SYMBOL FIXED BASKET chosen ex ante for this run, and clearly
states that historic 'top 20 by market cap' membership is NOT verified.
DO NOT present this exploratory fixed basket as a historical top-20 test.
"""
from __future__ import annotations
import json,math,hashlib,sys,time,urllib.request,urllib.parse
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
import pandas as pd

SYMBOLS=("BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT","DOGEUSDT",
"ADAUSDT","TRXUSDT","LTCUSDT","DOTUSDT","LINKUSDT","AVAXUSDT",
"BCHUSDT","XLMUSDT","ATOMUSDT","ETCUSDT","UNIUSDT","AAVEUSDT",
"FILUSDT","NEARUSDT")
START="2022-01-01";TRAIN_END="2024-01-01";END="2025-01-01"
SIDE_COST=0.0015
OUT=Path("results/v4_fixed20_trainval.json")

def download(symbol):
    start=int(pd.Timestamp(START,tz="UTC").timestamp()*1000)
    end=int(pd.Timestamp(END,tz="UTC").timestamp()*1000)
    base=["https://data-api.binance.vision/api/v3/klines",
          "https://api.binance.com/api/v3/klines"]
    raw=[]
    while start<end:
        query=urllib.parse.urlencode({"symbol":symbol,"interval":"1d",
                                      "startTime":start,"endTime":end-1,"limit":1000})
        last=None
        for attempt in range(4):
            for host in base:
                try:
                    req=urllib.request.Request(host+"?"+query,headers={"User-Agent":"Noncommercial-Research-Only/1"})
                    with urllib.request.urlopen(req,timeout=45) as res:
                        ans=json.load(res)
                    if not isinstance(ans,list):raise ValueError("NON_KLINES_RESPONSE:"+str(ans)[:100])
                    break
                except Exception as exc:
                    last=exc;ans=None
            if ans is not None:break
            time.sleep(1+attempt*2)
        if ans is None:raise RuntimeError(f"MARKET_ONLY_API_UNAVAILABLE:{symbol} {last}")
        if not ans:break
        raw.extend(ans)
        nxt=int(ans[-1][0])+86400000
        if nxt<=start:raise RuntimeError("HISTORICAL_NONMONOTONIC")
        start=nxt
    if not raw:raise RuntimeError("EMPTY_REAL_COIN_HISTORY_"+symbol)
    seen=set();rows=[]
    for x in raw:
        t=int(x[0])
        if t in seen:continue
        seen.add(t)
        rows.append([pd.Timestamp(t,unit="ms",tz="UTC"), *[float(x[i]) for i in [1,2,3,4,7]]])
    d=pd.DataFrame(rows,columns=["date","open","high","low","close","quote_volume"]).set_index("date").sort_index()
    if not d.index.is_unique or (d.high<d[["open","close","low"]].max(axis=1)).any() or (d.low>d[["open","close","high"]].min(axis=1)).any():
        raise RuntimeError("INVALID_ACTUAL_CANDLES_"+symbol)
    print("VERIFIED_MARKET_ONLY",symbol,len(d),d.index[0].isoformat(),d.index[-1].isoformat(),flush=True)
    return d

def values_and_trades(series,prices):
    # recompute completed closed-day signals at i then execute at i+1 open.
    # fixed 55-day Donchian upper breakout vs previous 55 days, initial
    # 20-day exit and ATR trailing 2x after entry. 5% max symbol weight.
    actions=[]
    for sym,df in series.items():
        df=df.copy()
        prev=df.close.shift(1)
        true=pd.concat([(df.high-df.low),(df.high-prev).abs(),(df.low-prev).abs()],axis=1).max(axis=1)
        atr=true.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
        high55=df.high.shift(1).rolling(55,min_periods=55).max()
        low20=df.low.shift(1).rolling(20,min_periods=20).min()
        df["atr"]=atr
        df["high55"]=high55
        df["low20"]=low20
        series[sym]=df
    days=pd.date_range(START,periods=(pd.Timestamp(END)-pd.Timestamp(START)).days,freq="D",tz="UTC")
    cash=1.0;shares={};peakprice={};entry={};fees=0.;trades=[];nav=[];orders=0;entry_dates=[]
    for i,d in enumerate(days):
        px_open={}
        px_close={}
        for sym,df in series.items():
            if d in df.index:
                px_open[sym]=float(df.at[d,"open"])
                px_close[sym]=float(df.at[d,"close"])
            elif sym in shares:
                # no invented exit price after delist/outage => forbid report
                raise RuntimeError("OPEN_POSITION_WITHOUT_OBSERVED_BAR_"+sym+"_"+str(d))
        # Previous completed session decision, execute at current open.
        if i>0:
            prevday=days[i-1]
            for sym in SYMBOLS:
                if sym not in px_open:continue
                df=series[sym]
                if prevday not in df.index:continue
                previous=df.loc[prevday]
                if not np.isfinite(previous.high55) or not np.isfinite(previous.low20) or not np.isfinite(previous.atr):
                    continue
                p=px_open[sym]
                if sym in shares:
                    peakprice[sym]=max(peakprice[sym],float(previous.high))
                    threshold=max(float(previous.low20),peakprice[sym]-2*float(previous.atr))
                    if float(previous.close)<threshold:
                        qty=shares.pop(sym)
                        cash+=qty*p*(1-SIDE_COST)
                        fees+=qty*p*SIDE_COST
                        orders+=1
                        trades.append(float(p*(1-SIDE_COST)/(entry.pop(sym)*(1+SIDE_COST))-1))
                        peakprice.pop(sym)
                else:
                    if float(previous.close)>float(previous.high55):
                        equity=cash+sum(q*px_open[s] for s,q in shares.items())
                        wanted=min(cash,0.05*equity)
                        if wanted>=1e-6:
                            qty=wanted/(p*(1+SIDE_COST))
                            cash-=wanted
                            shares[sym]=qty
                            fees+=qty*p*SIDE_COST
                            orders+=1
                            entry[sym]=p
                            peakprice[sym]=float(previous.high)
                            entry_dates.append(str(d.date()))
        equity=cash+sum(q*px_close[s] for s,q in shares.items())
        if not np.isfinite(equity) or equity<=0:raise RuntimeError("NONPOSITIVE_EQUITY")
        nav.append(equity)
    last=days[-1]
    for sym,qty in shares.items():
        exitprice=float(series[sym].at[last,"close"])
        cash+=qty*exitprice*(1-SIDE_COST)
        fees+=qty*exitprice*SIDE_COST
        trades.append(float(exitprice*(1-SIDE_COST)/(entry[sym]*(1+SIDE_COST))-1))
    nav[-1]=cash
    nav=pd.Series(nav,index=days)
    dfbtc=series["BTCUSDT"].reindex(days)
    if dfbtc.close.isna().any():raise RuntimeError("BTC_MISSING_DAYS")
    btcp=nav.copy()
    btcp.iloc[:]=dfbtc.close.to_numpy()/dfbtc.open.iloc[0]
    return nav,btcp,trades,orders,fees,entry_dates

def perf(portfolio,trades=None):
    r=portfolio.pct_change().fillna(0.).to_numpy()
    if len(r)<2:raise ValueError("NOT_ENOUGH_RETURNS")
    ave=float(r.mean());std=float(r.std(ddof=1))
    downside=float(np.sqrt(np.mean(np.minimum(r,0)**2)))
    peak=np.maximum.accumulate(np.r_[1.0,portfolio.to_numpy()])
    dd=1-np.r_[1.0,portfolio.to_numpy()]/peak
    if trades is None:trades=[]
    winners=[x for x in trades if x>0];losers=[-x for x in trades if x<0]
    return {"Sharpe":(ave/std*np.sqrt(365) if std>1e-14 else None),
            "Sortino":(ave/downside*np.sqrt(365) if downside>1e-14 else None),
            "MaxDD":float(dd.max()),"NetReturn":float(portfolio.iloc[-1]/portfolio.iloc[0]-1),
            "ProfitFactor":(float(sum(winners)/sum(losers)) if losers else None),
            "WinRate":(float(len(winners)/len(trades)) if trades else None),
            "Trades":len(trades)}

def main():
    market={s:download(s) for s in SYMBOLS}
    nav,btc,trades,orders,fees,entries=values_and_trades(market,None)
    train=nav.index<pd.Timestamp(TRAIN_END,tz="UTC")
    # NAV slices independent years; no global retuning.
    a=perf(nav[train],None);b=perf(nav[~train],None)
    aa=perf(btc[train]);bb=perf(btc[~train])
    selected= len(trades)
    # Needed for per-fold deals; engine only emits combined complete trade results.
    # Count explicit entries per calendar split instead of guessing trade ownership.
    train_count=sum(x<TRAIN_END for x in entries)
    val_count=sum(x>=TRAIN_END for x in entries)
    a["Trades"]=train_count;b["Trades"]=val_count
    a["ProfitFactor"]=None;a["WinRate"]=None
    b["ProfitFactor"]=None;b["WinRate"]=None
    verdict="PASS" if b["Sharpe"] is not None and b["Sharpe"]>.6 and b["NetReturn"]>bb["NetReturn"] else "FAIL"
    payload={
      "strategy":"v4 Donchian 55-day breakout, exit 20-day low or 2xATR",
      "historical_top20_verified":False,
      "fixed_universe_survivorship_risk":True,
      "symbol_count":len(SYMBOLS),
      "symbols":list(SYMBOLS),
      "train_2022_2023":a,"validation_2024":b,
      "train_BTC":aa,"validation_BTC":bb,
      "closed_trade_count_total":selected,
      "estimated_fees_slippage_total_usdt_per_unit_nav":fees,
      "orders_total":orders,
      "verdict":verdict,
      "test3_opened":False,
      "limitations":"Fixed 20 historically liquid pairs are NOT independently verified exact top20 by historical market cap. Undefined per-fold PF/WinRate are null, not fabricated."
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(payload,indent=2,allow_nan=False))
    print("V4_TRAIN_SHARPE",a["Sharpe"],"V4_VAL_SHARPE",b["Sharpe"],"VAL_BTC_NET",bb["NetReturn"],"VERDICT",verdict,flush=True)
if __name__=="__main__":main()
